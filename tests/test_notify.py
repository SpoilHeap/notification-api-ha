"""Tests for the notify entities: lifecycle (AC92, AC93, AC57) and send_message."""

from __future__ import annotations

import base64
from http import HTTPStatus
import json
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.components.notify import (
    ATTR_MESSAGE,
    ATTR_TITLE,
    DOMAIN as NOTIFY_DOMAIN,
    SERVICE_SEND_MESSAGE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_SUPPORTED_FEATURES,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.notification_api.const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
    RECIPIENTS_UPDATE_INTERVAL,
)
from custom_components.notification_api.receiver import compute_signature

from .common import calls, mock_addon
from .const import NOTIFY_URL, PING_V020, RECIPIENTS, RECIPIENTS_URL

ALICE = "notify.notification_api_contact_alice"
FAMILY = "notify.notification_api_group_family"
KIDS = {"handle": "group:kids", "name": "Kids", "type": "group"}


def _recipients(revision: str, *items: dict[str, str]) -> dict[str, Any]:
    return {"revision": revision, "recipients": list(items)}


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(RECIPIENTS_UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _notify_entities(registry: er.EntityRegistry, entry: MockConfigEntry) -> set[str]:
    return {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == NOTIFY_DOMAIN
    }


# Entity creation (AC92, AC57)


async def test_ac92_one_notify_entity_per_recipient(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """AC92: one notify entity per contact and group, on the service device."""
    assert _notify_entities(entity_registry, init_integration) == {ALICE, FAMILY}

    alice = hass.states.get(ALICE)
    assert alice is not None
    assert alice.state == STATE_UNKNOWN  # Never notified yet.
    assert alice.attributes[ATTR_FRIENDLY_NAME] == "Notification API Alice"
    assert alice.attributes["handle"] == "contact:alice"
    assert alice.attributes["type"] == "contact"
    assert alice.attributes[ATTR_SUPPORTED_FEATURES] == 1  # TITLE
    family = hass.states.get(FAMILY)
    assert family is not None
    assert family.attributes[ATTR_FRIENDLY_NAME] == "Notification API Family"
    assert family.attributes["handle"] == "group:family"
    assert family.attributes["type"] == "group"

    reg_entry = entity_registry.async_get(ALICE)
    assert reg_entry is not None
    assert reg_entry.unique_id == f"{init_integration.entry_id}_contact:alice"
    assert reg_entry.original_name == "Alice"
    assert reg_entry.translation_key == "recipient"
    sensor = entity_registry.async_get("sensor.notification_api_recipients")
    assert sensor is not None
    assert reg_entry.device_id == sensor.device_id


async def test_ac57_contact_and_group_with_the_same_name(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """AC57: `contact:family` and `group:family`, both named Family, don't collide."""
    mock_addon(
        aioclient_mock,
        recipients=_recipients(
            "3",
            {"handle": "contact:family", "name": "Family", "type": "contact"},
            {"handle": "group:family", "name": "Family", "type": "group"},
        ),
    )
    await _setup(hass, mock_config_entry)

    contact = hass.states.get("notify.notification_api_contact_family")
    group = hass.states.get(FAMILY)
    assert contact is not None
    assert group is not None
    assert contact.attributes["handle"] == "contact:family"
    assert group.attributes["handle"] == "group:family"
    assert (
        contact.attributes[ATTR_FRIENDLY_NAME]
        == group.attributes[ATTR_FRIENDLY_NAME]
        == "Notification API Family"
    )


async def test_ac92_no_entities_without_recipients_endpoint(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
) -> None:
    """Add-on v0.2.0 has no `/recipients`: setup works, with no notify entities."""
    mock_addon(
        aioclient_mock,
        ping=PING_V020,
        webhook_status=HTTPStatus.NOT_FOUND,
        recipients_status=HTTPStatus.NOT_FOUND,
    )
    await _setup(hass, mock_config_entry)

    assert not _notify_entities(entity_registry, mock_config_entry)
    assert not hass.states.async_entity_ids(NOTIFY_DOMAIN)


# Lifecycle (AC93)


async def test_ac93_new_recipient_appears_within_one_poll(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """AC93: a group created in the add-on appears on the next 30 s poll."""
    mock_addon(aioclient_mock, recipients=_recipients("18", *RECIPIENTS, KIDS))

    await _poll(hass, freezer)

    kids = hass.states.get("notify.notification_api_group_kids")
    assert kids is not None
    assert kids.attributes[ATTR_FRIENDLY_NAME] == "Notification API Kids"
    assert hass.states.get(ALICE) is not None


async def test_ac93_recipients_changed_adds_immediately(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """AC93: a `recipients.changed` webhook event adds the entity without waiting."""
    mock_addon(aioclient_mock, recipients=_recipients("18", *RECIPIENTS, KIDS))
    client = await hass_client_no_auth()
    body = json.dumps(
        {
            "id": "evt_rc",
            "type": "recipients.changed",
            "occurred_at": dt_util.utcnow().isoformat(),
            "data": {"revision": "18"},
        }
    ).encode()
    timestamp = str(int(dt_util.utcnow().timestamp()))
    key = base64.b64decode(init_integration.data[CONF_WEBHOOK_SECRET])

    response = await client.post(
        f"/api/webhook/{init_integration.data[CONF_WEBHOOK_ID]}",
        data=body,
        headers={
            "X-Notification-Api-Timestamp": timestamp,
            "X-Notification-Api-Signature": compute_signature(key, timestamp, body),
        },
    )
    await hass.async_block_till_done()

    assert response.status == HTTPStatus.OK
    assert len(calls(aioclient_mock, "GET", RECIPIENTS_URL)) == 1
    assert hass.states.get("notify.notification_api_group_kids") is not None


async def test_ac93_rename_updates_the_name(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A renamed recipient keeps its entity id and gets the new name."""
    mock_addon(
        aioclient_mock,
        recipients=_recipients(
            "18", {**RECIPIENTS[0], "name": "Alice Smith"}, RECIPIENTS[1]
        ),
    )

    await _poll(hass, freezer)

    alice = hass.states.get(ALICE)
    assert alice is not None
    assert alice.attributes[ATTR_FRIENDLY_NAME] == "Notification API Alice Smith"
    reg_entry = entity_registry.async_get(ALICE)
    assert reg_entry is not None
    assert reg_entry.original_name == "Alice Smith"
    assert _notify_entities(entity_registry, init_integration) == {ALICE, FAMILY}


async def test_ac93_removed_recipient_loses_entity_and_registry_entry(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A deleted recipient's entity and registry entry are removed."""
    mock_addon(aioclient_mock, recipients=_recipients("18", RECIPIENTS[1]))

    await _poll(hass, freezer)

    assert hass.states.get(ALICE) is None
    assert entity_registry.async_get(ALICE) is None
    assert _notify_entities(entity_registry, init_integration) == {FAMILY}

    # Re-created with the same handle: a new entity with the same entity id.
    mock_addon(aioclient_mock, recipients=_recipients("19", *RECIPIENTS))
    await _poll(hass, freezer)
    assert hass.states.get(ALICE) is not None


async def test_recipient_deleted_while_stopped_is_cleaned_up(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_ping: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
) -> None:
    """A registry entry left from a recipient deleted while HA was off is removed."""
    mock_config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        NOTIFY_DOMAIN,
        DOMAIN,
        f"{mock_config_entry.entry_id}_contact:bob",
        config_entry=mock_config_entry,
        suggested_object_id="notification_api_contact_bob",
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get("notify.notification_api_contact_bob") is None
    assert _notify_entities(entity_registry, mock_config_entry) == {ALICE, FAMILY}


async def test_unavailable_while_recipients_unavailable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failing `/recipients` makes the entities unavailable, not removed."""
    mock_addon(aioclient_mock, recipients_status=HTTPStatus.NOT_FOUND)
    await _poll(hass, freezer)

    assert hass.states.get(ALICE).state == STATE_UNAVAILABLE
    assert hass.states.get(FAMILY).state == STATE_UNAVAILABLE
    assert _notify_entities(entity_registry, init_integration) == {ALICE, FAMILY}

    mock_addon(aioclient_mock, exc=TimeoutError())  # The add-on is down.
    aioclient_mock.clear_requests()
    aioclient_mock.get(RECIPIENTS_URL, exc=TimeoutError())
    await _poll(hass, freezer)
    assert hass.states.get(ALICE).state == STATE_UNAVAILABLE

    mock_addon(aioclient_mock)
    await _poll(hass, freezer)
    assert hass.states.get(ALICE).state == STATE_UNKNOWN


async def test_entities_kept_when_unavailable_at_startup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
) -> None:
    """After a restart with `/recipients` failing, the registry entries are kept."""
    mock_addon(aioclient_mock, recipients_status=HTTPStatus.SERVICE_UNAVAILABLE)

    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert _notify_entities(entity_registry, init_integration) == {ALICE, FAMILY}
    assert hass.states.get(ALICE).state == STATE_UNAVAILABLE


# send_message


async def test_send_message_payload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """`notify.send_message` POSTs the handle, message, title and normal priority."""
    mock_addon(aioclient_mock)
    freezer.move_to("2026-11-27T20:00:00+00:00")

    await hass.services.async_call(
        NOTIFY_DOMAIN,
        SERVICE_SEND_MESSAGE,
        {ATTR_ENTITY_ID: ALICE, ATTR_MESSAGE: "Door opened", ATTR_TITLE: "Front door"},
        blocking=True,
    )

    [(_, _, body, _)] = calls(aioclient_mock, "POST", NOTIFY_URL)
    assert body == {
        "to": ["contact:alice"],
        "message": "Door opened",
        "title": "Front door",
        "priority": "normal",
        "source": "notify.send_message",
    }
    # The notify entity's state is the time of the last notification.
    assert hass.states.get(ALICE).state == "2026-11-27T20:00:00+00:00"


async def test_send_message_without_title_from_automation(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """No title means no `title` field; an automation is named as the source."""
    mock_addon(aioclient_mock)
    context = Context()
    hass.bus.async_fire(
        "automation_triggered",
        {"name": "Front door", ATTR_ENTITY_ID: "automation.front_door"},
        context=context,
    )

    await hass.services.async_call(
        NOTIFY_DOMAIN,
        SERVICE_SEND_MESSAGE,
        {ATTR_ENTITY_ID: FAMILY, ATTR_MESSAGE: "Hi"},
        blocking=True,
        context=context,
    )

    [(_, _, body, _)] = calls(aioclient_mock, "POST", NOTIFY_URL)
    assert body == {
        "to": ["group:family"],
        "message": "Hi",
        "priority": "normal",
        "source": "automation.front_door",
    }


@pytest.mark.parametrize(
    ("notify", "translation_key"),
    [
        ({"status": HTTPStatus.NOT_FOUND}, "send_unsupported"),
        ({"status": HTTPStatus.UNPROCESSABLE_ENTITY}, "send_no_deliveries"),
        ({"exc": TimeoutError()}, "addon_unreachable"),
    ],
    ids=["404_addon_without_notify", "422", "addon_down"],
)
async def test_send_message_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    notify: dict[str, Any],
    translation_key: str,
) -> None:
    """The entity maps errors like the action does, and records no notification."""
    mock_addon(aioclient_mock, notify=notify)

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            NOTIFY_DOMAIN,
            SERVICE_SEND_MESSAGE,
            {ATTR_ENTITY_ID: ALICE, ATTR_MESSAGE: "Hi"},
            blocking=True,
        )

    assert exc_info.value.translation_key == translation_key
    assert hass.states.get(ALICE).state == STATE_UNKNOWN
