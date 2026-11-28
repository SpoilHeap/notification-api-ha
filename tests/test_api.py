"""Tests for the Notification API client."""

from __future__ import annotations

from http import HTTPStatus

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.notification_api.api import (
    Channel,
    ChannelsResult,
    Delivery,
    NotificationApiAuthError,
    NotificationApiClient,
    NotificationApiConnectionError,
    NotificationApiError,
    NotificationApiNoDeliveriesError,
    NotificationApiNotFoundError,
    NotificationApiValidationError,
    NotifyResult,
    PingResult,
    ProblemDetails,
    Recipient,
    RecipientsResult,
    WebhookRegistration,
)

from .const import (
    BASE_URL,
    CHANNELS_OK,
    CHANNELS_URL,
    HOST,
    INSTANCE_ID,
    NOTIFICATION_ID,
    NOTIFICATION_OK,
    NOTIFICATION_URL,
    NOTIFY_OK,
    NOTIFY_URL,
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
        (HTTPStatus.BAD_REQUEST, NotificationApiValidationError),
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


async def test_ac36_put_webhook_400_field_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC36: a 400 ProblemDetails is parsed into field errors."""
    aioclient_mock.put(
        WEBHOOK_URL,
        status=HTTPStatus.BAD_REQUEST,
        json={
            "title": "One or more validation errors occurred.",
            "status": 400,
            "errors": {"secret": ["Must decode to 32-512 bytes."]},
        },
        headers={"Content-Type": "application/problem+json"},
    )

    with pytest.raises(NotificationApiValidationError) as exc_info:
        await _client(hass).async_put_webhook("http://homeassistant:8123/x", "c2Vj")

    assert exc_info.value.status == HTTPStatus.BAD_REQUEST
    assert exc_info.value.problem.errors == {
        "secret": ("Must decode to 32-512 bytes.",)
    }
    assert str(exc_info.value) == "HTTP 400: secret: Must decode to 32-512 bytes."


# Send (spec 4.1)


async def test_ac01_notify_minimal_payload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """POST /notify sends only the fields that are set and parses the 202."""
    aioclient_mock.post(NOTIFY_URL, status=HTTPStatus.ACCEPTED, json=NOTIFY_OK)

    result = await _client(hass).async_notify(["contact:alice"], "Hello")

    assert result == NotifyResult(
        notification_id="ntf_01",
        deliveries=(
            Delivery(
                delivery_id="dlv_1",
                contact="alice",
                via="contact:alice",
                channel="telegram",
                status="queued",
            ),
        ),
    )
    assert result.as_dict() == NOTIFY_OK
    method, url, body, headers = aioclient_mock.mock_calls[0]
    assert (method, str(url)) == ("POST", NOTIFY_URL)
    assert body == {"to": ["contact:alice"], "message": "Hello", "priority": "normal"}
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_notify_all_fields(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Every optional field is sent when set; an empty channel list is omitted."""
    aioclient_mock.post(NOTIFY_URL, status=HTTPStatus.ACCEPTED, json=NOTIFY_OK)
    client = _client(hass)

    await client.async_notify(
        ("group:family", "contact:alice"),
        "Door opened",
        title="Front door",
        priority="high",
        channels=("telegram",),
        tag="front_door_alarm",
        source="automation.front_door",
        idempotency_key="6f1c",
    )
    await client.async_notify(["contact:alice"], "Hi", channels=[])

    assert aioclient_mock.mock_calls[0][2] == {
        "to": ["group:family", "contact:alice"],
        "message": "Door opened",
        "title": "Front door",
        "priority": "high",
        "channels": ["telegram"],
        "tag": "front_door_alarm",
        "source": "automation.front_door",
        "idempotency_key": "6f1c",
    }
    assert "channels" not in aioclient_mock.mock_calls[1][2]


async def test_ac12_notify_replay_200_accepted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC12: a repeated idempotency key may return the original response as 200."""
    aioclient_mock.post(NOTIFY_URL, json=NOTIFY_OK)

    result = await _client(hass).async_notify(["contact:alice"], "Hi")

    assert result.notification_id == "ntf_01"


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"status": HTTPStatus.NOT_FOUND}, NotificationApiNotFoundError),
        (
            {"status": HTTPStatus.BAD_REQUEST, "text": "not json"},
            NotificationApiValidationError,
        ),
        (
            {"status": HTTPStatus.BAD_REQUEST, "json": ["x"]},
            NotificationApiValidationError,
        ),
        (
            {
                "status": HTTPStatus.UNPROCESSABLE_ENTITY,
                "json": {"title": "No deliveries"},
            },
            NotificationApiNoDeliveriesError,
        ),
        ({"status": HTTPStatus.UNAUTHORIZED}, NotificationApiAuthError),
        ({"exc": aiohttp.ClientConnectionError()}, NotificationApiConnectionError),
        (
            {"status": HTTPStatus.ACCEPTED, "json": {"deliveries": []}},
            NotificationApiError,
        ),
        (
            {
                "status": HTTPStatus.ACCEPTED,
                "json": {"notification_id": "n", "deliveries": [{}]},
            },
            NotificationApiError,
        ),
        ({"status": HTTPStatus.CONFLICT}, NotificationApiError),
    ],
    ids=[
        "404_addon_without_notify",
        "400_no_problem_body",
        "400_body_not_object",
        "422",
        "401",
        "connection_error",
        "202_missing_id",
        "202_delivery_missing_keys",
        "409_unexpected",
    ],
)
async def test_notify_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
    expected: type[Exception],
) -> None:
    """Each failure maps to the right typed exception."""
    aioclient_mock.post(NOTIFY_URL, **mock_kwargs)

    with pytest.raises(expected) as exc_info:
        await _client(hass).async_notify(["contact:alice"], "Hi")
    assert type(exc_info.value) is expected


def test_problem_details_describe() -> None:
    """Field errors win over detail, which wins over title."""
    assert (
        ProblemDetails(
            title="t",
            detail="d",
            errors={"to": ("Unknown handle contact:bob.",), "priority": ()},
        ).describe()
        == "to: Unknown handle contact:bob.; priority"
    )
    assert ProblemDetails(title="t", detail="d").describe() == "d"
    assert ProblemDetails(title="t").describe() == "t"
    assert ProblemDetails().describe() == "no details"


async def test_ac09_problem_errors_parsing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A string instead of a list, and non-string title/detail, are tolerated."""
    aioclient_mock.post(
        NOTIFY_URL,
        status=HTTPStatus.BAD_REQUEST,
        json={
            "title": 1,
            "detail": None,
            "errors": {"to": "Unknown handle: contact:bob"},
        },
    )

    with pytest.raises(NotificationApiValidationError) as exc_info:
        await _client(hass).async_notify(["contact:bob"], "Hi")

    assert exc_info.value.problem == ProblemDetails(
        errors={"to": ("Unknown handle: contact:bob",)}
    )


# GET /notifications/{id} (spec 4.2, AC41)


async def test_ac41_get_notification(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC41: the body is returned as the add-on sent it."""
    aioclient_mock.get(NOTIFICATION_URL, json=NOTIFICATION_OK)

    assert await _client(hass).async_get_notification(NOTIFICATION_ID) == (
        NOTIFICATION_OK
    )

    [(method, url, _, headers)] = aioclient_mock.mock_calls
    assert method == "GET"
    assert str(url) == NOTIFICATION_URL
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_get_notification_keeps_extra_keys(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Keys a later add-on adds are passed through, not dropped."""
    body = {**NOTIFICATION_OK, "future_field": 1}
    aioclient_mock.get(NOTIFICATION_URL, json=body)

    assert await _client(hass).async_get_notification(NOTIFICATION_ID) == body


async def test_get_notification_encodes_the_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The id stays one path segment: `/`, `?` and spaces are percent-encoded."""
    # yarl un-escapes `%3D` to `=`, which is harmless inside a path segment.
    url = f"{BASE_URL}/notifications/..%2Fping%3Fx%3D1%20y"
    aioclient_mock.get(url, json=NOTIFICATION_OK)

    await _client(hass).async_get_notification("../ping?x=1 y")

    [(_, called, _, _)] = aioclient_mock.mock_calls
    assert called.raw_path == "/api/v1/notifications/..%2Fping%3Fx=1%20y"


@pytest.mark.parametrize("notification_id", [".", ".."])
async def test_get_notification_dot_segments_are_not_sent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, notification_id: str
) -> None:
    """`.` and `..` would be normalised into another path, so they are a 404."""
    with pytest.raises(NotificationApiNotFoundError):
        await _client(hass).async_get_notification(notification_id)
    assert not aioclient_mock.mock_calls


def _without(body: dict[str, object], key: str) -> dict[str, object]:
    return {k: v for k, v in body.items() if k != key}


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"status": HTTPStatus.NOT_FOUND}, NotificationApiNotFoundError),
        ({"status": HTTPStatus.UNAUTHORIZED}, NotificationApiAuthError),
        ({"status": HTTPStatus.INTERNAL_SERVER_ERROR}, NotificationApiConnectionError),
        ({"exc": TimeoutError()}, NotificationApiConnectionError),
        ({"text": "not json"}, NotificationApiError),
        ({"json": _without(NOTIFICATION_OK, "tag")}, NotificationApiError),
        ({"json": _without(NOTIFICATION_OK, "deliveries")}, NotificationApiError),
        ({"json": {**NOTIFICATION_OK, "deliveries": {}}}, NotificationApiError),
        ({"json": {**NOTIFICATION_OK, "deliveries": ["x"]}}, NotificationApiError),
        (
            {
                "json": {
                    **NOTIFICATION_OK,
                    "deliveries": [
                        _without(NOTIFICATION_OK["deliveries"][0], "attempts")  # type: ignore[index]
                    ],
                }
            },
            NotificationApiError,
        ),
    ],
    ids=[
        "404_unknown_or_old_addon",
        "401",
        "500",
        "timeout",
        "not_json",
        "missing_tag",
        "missing_deliveries",
        "deliveries_not_a_list",
        "delivery_not_an_object",
        "delivery_missing_keys",
    ],
)
async def test_get_notification_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
    expected: type[Exception],
) -> None:
    """Each failure maps to the right typed exception."""
    aioclient_mock.get(NOTIFICATION_URL, **mock_kwargs)

    with pytest.raises(expected) as exc_info:
        await _client(hass).async_get_notification(NOTIFICATION_ID)
    assert type(exc_info.value) is expected


# GET /channels (spec 4.2, AC42)


async def test_ac42_channels(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC42: both channels are parsed, and looked up by name."""
    aioclient_mock.get(CHANNELS_URL, json=CHANNELS_OK)

    result = await _client(hass).async_get_channels()

    assert result == ChannelsResult(
        channels=(
            Channel(name="telegram", status="not_configured"),
            Channel(name="sms", status="configured"),
        )
    )
    assert result.get("sms") == Channel(name="sms", status="configured")
    assert result.get("sms").configured  # type: ignore[union-attr]
    assert not result.get("telegram").configured  # type: ignore[union-attr]
    assert result.get("email") is None
    [(_, url, _, headers)] = aioclient_mock.mock_calls
    assert str(url) == CHANNELS_URL
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("mock_kwargs", "expected"),
    [
        ({"status": HTTPStatus.NOT_FOUND}, NotificationApiNotFoundError),
        ({"status": HTTPStatus.UNAUTHORIZED}, NotificationApiAuthError),
        ({"status": HTTPStatus.BAD_GATEWAY}, NotificationApiConnectionError),
        ({"json": {}}, NotificationApiError),
        ({"json": {"channels": ["sms"]}}, NotificationApiError),
        ({"json": {"channels": [{"name": "sms"}]}}, NotificationApiError),
    ],
    ids=[
        "404_addon_before_v070",
        "401",
        "502",
        "missing_list",
        "item_not_object",
        "item_missing_status",
    ],
)
async def test_channels_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
    expected: type[Exception],
) -> None:
    """A 404 (add-on v0.6.0 and older) and malformed bodies map to typed errors."""
    aioclient_mock.get(CHANNELS_URL, **mock_kwargs)

    with pytest.raises(expected) as exc_info:
        await _client(hass).async_get_channels()
    assert type(exc_info.value) is expected
