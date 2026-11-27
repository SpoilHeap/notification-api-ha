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
    NotificationApiNotFoundError,
    PingResult,
    Recipient,
    RecipientsResult,
    WebhookRegistration,
)

from .const import (
    HOST,
    INSTANCE_ID,
    PING_OK,
    PING_URL,
    PING_V020,
    PORT,
    RECIPIENTS_OK,
    RECIPIENTS_URL,
    REVISION,
    TOKEN,
    VERSION,
    WEBHOOK_URL,
)


def _client(hass: HomeAssistant) -> NotificationApiClient:
    return NotificationApiClient(async_get_clientsession(hass), HOST, PORT, TOKEN)


async def test_ping_sends_bearer_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Ping returns the version and instance id, authenticating with the token."""
    aioclient_mock.get(PING_URL, json=PING_OK)

    assert await _client(hass).async_ping() == PingResult(
        ok=True, version=VERSION, instance_id=INSTANCE_ID
    )

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
        ({"status": HTTPStatus.NOT_FOUND}, NotificationApiNotFoundError),
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


async def test_ac44_ping_without_instance_id_is_tolerated(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC44 compatibility: add-on v0.2.0 has no instance_id."""
    aioclient_mock.get(PING_URL, json=PING_V020)

    assert await _client(hass).async_ping() == PingResult(
        ok=True, version="0.2.0", instance_id=None
    )


async def test_ac40_recipients(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC40: the first request is unconditional and returns the parsed list."""
    aioclient_mock.get(RECIPIENTS_URL, json=RECIPIENTS_OK)

    result = await _client(hass).async_get_recipients()

    assert result == RecipientsResult(
        revision=REVISION,
        recipients=(
            Recipient(handle="contact:alice", name="Alice", type="contact"),
            Recipient(handle="group:family", name="Family", type="group"),
        ),
    )
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert "If-None-Match" not in headers
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_ac43_recipients_not_modified(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC43: with a known revision the request is conditional; 304 means None."""
    aioclient_mock.get(RECIPIENTS_URL, status=HTTPStatus.NOT_MODIFIED)

    assert await _client(hass).async_get_recipients(REVISION) is None

    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["If-None-Match"] == f'"{REVISION}"'


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"status": HTTPStatus.NOT_FOUND}, NotificationApiNotFoundError),
        ({"json": {"revision": "1"}}, NotificationApiError),
        ({"json": {"revision": "1", "recipients": ["x"]}}, NotificationApiError),
        ({"json": {"revision": "1", "recipients": [{}]}}, NotificationApiError),
    ],
    ids=["404_addon_v020", "missing_list", "item_not_object", "item_missing_keys"],
)
async def test_recipients_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
    expected: type[Exception],
) -> None:
    """A 404 (add-on v0.2.0) and malformed bodies map to typed exceptions."""
    aioclient_mock.get(RECIPIENTS_URL, **mock_kwargs)

    with pytest.raises(expected) as exc_info:
        await _client(hass).async_get_recipients()
    assert type(exc_info.value) is expected


async def test_ac35_put_webhook(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC35: PUT /webhook sends the URL and secret and expects 204."""
    aioclient_mock.put(WEBHOOK_URL, status=HTTPStatus.NO_CONTENT)

    await _client(hass).async_put_webhook("http://homeassistant:8123/x", "c2VjcmV0")

    method, url, body, headers = aioclient_mock.mock_calls[0]
    assert method == "PUT"
    assert str(url) == WEBHOOK_URL
    assert body == {"url": "http://homeassistant:8123/x", "secret": "c2VjcmV0"}
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (HTTPStatus.NOT_FOUND, NotificationApiNotFoundError),
        (HTTPStatus.BAD_REQUEST, NotificationApiError),
        (HTTPStatus.OK, NotificationApiError),
        (HTTPStatus.UNAUTHORIZED, NotificationApiAuthError),
    ],
    ids=["404_addon_v020", "400_validation", "200_not_204", "401"],
)
async def test_put_webhook_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: HTTPStatus,
    expected: type[Exception],
) -> None:
    """Anything but 204 is an error, with 404 distinguishable."""
    aioclient_mock.put(WEBHOOK_URL, status=status, json={})

    with pytest.raises(expected) as exc_info:
        await _client(hass).async_put_webhook("http://homeassistant:8123/x", "c2Vj")
    assert type(exc_info.value) is expected


async def test_ac35_get_webhook(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC35: GET /webhook returns the registration, or None on 404."""
    aioclient_mock.get(
        WEBHOOK_URL, json={"url": "http://x/y", "registered_at": "2026-11-27T16:00:00Z"}
    )
    assert await _client(hass).async_get_webhook() == WebhookRegistration(
        url="http://x/y", registered_at="2026-11-27T16:00:00Z"
    )

    aioclient_mock.clear_requests()
    aioclient_mock.get(WEBHOOK_URL, status=HTTPStatus.NOT_FOUND)
    assert await _client(hass).async_get_webhook() is None

    aioclient_mock.clear_requests()
    aioclient_mock.get(WEBHOOK_URL, json={"url": "http://x/y"})
    with pytest.raises(NotificationApiError):
        await _client(hass).async_get_webhook()


async def test_ac35_delete_webhook(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC35: DELETE /webhook expects 204."""
    aioclient_mock.delete(WEBHOOK_URL, status=HTTPStatus.NO_CONTENT)

    await _client(hass).async_delete_webhook()

    method, url, _, _ = aioclient_mock.mock_calls[0]
    assert (method, str(url)) == ("DELETE", WEBHOOK_URL)
