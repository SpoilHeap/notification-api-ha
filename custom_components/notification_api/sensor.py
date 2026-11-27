"""Sensor: the add-on version reported by ping."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NotificationApiConfigEntry
from .entity import NotificationApiEntity

# Coordinator-driven: entities never poll on their own.
PARALLEL_UPDATES = 0

VERSION = SensorEntityDescription(
    key="version",
    translation_key="version",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotificationApiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor."""
    async_add_entities(
        [NotificationApiVersionSensor(entry.runtime_data.coordinator, VERSION)]
    )


class NotificationApiVersionSensor(NotificationApiEntity, SensorEntity):
    """The add-on version. Unavailable while the add-on can't be reached."""

    @property
    def native_value(self) -> str:
        """Return the add-on version."""
        return self.coordinator.data.version
