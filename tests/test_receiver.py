"""Tests for the webhook receiver: signatures, replay window, dedupe, dispatch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from http import HTTPStatus
import json
from typing import Any
from unittest.mock import patch

from aiohttp import ClientResponse
from aiohttp.test_utils import TestClient
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant

from custom_components.notification_api.const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    EVENT_DELIVERY_FAILED,
    EVENT_DELIVERY_SENT,
    EVENT_WEBHOOK_TEST,
)
from custom_components.notification_api.receiver import compute_signature

from .common import calls, mock_addon
from .const import ENTRY_DATA, RECIPIENTS_URL

# Known-answer vector (computed here, cross-checked with `openssl dgst -hmac`):
# the key is the raw bytes 0x00..0x1f, sent to the add-on as standard base64.
VECTOR_SECRET_B64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
VECTOR_KEY = bytes(range(32))
VECTOR_TIMESTAMP = "1795796120"  # 2026-11-27T16:15:20Z
VECTOR_BODY = (
    b'{"id":"evt_test_0001","type":"webhook.test",'
    b'"occurred_at":"2026-11-27T16:15:20Z","data":{}}'
)
VECTOR_SIGNATURE = (
    "sha256=51e8fd165211ea54ae0a0993391d96791f82a347df714ce5f82d3a627b971b94"
)

NOW = "2026-11-27T16:15:20Z"
NOW_TS = int(VECTOR_TIMESTAMP)
WEBHOOK_ID = "0123456789abcdef" * 4
WEBHOOK_PATH = f"/api/webhook/{WEBHOOK_ID}"

DELIVERY_DATA = {
    "notification_id": "ntf_01",
    "delivery_id": "dlv_1",
    "contact": "alice",
    "via": "group:family",
    "channel": "telegram",
    "tag": "front_door_alarm",
    "source": "automation.front_door",
}

type Post = Callable[..., Awaitable[ClientResponse]]


def _envelope(
    event_type: str = "delivery.sent",
    event_id: str = "evt_01",
    data: dict[str, Any] | None = None,
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "occurred_at": NOW,
            "data": DELIVERY_DATA if data is None else data,
        }
    ).encode()


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return an entry whose webhook id and secret (the vector key) are fixed."""
    return MockConfigEntry(
        domain="notification_api",
        title="Notification API",
        unique_id="notification_api",
        source="hassio",
        data={
            **ENTRY_DATA,
            CONF_WEBHOOK_ID: WEBHOOK_ID,
            CONF_WEBHOOK_SECRET: VECTOR_SECRET_B64,
        },
    )


@pytest.fixture
async def client(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
    freezer: FrozenDateTimeFactory,
) -> TestClient:
    """Return an HTTP client for HA, with the clock frozen at the vector time."""
    freezer.move_to(NOW)
    return await hass_client_no_auth()


@pytest.fixture
def post(client: TestClient) -> Post:
    """POST a body to the webhook, signed with the vector key unless overridden."""

    async def _post(
        body: bytes,
        *,
        timestamp: str | None = VECTOR_TIMESTAMP,
        signature: str | None = None,
        key: bytes = VECTOR_KEY,
    ) -> ClientResponse:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if timestamp is not None:
            headers["X-Notification-Api-Timestamp"] = timestamp
            if signature is None:
                signature = compute_signature(key, timestamp, body)
        if signature is not None:
            headers["X-Notification-Api-Signature"] = signature
        return await client.post(WEBHOOK_PATH, data=body, headers=headers)

    return _post


def test_ac37_known_answer_vector() -> None:
    """AC37: the signature matches the fixed vector, byte for byte."""
    assert compute_signature(VECTOR_KEY, VECTOR_TIMESTAMP, VECTOR_BODY) == (
        VECTOR_SIGNATURE
    )


async def test_ac37_known_answer_vector_accepted(
    hass: HomeAssistant, post: Post
) -> None:
    """AC37: the vector, POSTed as-is, is accepted and fires webhook_test."""
    events = async_capture_events(hass, EVENT_WEBHOOK_TEST)

    response = await post(VECTOR_BODY, signature=VECTOR_SIGNATURE)
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert [e.data for e in events] == [
        {"event_id": "evt_test_0001", "occurred_at": NOW}
    ]


# Signature (AC33, AC37)


@pytest.mark.parametrize(
    ("timestamp", "signature"),
    [
        (None, None),
        (VECTOR_TIMESTAMP, ""),
        (None, VECTOR_SIGNATURE),
        ("", VECTOR_SIGNATURE),
        ("1795796120.5", VECTOR_SIGNATURE),
        ("-1795796120", VECTOR_SIGNATURE),
        ("١٧٩٥٧٩٦١٢٠", VECTOR_SIGNATURE),  # Arabic-Indic digits
    ],
    ids=[
        "no_headers",
        "empty_signature",
        "no_timestamp",
        "empty_timestamp",
        "fractional_timestamp",
        "negative_timestamp",
        "non_ascii_digits",
    ],
)
async def test_ac33_missing_or_malformed_headers_rejected(
    hass: HomeAssistant,
    post: Post,
    timestamp: str | None,
    signature: str | None,
) -> None:
    """AC33: no valid signature headers means 401 and no event."""
    events = async_capture_events(hass, EVENT_DELIVERY_SENT)

    response = await post(_envelope(), timestamp=timestamp, signature=signature)

    assert response.status == HTTPStatus.UNAUTHORIZED
    assert not events


