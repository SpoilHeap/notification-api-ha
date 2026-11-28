"""Sensors: the add-on version reported by ping, and the recipients count."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    NotificationApiConfigEntry,
    NotificationApiCoordinator,
    NotificationApiRecipientsCoordinator,
)
from .entity import NotificationApiEntity

# Coordinator-driven: entities never poll on their own.
PARALLEL_UPDATES = 0

VERSION = SensorEntityDescription(
    key="version",
    translation_key="version",
    entity_category=EntityCategory.DIAGNOSTIC,
)
RECIPIENTS = SensorEntityDescription(
    key="recipients",
    translation_key="recipients",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotificationApiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    data = entry.runtime_data
    async_add_entities(
        [
            NotificationApiVersionSensor(data.coordinator, VERSION),
            NotificationApiRecipientsSensor(data.recipients_coordinator, RECIPIENTS),
        ]
    )


class NotificationApiVersionSensor(
    NotificationApiEntity[NotificationApiCoordinator], SensorEntity
):
    """The add-on version. Unavailable while the add-on can't be reached."""

    @property
    def native_value(self) -> str:
        """Return the add-on version."""
        return self.coordinator.data.ping.version


class NotificationApiRecipientsSensor(
    NotificationApiEntity[NotificationApiRecipientsCoordinator], SensorEntity
):
    """How many contacts and groups the add-on has, with their handles.

    Unavailable while `/recipients` fails, including on add-on v0.2.0 (404).
    """

    @property
    def available(self) -> bool:
        """Return whether a recipients list has been loaded and is current."""
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> int:
        """Return the number of recipients."""
        return len(self.coordinator.data.recipients)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the recipient handles, for templates and automations."""
        return {"handles": [r.handle for r in self.coordinator.data.recipients]}
