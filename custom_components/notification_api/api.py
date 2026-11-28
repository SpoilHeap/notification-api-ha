"""Async client for the Notification API add-on (spec section 4).

Covers `/ping`, `/recipients`, `/webhook`, `/notify`, `/notifications/{id}` and
`/channels`. Every `async_*` method goes through `_request`, which owns auth,
timeouts and error mapping.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import REQUEST_TIMEOUT

API_PREFIX = "/api/v1"


class NotificationApiError(Exception):
    """Base error for the Notification API client (unexpected response)."""


class NotificationApiConnectionError(NotificationApiError):
    """The add-on could not be reached, timed out, or returned a 5xx."""


class NotificationApiAuthError(NotificationApiError):
    """The add-on rejected the API token (HTTP 401)."""


class NotificationApiNotFoundError(NotificationApiError):
    """The endpoint or resource does not exist (HTTP 404).

    For `/webhook` and `/recipients` this means the add-on predates them (v0.2.0).
    For `/notify` it means the add-on predates sending (v0.3.0 and older), and for
    `/channels` that it predates v0.7.0. For `/notifications/{id}` the id is unknown,
    malformed or purged, or the add-on predates v0.7.0.
    """


@dataclass(frozen=True, slots=True)
class ProblemDetails:
    """The parts of an RFC 9457 `ProblemDetails` body that the integration uses."""

    title: str | None = None
    detail: str | None = None
    # Field errors of a 400, keyed by the request's (snake_case) JSON field name.
    errors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def describe(self) -> str:
        """Return a one-line summary: the field errors, else the detail or title."""
        if self.errors:
            return "; ".join(
                f"{name}: {' '.join(messages)}" if messages else name
                for name, messages in self.errors.items()
            )
        return self.detail or self.title or "no details"


class NotificationApiProblemError(NotificationApiError):
    """The add-on refused the request with a `ProblemDetails` body (4xx)."""

    def __init__(self, status: HTTPStatus, problem: ProblemDetails) -> None:
        """Keep the status and the parsed problem."""
        super().__init__(f"HTTP {status}: {problem.describe()}")
        self.status = status
        self.problem = problem


class NotificationApiValidationError(NotificationApiProblemError):
    """HTTP 400: invalid payload or unknown handles (AC09, AC10, AC36)."""


class NotificationApiNoDeliveriesError(NotificationApiProblemError):
    """HTTP 422 from `/notify`: no recipient has a channel to deliver on (AC08)."""


@dataclass(frozen=True, slots=True)
class PingResult:
    """Response of `GET /api/v1/ping`."""

    ok: bool
    version: str
    # None for add-on versions that predate it (v0.2.0).
    instance_id: str | None = None


@dataclass(frozen=True, slots=True)
class Recipient:
    """One contact or group, by its immutable handle (no PII)."""

    handle: str
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class RecipientsResult:
    """Response of `GET /api/v1/recipients`. Comparable, so a 304 changes nothing."""

    revision: str
    recipients: tuple[Recipient, ...]


@dataclass(frozen=True, slots=True)
class WebhookRegistration:
    """Response of `GET /api/v1/webhook` (the secret is never returned)."""

    url: str
    registered_at: str


@dataclass(frozen=True, slots=True)
class Delivery:
    """One queued delivery of a notification (spec 4.1)."""

    delivery_id: str
    contact: str
    via: str
    channel: str
    status: str


@dataclass(frozen=True, slots=True)
class NotifyResult:
    """Response of `POST /api/v1/notify` (202 Accepted)."""

    notification_id: str
    deliveries: tuple[Delivery, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return the response as the add-on sent it (snake_case), JSON-ready."""
        return {
            "notification_id": self.notification_id,
            "deliveries": [
                {
                    "delivery_id": d.delivery_id,
                    "contact": d.contact,
                    "via": d.via,
                    "channel": d.channel,
                    "status": d.status,
                }
                for d in self.deliveries
            ],
        }


CHANNEL_CONFIGURED = "configured"


@dataclass(frozen=True, slots=True)
class Channel:
    """One channel and whether it can send (spec 4.2)."""

    name: str
    # `configured` or `not_configured`.
    status: str

    @property
    def configured(self) -> bool:
        """Return whether the channel has its credentials saved in the add-on."""
        return self.status == CHANNEL_CONFIGURED


@dataclass(frozen=True, slots=True)
class ChannelsResult:
    """Response of `GET /api/v1/channels`."""

    channels: tuple[Channel, ...]

    def get(self, name: str) -> Channel | None:
        """Return the named channel, or None if the add-on didn't report it."""
        return next((c for c in self.channels if c.name == name), None)