@pytest.mark.parametrize(
    "signature",
    [
        "sha256=" + "0" * 64,
        "sha256=" + "51E8FD165211EA54AE0A0993391D96791F82A347DF714CE5F82D3A627B971B94",
        "51e8fd165211ea54ae0a0993391d96791f82a347df714ce5f82d3a627b971b94",
        "sha1=51e8fd165211ea54ae0a0993391d96791f82a347df714ce5f82d3a627b971b94",
    ],
    ids=["wrong", "uppercase_hex", "no_prefix", "wrong_prefix"],
)
async def test_ac37_bad_signature_rejected(
    hass: HomeAssistant, post: Post, signature: str
) -> None:
    """AC37: anything but the exact lowercase `sha256=<hex>` is rejected."""
    events = async_capture_events(hass, EVENT_WEBHOOK_TEST)

    response = await post(VECTOR_BODY, signature=signature)

    assert response.status == HTTPStatus.UNAUTHORIZED
    assert not events


async def test_ac37_base64_text_is_not_the_key(hass: HomeAssistant, post: Post) -> None:
    """AC37: the HMAC key is the raw 32 bytes, not the base64 string."""
    response = await post(_envelope(), key=VECTOR_SECRET_B64.encode())

    assert response.status == HTTPStatus.UNAUTHORIZED


async def test_ac37_tampered_body_rejected(hass: HomeAssistant, post: Post) -> None:
    """AC37: the signature covers the raw bytes; any change breaks it."""
    events = async_capture_events(hass, EVENT_WEBHOOK_TEST)
    # Semantically identical JSON with different bytes is also a tamper.
    tampered = VECTOR_BODY.replace(b'"data":{}', b'"data": {}')

    response = await post(tampered, signature=VECTOR_SIGNATURE)

    assert response.status == HTTPStatus.UNAUTHORIZED
    assert not events


async def test_ac37_signature_over_timestamp(hass: HomeAssistant, post: Post) -> None:
    """AC37: the timestamp is signed; changing it invalidates the signature."""
    response = await post(
        VECTOR_BODY, timestamp=str(NOW_TS - 1), signature=VECTOR_SIGNATURE
    )

    assert response.status == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize(
    ("offset", "status"),
    [
        (-300, HTTPStatus.OK),
        (300, HTTPStatus.OK),
        (-301, HTTPStatus.UNAUTHORIZED),
        (301, HTTPStatus.UNAUTHORIZED),
        (-86400, HTTPStatus.UNAUTHORIZED),
    ],
    ids=["300s_old", "300s_ahead", "301s_old", "301s_ahead", "a_day_old"],
)
async def test_ac37_timestamp_window(
    hass: HomeAssistant, post: Post, offset: int, status: HTTPStatus
) -> None:
    """AC37: exactly 300 s off is accepted; 301 s is a replay (401)."""
    events = async_capture_events(hass, EVENT_DELIVERY_SENT)

    response = await post(_envelope(), timestamp=str(NOW_TS + offset))
    await hass.async_block_till_done()

    assert response.status == status
    assert len(events) == (1 if status == HTTPStatus.OK else 0)


async def test_ac37_duplicate_event_id_ignored(hass: HomeAssistant, post: Post) -> None:
    """AC37: a repeated event id gets 200 and does nothing."""
    events = async_capture_events(hass, EVENT_DELIVERY_SENT)

    first = await post(_envelope(event_id="evt_dup"))
    second = await post(_envelope(event_id="evt_dup"))
    await hass.async_block_till_done()

    assert first.status == second.status == HTTPStatus.OK
    assert len(events) == 1


async def test_dedupe_is_bounded(hass: HomeAssistant, post: Post) -> None:
    """Only the most recent ids are remembered; the oldest are forgotten."""
    events = async_capture_events(hass, EVENT_DELIVERY_SENT)

    with patch("custom_components.notification_api.receiver.DEDUPE_SIZE", 2):
        for event_id in ("a", "b", "c", "a", "c"):
            assert (await post(_envelope(event_id=event_id))).status == HTTPStatus.OK
    await hass.async_block_till_done()

    # "a" was evicted by "c", so it fires again; "c" is still remembered.
    assert [e.data["event_id"] for e in events] == ["a", "b", "c", "a"]


