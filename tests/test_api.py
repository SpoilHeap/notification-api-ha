"""Tests for the Notification API client."""

from __future__ import annotations

from http import HTTPStatus

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.notification_api.api import (
    NotificationApiAuthError,
    NotificationApiClient,
    NotificationApiConnectionError,
    NotificationApiError,
    PingResult,
)

from .const import HOST, PING_OK, PING_URL, PORT, TOKEN, VERSION


def _client(hass: HomeAssistant) -> NotificationApiClient:
    return NotificationApiClient(async_get_clientsession(hass), HOST, PORT, TOKEN)


async def test_ping_sends_bearer_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Ping returns the version and authenticates with the bearer token."""
    aioclient_mock.get(PING_URL, json=PING_OK)

    assert await _client(hass).async_ping() == PingResult(ok=True, version=VERSION)

    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == PING_URL
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"status": HTTPStatus.UNAUTHORIZED}, NotificationApiAuthError),
        ({"status": HTTPStatus.INTERNAL_SERVER_ERROR}, NotificationApiConnectionError),
        ({"status": HTTPStatus.SERVICE_UNAVAILABLE}, NotificationApiConnectionError),
        ({"exc": TimeoutError()}, NotificationApiConnectionError),
        ({"exc": aiohttp.ClientConnectionError()}, NotificationApiConnectionError),
        ({"status": HTTPStatus.NOT_FOUND}, NotificationApiError),
        ({"status": HTTPStatus.FORBIDDEN}, NotificationApiError),
        ({"text": "<html>not json</html>"}, NotificationApiError),
        ({"json": ["not", "an", "object"]}, NotificationApiError),
        ({"json": {"ok": True}}, NotificationApiError),
    ],
    ids=[
        "401",
        "500",
        "503",
        "timeout",
        "connection_error",
        "404",
        "403",
        "not_json",
        "not_an_object",
        "missing_version",
    ],
)
async def test_ping_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
    expected: type[Exception],
) -> None:
    """Each failure maps to the right typed exception."""
    aioclient_mock.get(PING_URL, **mock_kwargs)

    with pytest.raises(expected) as exc_info:
        await _client(hass).async_ping()
    assert type(exc_info.value) is expected
