"""Async client for the Notification API add-on (spec section 4).

Only `/api/v1/ping` exists in the add-on today. `/recipients`, `/notify` and
`/notifications/{id}` will be added here as `async_*` methods once the add-on ships
them; they all go through `_request`, which owns auth, timeouts and error mapping.
"""

from __future__ import annotations

import asyncio
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


@dataclass(frozen=True, slots=True)
class PingResult:
    """Response of `GET /api/v1/ping`."""

    ok: bool
    version: str


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
        """Check the token and return the add-on version."""
        body = await self._request("GET", "/ping")
        try:
            return PingResult(ok=bool(body["ok"]), version=str(body["version"]))
        except (KeyError, TypeError) as err:
            raise NotificationApiError(f"Unexpected ping response: {body!r}") from err

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        """Send a request and return the decoded JSON object."""
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, url, headers=headers
                ) as response:
                    if response.status == HTTPStatus.UNAUTHORIZED:
                        raise NotificationApiAuthError("The API token was rejected")
                    if response.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                        raise NotificationApiConnectionError(
                            f"The add-on returned HTTP {response.status}"
                        )
                    if response.status != HTTPStatus.OK:
                        raise NotificationApiError(
                            f"Unexpected HTTP {response.status} from {path}"
                        )
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
        return body
