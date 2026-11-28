"""Tests for the `notification_api.send` action (AC94) and its error mapping."""

from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
from typing import Any

import aiohttp
import probatio
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import yaml

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import ATTR_ENTITY_ID, EVENT_CALL_SERVICE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.notification_api.const import (
    DOMAIN,
    SERVICE_GET_NOTIFICATION,
    SERVICE_SEND,
)
from custom_components.notification_api.services import (
    GET_NOTIFICATION_SCHEMA,
    SEND_SCHEMA,
    source_from_context,
)

from .common import calls, mock_addon
from .const import (
    BASE_URL,
    NOTIFICATION_ID,
    NOTIFICATION_OK,
    NOTIFICATION_URL,
    NOTIFY_OK,
    NOTIFY_URL,
    PING_OK,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "notification_api"
ALICE = "notify.notification_api_contact_alice"
FAMILY = "notify.notification_api_group_family"


async def _send(
    hass: HomeAssistant,
    data: dict[str, Any],
    *,
    context: Context | None = None,
    return_response: bool = True,
) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND,
        data,
        blocking=True,
        return_response=return_response,
        context=context,
    )


def _sent_body(aioclient_mock: AiohttpClientMocker) -> dict[str, Any]:
    [(_, _, body, _)] = calls(aioclient_mock, "POST", NOTIFY_URL)
    return dict(body)


@pytest.fixture
def addon(
    init_integration: MockConfigEntry, aioclient_mock: AiohttpClientMocker
) -> AiohttpClientMocker:
    """Set up the integration, then reset the recorded calls."""
    return mock_addon(aioclient_mock)


# Targets


