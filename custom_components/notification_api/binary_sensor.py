"""Binary sensor: is the add-on reachable with a valid token."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NotificationApiConfigEntry
from .entity import NotificationApiEntity

# Coordinator-driven: entities never poll on their own.
PARALLEL_UPDATES = 0

CONNECTED = BinarySensorEntityDescription(
    key="connected",
    translation_key="connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotificationApiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor."""
    async_add_entities(
        [NotificationApiConnectedSensor(entry.runtime_data.coordinator, CONNECTED)]
    )


class NotificationApiConnectedSensor(NotificationApiEntity, BinarySensorEntity):
    """On while the last ping succeeded.

    Unlike the other entities this one stays available when the add-on is down,
    so it reads "Disconnected" instead of "Unavailable" and can drive automations.
    """

    @property
    def available(self) -> bool:
        """Always available: being unreachable is the state it reports."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the last ping succeeded."""
        return self.coordinator.last_update_success
