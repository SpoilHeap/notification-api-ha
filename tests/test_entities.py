"""Tests for the entities and their device."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    STATE_ON,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.notification_api.const import DOMAIN

from .const import SLUG, VERSION


async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Connected, Add-on version and Recipients, under one service device."""
    connected = hass.states.get("binary_sensor.notification_api_connected")
    assert connected is not None
    assert connected.state == STATE_ON
    assert connected.attributes[ATTR_DEVICE_CLASS] == "connectivity"
    assert connected.attributes[ATTR_FRIENDLY_NAME] == "Notification API Connected"

    version = hass.states.get("sensor.notification_api_add_on_version")
    assert version is not None
    assert version.state == VERSION
    assert version.attributes[ATTR_FRIENDLY_NAME] == "Notification API Add-on version"

    recipients = hass.states.get("sensor.notification_api_recipients")
    assert recipients is not None
    assert recipients.state == "2"
    assert recipients.attributes[ATTR_FRIENDLY_NAME] == "Notification API Recipients"
    assert recipients.attributes["handles"] == ["contact:alice", "group:family"]

    entry_id = init_integration.entry_id
    connected_entry = entity_registry.async_get(connected.entity_id)
    version_entry = entity_registry.async_get(version.entity_id)
    assert connected_entry is not None
    assert version_entry is not None
    assert connected_entry.unique_id == f"{entry_id}_connected"
    assert version_entry.unique_id == f"{entry_id}_version"
    assert connected_entry.translation_key == "connected"
    assert version_entry.translation_key == "version"
    assert connected_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert connected_entry.device_id == version_entry.device_id
    recipients_entry = entity_registry.async_get(recipients.entity_id)
    assert recipients_entry is not None
    assert recipients_entry.unique_id == f"{entry_id}_recipients"
    assert recipients_entry.translation_key == "recipients"
    assert recipients_entry.entity_category is None
    assert recipients_entry.device_id == connected_entry.device_id

    device = device_registry.async_get(connected_entry.device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, entry_id)}
    assert device.name == "Notification API"
    assert device.entry_type is dr.DeviceEntryType.SERVICE
    assert device.sw_version == VERSION
    assert device.configuration_url == f"homeassistant://app/{SLUG}"