async def test_rejected_call_does_not_consume_the_id(
    hass: HomeAssistant, post: Post
) -> None:
    """A forged call can't block the genuine event by using its id first."""
    events = async_capture_events(hass, EVENT_DELIVERY_SENT)

    forged = await post(_envelope(event_id="evt_x"), signature="sha256=" + "0" * 64)
    genuine = await post(_envelope(event_id="evt_x"))
    await hass.async_block_till_done()

    assert forged.status == HTTPStatus.UNAUTHORIZED
    assert genuine.status == HTTPStatus.OK
    assert len(events) == 1


# Envelope validation


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"",
        b"[1, 2]",
        json.dumps({"type": "delivery.sent", "occurred_at": NOW, "data": {}}).encode(),
        json.dumps({"id": "", "type": "x", "occurred_at": NOW, "data": {}}).encode(),
        json.dumps({"id": "e", "occurred_at": NOW, "data": {}}).encode(),
        json.dumps({"id": "e", "type": "x", "data": {}}).encode(),
        json.dumps({"id": "e", "type": "x", "occurred_at": NOW}).encode(),
        json.dumps({"id": "e", "type": "x", "occurred_at": NOW, "data": []}).encode(),
        json.dumps({"id": 1, "type": "x", "occurred_at": NOW, "data": {}}).encode(),
    ],
    ids=[
        "not_json",
        "empty",
        "not_an_object",
        "no_id",
        "empty_id",
        "no_type",
        "no_occurred_at",
        "no_data",
        "data_not_object",
        "id_not_string",
    ],
)
async def test_invalid_envelope_is_400(post: Post, body: bytes) -> None:
    """A correctly signed but invalid body is a 400."""
    assert (await post(body)).status == HTTPStatus.BAD_REQUEST


async def test_only_post_is_allowed(client: TestClient) -> None:
    """GET (and other methods) are refused by HA's webhook component."""
    response = await client.get(WEBHOOK_PATH)

    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED


# Dispatch (AC30, AC31, AC32, AC95)


async def test_ac30_delivery_sent_fires_event(hass: HomeAssistant, post: Post) -> None:
    """AC30: delivery.sent fires notification_api_delivery_sent with the data."""
    events = async_capture_events(hass, EVENT_DELIVERY_SENT)

    response = await post(_envelope("delivery.sent", "evt_sent"))
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert [e.data for e in events] == [
        {**DELIVERY_DATA, "event_id": "evt_sent", "occurred_at": NOW}
    ]


async def test_ac95_delivery_failed_fires_event(
    hass: HomeAssistant, post: Post
) -> None:
    """AC95: delivery.failed fires notification_api_delivery_failed."""
    failed = async_capture_events(hass, EVENT_DELIVERY_FAILED)
    sent = async_capture_events(hass, EVENT_DELIVERY_SENT)
    data = {**DELIVERY_DATA, "error": "Telegram: chat not found"}

    response = await post(_envelope("delivery.failed", "evt_failed", data))
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert [e.data for e in failed] == [
        {**data, "event_id": "evt_failed", "occurred_at": NOW}
    ]
    assert not sent


async def test_webhook_test_fires_event(hass: HomeAssistant, post: Post) -> None:
    """webhook.test (the add-on's "Send test event" button) fires an HA event."""
    events = async_capture_events(hass, EVENT_WEBHOOK_TEST)

    response = await post(_envelope("webhook.test", "evt_test", {}))
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert [e.data for e in events] == [{"event_id": "evt_test", "occurred_at": NOW}]


async def test_envelope_fields_win_over_data(hass: HomeAssistant, post: Post) -> None:
    """`event_id` and `occurred_at` always come from the envelope."""
    events = async_capture_events(hass, EVENT_DELIVERY_SENT)

    await post(_envelope(data={"event_id": "spoofed", "occurred_at": "never"}))
    await hass.async_block_till_done()

    assert events[0].data == {"event_id": "evt_01", "occurred_at": NOW}


async def test_ac32_recipients_changed_refreshes_now(
    hass: HomeAssistant, post: Post, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC32/AC45: recipients.changed refreshes the list without waiting 30 s."""
    kids = {"handle": "group:kids", "name": "Kids", "type": "group"}
    mock_addon(aioclient_mock, recipients={"revision": "18", "recipients": [kids]})

    response = await post(_envelope("recipients.changed", "evt_rc", {"revision": "18"}))
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert len(calls(aioclient_mock, "GET", RECIPIENTS_URL)) == 1
    state = hass.states.get("sensor.notification_api_recipients")
    assert state.state == "1"
    assert state.attributes["handles"] == ["group:kids"]


async def test_unknown_type_is_ignored(
    hass: HomeAssistant,
    post: Post,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown (e.g. future v2) types get 200 and do nothing."""
    caplog.set_level("DEBUG")
    events = async_capture_events(hass, "*")
    aioclient_mock.clear_requests()

    response = await post(_envelope("reply.received", "evt_v2", {}))
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert not [e for e in events if e.event_type.startswith("notification_api")]
    assert not aioclient_mock.mock_calls
    assert "Ignoring webhook event evt_v2 of type reply.received" in caplog.text