# Keys `GET /notifications/{id}` always returns (spec 4.2).
_NOTIFICATION_KEYS = ("notification_id", "priority", "tag", "source", "created_at")
_NOTIFICATION_DELIVERY_KEYS = (
    "delivery_id",
    "contact",
    "via",
    "channel",
    "status",
    "attempts",
    "last_error",
    "next_attempt_at",
    "last_attempt_at",
    "completed_at",
)


@dataclass(frozen=True, slots=True)
class _Response:
    status: HTTPStatus
    # The decoded JSON object, empty for bodiless statuses (204, 304).
    body: dict[str, Any]


class NotificationApiClient:
    """Small typed client for the add-on's `/api/v1`."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, port: int, token: str
    ) -> None:
        """Initialise the client. The session is HA's shared aiohttp session."""
        self._session = session
        self._base_url = f"http://{host}:{port}{API_PREFIX}"
        self._token = token

    async def async_ping(self) -> PingResult:
        """Check the token and return the add-on version and instance id."""
        body = (await self._request("GET", "/ping")).body
        try:
            instance_id = body.get("instance_id")
            return PingResult(
                ok=bool(body["ok"]),
                version=str(body["version"]),
                instance_id=str(instance_id) if instance_id else None,
            )
        except (KeyError, TypeError) as err:
            raise NotificationApiError(f"Unexpected ping response: {body!r}") from err

    async def async_get_recipients(
        self, revision: str | None = None
    ) -> RecipientsResult | None:
        """Return every contact and group, or None if `revision` is still current.

        With a known revision the request is conditional (`If-None-Match`), and the
        add-on answers 304 when nothing changed (AC43).
        """
        headers = {"If-None-Match": f'"{revision}"'} if revision is not None else {}
        response = await self._request(
            "GET",
            "/recipients",
            headers=headers,
            expected=(HTTPStatus.OK, HTTPStatus.NOT_MODIFIED),
        )
        if response.status == HTTPStatus.NOT_MODIFIED:
            return None
        body = response.body
        try:
            return RecipientsResult(
                revision=str(body["revision"]),
                recipients=tuple(
                    Recipient(
                        handle=str(item["handle"]),
                        name=str(item["name"]),
                        type=str(item["type"]),
                    )
                    for item in body["recipients"]
                ),
            )
        except (KeyError, TypeError) as err:
            raise NotificationApiError(
                f"Unexpected recipients response: {body!r}"
            ) from err

    async def async_get_webhook(self) -> WebhookRegistration | None:
        """Return the current webhook registration, or None if there is none."""
        try:
            body = (await self._request("GET", "/webhook")).body
        except NotificationApiNotFoundError:
            return None
        try:
            return WebhookRegistration(
                url=str(body["url"]), registered_at=str(body["registered_at"])
            )
        except KeyError as err:
            raise NotificationApiError(
                f"Unexpected webhook response: {body!r}"
            ) from err

    async def async_put_webhook(self, url: str, secret: str) -> None:
        """Register (or replace) the webhook. `secret` is standard base64."""
        await self._request(
            "PUT",
            "/webhook",
            json={"url": url, "secret": secret},
            expected=(HTTPStatus.NO_CONTENT,),
        )

    async def async_delete_webhook(self) -> None:
        """Remove the webhook registration. Idempotent on the add-on side."""
        await self._request("DELETE", "/webhook", expected=(HTTPStatus.NO_CONTENT,))

    async def async_notify(
        self,
        to: Sequence[str],
        message: str,
        *,
        title: str | None = None,
        priority: str = "normal",
        channels: Sequence[str] | None = None,
        tag: str | None = None,
        source: str | None = None,
        idempotency_key: str | None = None,
    ) -> NotifyResult:
        """Send a notification (spec 4.1). Optional fields left as None are omitted.

        Raises `NotificationApiValidationError` (400), `NotificationApiNoDeliveriesError`
        (422) and `NotificationApiNotFoundError` (404: the add-on predates sending).
        """
        payload: dict[str, Any] = {
            "to": list(to),
            "message": message,
            "priority": priority,
        }
        optional: dict[str, Any] = {
            "title": title,
            "channels": list(channels) if channels else None,
            "tag": tag,
            "source": source,
            "idempotency_key": idempotency_key,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        # 202 for a new notification. A repeated idempotency key returns "the
        # original response" (AC12); 200 is accepted too, in case that is its status.
        body = (
            await self._request(
                "POST",
                "/notify",
                json=payload,
                expected=(HTTPStatus.ACCEPTED, HTTPStatus.OK),
            )
        ).body
        try:
            return NotifyResult(
                notification_id=str(body["notification_id"]),
                deliveries=tuple(
                    Delivery(
                        delivery_id=str(item["delivery_id"]),
                        contact=str(item["contact"]),
                        via=str(item["via"]),
                        channel=str(item["channel"]),
                        status=str(item["status"]),
                    )
                    for item in body["deliveries"]
                ),
            )
        except (KeyError, TypeError) as err:
            raise NotificationApiError(f"Unexpected notify response: {body!r}") from err

    async def async_get_notification(self, notification_id: str) -> dict[str, Any]:
        """Return a notification and the current state of its deliveries (spec 4.2).

        The body is returned as the add-on sent it (snake_case), after checking it
        has every documented key. Raises `NotificationApiNotFoundError` (404) for an
        unknown, malformed or purged id, and on add-ons older than v0.7.0.
        """
        # The id is user input: encode everything, `/` included, so it stays one
        # path segment. `.` and `..` can't be encoded (yarl normalises them away,
        # escaped or not, and the request would reach another endpoint), and can't
        # be ids either, so they are answered like the add-on's malformed-id 404.
        if notification_id in (".", ".."):
            raise NotificationApiNotFoundError(
                f"Invalid notification id {notification_id!r}"
            )
        path = f"/notifications/{quote(notification_id, safe='')}"
        body = (await self._request("GET", path)).body
        deliveries = body.get("deliveries")
        if (
            any(key not in body for key in _NOTIFICATION_KEYS)
            or not isinstance(deliveries, list)
            or not all(
                isinstance(item, dict)
                and all(key in item for key in _NOTIFICATION_DELIVERY_KEYS)
                for item in deliveries
            )
        ):
            raise NotificationApiError(f"Unexpected notification response: {body!r}")
        return body

    async def async_get_channels(self) -> ChannelsResult:
        """Return whether each channel can send (spec 4.2).

        Raises `NotificationApiNotFoundError` (404) on add-ons older than v0.7.0.
        """
        body = (await self._request("GET", "/channels")).body
        try:
            return ChannelsResult(
                channels=tuple(
                    Channel(name=str(item["name"]), status=str(item["status"]))
                    for item in body["channels"]
                )
            )
        except (KeyError, TypeError) as err:
            raise NotificationApiError(
                f"Unexpected channels response: {body!r}"
            ) from err

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected: tuple[HTTPStatus, ...] = (HTTPStatus.OK,),
    ) -> _Response:
        """Send a request. A response body is decoded and must be a JSON object."""
        url = f"{self._base_url}{path}"
        request_headers = {"Authorization": f"Bearer {self._token}", **(headers or {})}
        body: Any = {}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, url, headers=request_headers, json=json
                ) as response:
                    status = response.status
                    if status == HTTPStatus.UNAUTHORIZED:
                        raise NotificationApiAuthError("The API token was rejected")
                    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                        raise NotificationApiConnectionError(
                            f"The add-on returned HTTP {status}"
                        )
                    if status == HTTPStatus.NOT_FOUND:
                        raise NotificationApiNotFoundError(
                            f"{method} {path} returned HTTP 404"
                        )
                    if status in _PROBLEM_ERRORS:
                        raise _PROBLEM_ERRORS[status](
                            HTTPStatus(status), await _read_problem(response)
                        )
                    if status not in expected:
                        raise NotificationApiError(
                            f"Unexpected HTTP {status} from {method} {path}"
                        )
                    if status not in (HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED):
                        body = await response.json()
        except TimeoutError as err:
            raise NotificationApiConnectionError(
                f"Timed out talking to {self._base_url}"
            ) from err
        except (aiohttp.ContentTypeError, ValueError) as err:
            raise NotificationApiError(f"Non-JSON response from {path}") from err
        except aiohttp.ClientError as err:
            raise NotificationApiConnectionError(
                f"Error talking to {self._base_url}: {err}"
            ) from err
        if not isinstance(body, dict):
            raise NotificationApiError(f"Unexpected response from {path}: {body!r}")
        return _Response(status=HTTPStatus(status), body=body)


_PROBLEM_ERRORS: dict[int, type[NotificationApiProblemError]] = {
    HTTPStatus.BAD_REQUEST: NotificationApiValidationError,
    HTTPStatus.UNPROCESSABLE_ENTITY: NotificationApiNoDeliveriesError,
}


async def _read_problem(response: aiohttp.ClientResponse) -> ProblemDetails:
    """Parse a `ProblemDetails` body, tolerating a missing or malformed one."""
    try:
        # `application/problem+json`, which aiohttp's default check refuses.
        body = await response.json(content_type=None)
    except ValueError:
        return ProblemDetails()
    if not isinstance(body, dict):
        return ProblemDetails()
    errors: dict[str, tuple[str, ...]] = {}
    raw_errors = body.get("errors")
    if isinstance(raw_errors, dict):
        for name, messages in raw_errors.items():
            if isinstance(messages, list):
                errors[str(name)] = tuple(str(m) for m in messages)
            else:
                errors[str(name)] = (str(messages),)
    title = body.get("title")
    detail = body.get("detail")
    return ProblemDetails(
        title=title if isinstance(title, str) else None,
        detail=detail if isinstance(detail, str) else None,
        errors=errors,
    )