async def test_ac92_action_is_registered(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """AC92: the `notification_api.send` action exists after setup."""
    assert hass.services.has_service(DOMAIN, SERVICE_SEND)


async def test_ac94_send_to_handles(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """AC94: handles are sent as-is, in order, and the response is returned."""
    response = await _send(
        hass, {"to": ["group:family", "contact:alice"], "message": "Door opened"}
    )

    assert response == NOTIFY_OK
    assert _sent_body(addon) == {
        "to": ["group:family", "contact:alice"],
        "message": "Door opened",
        "priority": "normal",
        "source": "notification_api.send",
    }


async def test_ac94_send_to_a_single_handle_string(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """A single target can be given as a string."""
    await _send(hass, {"to": "contact:alice", "message": "Hi"})

    assert _sent_body(addon)["to"] == ["contact:alice"]


async def test_ac94_send_to_entity_ids(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """Notify entity ids of this integration resolve to their handles."""
    await _send(hass, {"to": [FAMILY, ALICE], "message": "Hi"})

    assert _sent_body(addon)["to"] == ["group:family", "contact:alice"]


async def test_ac94_send_to_mixed_targets_keeps_duplicates(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """Handles and entity ids mix; duplicates are kept (no deduplication, AC04)."""
    await _send(hass, {"to": [ALICE, "group:family", "contact:alice"], "message": "x"})

    assert _sent_body(addon)["to"] == ["contact:alice", "group:family", "contact:alice"]


async def test_entity_id_survives_a_rename(
    hass: HomeAssistant, addon: AiohttpClientMocker, entity_registry: er.EntityRegistry
) -> None:
    """The handle comes from the unique id, so a user-renamed entity id works."""
    entity_registry.async_update_entity(ALICE, new_entity_id="notify.alice_phone")

    await _send(hass, {"to": ["notify.alice_phone"], "message": "Hi"})

    assert _sent_body(addon)["to"] == ["contact:alice"]


@pytest.mark.parametrize(
    "target",
    [
        "notify.notification_api_contact_bob",
        "sensor.notification_api_recipients",
        "notify.other_integration",
    ],
    ids=["unknown", "not_notify", "other_integration"],
)
async def test_unknown_entity_id(
    hass: HomeAssistant,
    addon: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    target: str,
) -> None:
    """An entity id that isn't one of this entry's notify entities is rejected."""
    entity_registry.async_get_or_create(
        "notify", "other", "x", suggested_object_id="other_integration"
    )

    with pytest.raises(ServiceValidationError) as exc_info:
        await _send(hass, {"to": [ALICE, target], "message": "Hi"})

    assert exc_info.value.translation_key == "unknown_entity"
    assert exc_info.value.translation_placeholders == {"entity_id": target}
    assert not calls(addon, "POST", NOTIFY_URL)


# Fields


async def test_ac94_all_optional_fields(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """Every optional field is passed through."""
    await _send(
        hass,
        {
            "to": ["group:family"],
            "message": "Door opened while the alarm is armed",
            "title": "Front door",
            "priority": "critical",
            "channels": ["telegram", "sms"],
            "tag": "front_door_alarm",
            "idempotency_key": "6f1c",
        },
    )

    assert _sent_body(addon) == {
        "to": ["group:family"],
        "message": "Door opened while the alarm is armed",
        "title": "Front door",
        "priority": "critical",
        "channels": ["telegram", "sms"],
        "tag": "front_door_alarm",
        "idempotency_key": "6f1c",
        "source": "notification_api.send",
    }


async def test_no_response_requested(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """Without `response_variable` the action still sends and returns nothing."""
    assert (
        await _send(
            hass, {"to": ["contact:alice"], "message": "x"}, return_response=False
        )
        is None
    )
    assert len(calls(addon, "POST", NOTIFY_URL)) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"message": "x"},
        {"to": [], "message": "x"},
        {"to": ["contact:alice"]},
        {"to": ["contact:alice"], "message": "x", "priority": "urgent"},
        {"to": ["contact:alice"], "message": "x", "channels": ["email"]},
        {"to": ["contact:alice"], "message": "x", "actions": []},
    ],
    ids=["no_to", "empty_to", "no_message", "bad_priority", "bad_channel", "extra"],
)
async def test_schema_rejects_invalid_data(
    hass: HomeAssistant, addon: AiohttpClientMocker, data: dict[str, Any]
) -> None:
    """Invalid data is refused before anything is sent."""
    with pytest.raises(probatio.Invalid):
        await _send(hass, data)
    assert not calls(addon, "POST", NOTIFY_URL)


@pytest.mark.parametrize(
    ("service", "schema", "required"),
    [
        (SERVICE_SEND, SEND_SCHEMA, {"to", "message"}),
        (SERVICE_GET_NOTIFICATION, GET_NOTIFICATION_SCHEMA, {"notification_id"}),
    ],
)
def test_services_yaml_matches_schema(
    service: str, schema: probatio.Schema, required: set[str]
) -> None:
    """services.yaml and strings.json describe exactly the schema's fields."""
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text("utf-8"))
    strings = json.loads((COMPONENT / "strings.json").read_text("utf-8"))
    icons = json.loads((COMPONENT / "icons.json").read_text("utf-8"))
    schema_fields = {str(key) for key in schema.schema}

    assert set(services) == {SERVICE_SEND, SERVICE_GET_NOTIFICATION}
    assert set(strings["services"]) == set(services)
    assert set(icons["services"]) == set(services)
    assert set(services[service]["fields"]) == schema_fields
    assert set(strings["services"][service]["fields"]) == schema_fields
    assert {
        name
        for name, field in services[service]["fields"].items()
        if field.get("required")
    } == required


# source


async def test_source_from_automation(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """An automation's run names the automation as the source."""
    context = Context()
    hass.bus.async_fire(
        "automation_triggered",
        {"name": "Front door", ATTR_ENTITY_ID: "automation.front_door"},
        context=context,
    )

    await _send(hass, {"to": ["contact:alice"], "message": "x"}, context=context)

    assert _sent_body(addon)["source"] == "automation.front_door"


@pytest.mark.parametrize(
    ("event_type", "data", "source"),
    [
        (
            "automation_triggered",
            {ATTR_ENTITY_ID: "automation.front_door"},
            "automation.front_door",
        ),
        ("script_started", {ATTR_ENTITY_ID: "script.goodnight"}, "script.goodnight"),
        (
            EVENT_CALL_SERVICE,
            {"domain": "script", "service": "goodnight", "service_data": {}},
            "script.goodnight",
        ),
        (
            EVENT_CALL_SERVICE,
            {
                "domain": "script",
                "service": "turn_on",
                "service_data": {ATTR_ENTITY_ID: "script.goodnight"},
            },
            "script.goodnight",
        ),
        (
            EVENT_CALL_SERVICE,
            {
                "domain": "script",
                "service": "turn_on",
                "service_data": {ATTR_ENTITY_ID: ["script.goodnight"]},
            },
            "script.goodnight",
        ),
        (
            EVENT_CALL_SERVICE,
            {
                "domain": "script",
                "service": "turn_on",
                "service_data": {ATTR_ENTITY_ID: ["script.a", "script.b"]},
            },
            "default",
        ),
        (
            EVENT_CALL_SERVICE,
            {"domain": "notification_api", "service": "send"},
            "default",
        ),
        ("automation_triggered", {}, "default"),
        ("state_changed", {ATTR_ENTITY_ID: "light.x"}, "default"),
    ],
    ids=[
        "automation",
        "script_started",
        "script_action",
        "script_turn_on",
        "script_turn_on_list",
        "script_turn_on_two",
        "developer_tools",
        "automation_without_entity_id",
        "other_event",
    ],
)
async def test_source_from_context(
    hass: HomeAssistant, event_type: str, data: dict[str, Any], source: str
) -> None:
    """`source` names the automation or script, else falls back to the default."""
    context = Context()
    hass.bus.async_listen(event_type, lambda _: None)
    hass.bus.async_fire(event_type, data, context=context)

    assert source_from_context(context, "default") == source
    assert source_from_context(Context(), "default") == "default"
    assert source_from_context(None, "default") == "default"


# Errors (spec 4.1)


async def test_ac09_400_field_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """AC09/AC10: a 400 is a ServiceValidationError naming the add-on's errors."""
    mock_addon(
        aioclient_mock,
        notify={
            "status": HTTPStatus.BAD_REQUEST,
            "json": {
                "type": "https://tools.ietf.org/html/rfc9110#section-15.5.1",
                "title": "One or more validation errors occurred.",
                "status": 400,
                "errors": {
                    "to": ["Unknown handle: contact:bob."],
                    "idempotency_key": ["Too long."],
                },
            },
            "headers": {"Content-Type": "application/problem+json"},
        },
    )

    with pytest.raises(ServiceValidationError) as exc_info:
        await _send(hass, {"to": ["contact:bob"], "message": "x"})

    assert exc_info.value.translation_key == "send_invalid"
    assert exc_info.value.translation_placeholders == {
        "errors": "to: Unknown handle: contact:bob.; idempotency_key: Too long."
    }
    assert "Unknown handle: contact:bob." in str(exc_info.value)


@pytest.mark.parametrize(
    ("notify", "translation_key"),
    [
        (
            {"status": HTTPStatus.UNPROCESSABLE_ENTITY, "json": {"title": "x"}},
            "send_no_deliveries",
        ),
        ({"exc": aiohttp.ClientConnectionError()}, "addon_unreachable"),
        ({"status": HTTPStatus.BAD_GATEWAY}, "addon_unreachable"),
        ({"status": HTTPStatus.NOT_FOUND}, "send_unsupported"),
        ({"status": HTTPStatus.CONFLICT}, "send_failed"),
    ],
    ids=["422", "connection_error", "5xx", "404_addon_without_notify", "unexpected"],
)
async def test_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    notify: dict[str, Any],
    translation_key: str,
) -> None:
    """Each failure maps to a HomeAssistantError with its own translation."""
    mock_addon(aioclient_mock, notify=notify)

    with pytest.raises(HomeAssistantError) as exc_info:
        await _send(hass, {"to": ["contact:alice"], "message": "x"})

    assert type(exc_info.value) is HomeAssistantError
    assert exc_info.value.translation_key == translation_key
    assert not hass.config_entries.flow.async_progress()


async def test_401_starts_reauth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A rejected token starts reauth and fails the call."""
    mock_addon(aioclient_mock, notify={"status": HTTPStatus.UNAUTHORIZED})

    with pytest.raises(HomeAssistantError) as exc_info:
        await _send(hass, {"to": ["contact:alice"], "message": "x"})
    await hass.async_block_till_done()

    assert exc_info.value.translation_key == "auth_failed"
    [flow] = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == SOURCE_REAUTH
    assert flow["context"]["entry_id"] == init_integration.entry_id


async def test_no_loaded_entry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """With the entry unloaded the action stays registered and refuses to run."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    mock_addon(aioclient_mock)

    with pytest.raises(ServiceValidationError) as exc_info:
        await _send(hass, {"to": ["contact:alice"], "message": "x"})

    assert exc_info.value.translation_key == "not_loaded"
    assert not calls(aioclient_mock, "POST", NOTIFY_URL)


# notification_api.get_notification (spec 4.2, 11; AC41)


async def _get_notification(hass: HomeAssistant, data: dict[str, Any]) -> Any:
    return await hass.services.async_call(
        DOMAIN, SERVICE_GET_NOTIFICATION, data, blocking=True, return_response=True
    )


async def test_ac41_get_notification(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """AC41: the add-on's body is the response, as-is (snake_case)."""
    assert hass.services.has_service(DOMAIN, SERVICE_GET_NOTIFICATION)

    response = await _get_notification(hass, {"notification_id": NOTIFICATION_ID})

    assert response == NOTIFICATION_OK
    assert len(calls(addon, "GET", NOTIFICATION_URL)) == 1


async def test_get_notification_encodes_the_id(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """An id with a `/` stays one path segment."""
    url = f"{BASE_URL}/notifications/ntf_a%2Fb"
    addon.get(url, json=NOTIFICATION_OK)

    await _get_notification(hass, {"notification_id": "ntf_a/b"})

    assert len(calls(addon, "GET", url)) == 1


async def test_get_notification_requires_return_response(
    hass: HomeAssistant, addon: AiohttpClientMocker
) -> None:
    """Response-only: calling it without asking for the response is refused."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_NOTIFICATION,
            {"notification_id": NOTIFICATION_ID},
            blocking=True,
        )
    assert not calls(addon, "GET", NOTIFICATION_URL)


@pytest.mark.parametrize(
    "data",
    [{}, {"notification_id": ""}, {"notification_id": NOTIFICATION_ID, "x": 1}],
    ids=["missing", "empty", "extra"],
)
async def test_get_notification_schema(
    hass: HomeAssistant, addon: AiohttpClientMocker, data: dict[str, Any]
) -> None:
    """Invalid data is refused before anything is requested."""
    with pytest.raises(probatio.Invalid):
        await _get_notification(hass, data)
    assert not calls(addon, "GET", NOTIFICATION_URL)


async def test_get_notification_unknown_id(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An unknown, malformed or purged id (404) is a translated validation error."""
    mock_addon(
        aioclient_mock,
        notification={
            "status": HTTPStatus.NOT_FOUND,
            "json": {"title": "Not Found", "status": 404},
            "headers": {"Content-Type": "application/problem+json"},
        },
    )

    with pytest.raises(ServiceValidationError) as exc_info:
        await _get_notification(hass, {"notification_id": NOTIFICATION_ID})

    assert exc_info.value.translation_key == "notification_not_found"
    assert exc_info.value.translation_placeholders == {
        "notification_id": NOTIFICATION_ID
    }
    assert NOTIFICATION_ID in str(exc_info.value)


async def test_get_notification_addon_before_v070(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """When /channels is a 404 too, the add-on predates v0.7.0: say so."""
    mock_addon(
        aioclient_mock,
        ping={**PING_OK, "version": "0.6.0"},
        channels={"status": HTTPStatus.NOT_FOUND},
        notification={"status": HTTPStatus.NOT_FOUND},
    )
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError) as exc_info:
        await _get_notification(hass, {"notification_id": NOTIFICATION_ID})

    assert type(exc_info.value) is HomeAssistantError
    assert exc_info.value.translation_key == "get_notification_unsupported"
    assert "v0.7.0" in str(exc_info.value)


@pytest.mark.parametrize(
    ("notification", "translation_key"),
    [
        ({"exc": aiohttp.ClientConnectionError()}, "addon_unreachable"),
        ({"exc": TimeoutError()}, "addon_unreachable"),
        ({"status": HTTPStatus.SERVICE_UNAVAILABLE}, "addon_unreachable"),
        ({"status": HTTPStatus.BAD_REQUEST, "json": {}}, "get_notification_failed"),
        ({"status": HTTPStatus.CONFLICT}, "get_notification_failed"),
        ({"json": {"notification_id": "x"}}, "get_notification_failed"),
    ],
    ids=["connection_error", "timeout", "5xx", "400", "unexpected", "malformed"],
)
async def test_get_notification_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    notification: dict[str, Any],
    translation_key: str,
) -> None:
    """Other failures map to a HomeAssistantError, as for `send`."""
    mock_addon(aioclient_mock, notification=notification)

    with pytest.raises(HomeAssistantError) as exc_info:
        await _get_notification(hass, {"notification_id": NOTIFICATION_ID})

    assert type(exc_info.value) is HomeAssistantError
    assert exc_info.value.translation_key == translation_key
    assert not hass.config_entries.flow.async_progress()


async def test_get_notification_401_starts_reauth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A rejected token starts reauth and fails the call."""
    mock_addon(aioclient_mock, notification={"status": HTTPStatus.UNAUTHORIZED})

    with pytest.raises(HomeAssistantError) as exc_info:
        await _get_notification(hass, {"notification_id": NOTIFICATION_ID})
    await hass.async_block_till_done()

    assert exc_info.value.translation_key == "auth_failed"
    [flow] = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == SOURCE_REAUTH
    assert flow["context"]["entry_id"] == init_integration.entry_id


async def test_get_notification_no_loaded_entry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """With the entry unloaded the action stays registered and refuses to run."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    mock_addon(aioclient_mock)

    assert hass.services.has_service(DOMAIN, SERVICE_GET_NOTIFICATION)
    with pytest.raises(ServiceValidationError) as exc_info:
        await _get_notification(hass, {"notification_id": NOTIFICATION_ID})

    assert exc_info.value.translation_key == "not_loaded"
    assert not calls(aioclient_mock, "GET", NOTIFICATION_URL)
