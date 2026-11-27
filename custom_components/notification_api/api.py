"""Async client for the Notification API add-on (spec section 4).

Covers `/ping`, `/recipients` and `/webhook`. `/notify` and `/notifications/{id}`
will be added here as `async_*` methods; they all go through `_request`, which owns
auth, timeouts and error mapping.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

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
    """


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
class _Response:
    status: HTTPStatus
    # The decoded JSON object for a 200, empty for bodiless statuses (204, 304).
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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        expected: tuple[HTTPStatus, ...] = (HTTPStatus.OK,),
    ) -> _Response:
        """Send a request. A 200 body is decoded and must be a JSON object."""
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
                    if status not in expected:
                        raise NotificationApiError(
                            f"Unexpected HTTP {status} from {method} {path}"
                        )
                    if status == HTTPStatus.OK:
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
