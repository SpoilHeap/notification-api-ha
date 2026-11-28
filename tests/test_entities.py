"""Tests for the entities and their device."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_ICON,
    STATE_OFF,
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


async def test_channel_sensors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """One diagnostic binary sensor per channel, on the service device."""
    connected_entry = entity_registry.async_get(
        "binary_sensor.notification_api_connected"
    )
    assert connected_entry is not None
    for entity_id, key, name, state in (
        (
            "binary_sensor.notification_api_sms_configured",
            "sms_configured",
            "Notification API SMS configured",
            STATE_ON,
        ),
        (
            "binary_sensor.notification_api_telegram_configured",
            "telegram_configured",
            "Notification API Telegram configured",
            STATE_OFF,
        ),
    ):
        sensor = hass.states.get(entity_id)
        assert sensor is not None
        assert sensor.state == state
        assert sensor.attributes[ATTR_FRIENDLY_NAME] == name
        # No device class: the state translations say Configured / Not configured.
        assert ATTR_DEVICE_CLASS not in sensor.attributes
        # Icons come from icons.json, not the state attributes.
        assert ATTR_ICON not in sensor.attributes
        reg_entry = entity_registry.async_get(entity_id)
        assert reg_entry is not None
        assert reg_entry.unique_id == f"{init_integration.entry_id}_{key}"
        assert reg_entry.translation_key == key
        assert reg_entry.entity_category is EntityCategory.DIAGNOSTIC
        assert reg_entry.device_id == connected_entry.device_id
