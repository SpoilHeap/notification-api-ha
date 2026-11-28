"""Binary sensors: is the add-on reachable, and which channels can send."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Channel
from .coordinator import NotificationApiConfigEntry, NotificationApiCoordinator
from .entity import NotificationApiEntity

# Coordinator-driven: entities never poll on their own.
PARALLEL_UPDATES = 0

CONNECTED = BinarySensorEntityDescription(
    key="connected",
    translation_key="connected",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


@dataclass(frozen=True, kw_only=True)
class ChannelBinarySensorEntityDescription(BinarySensorEntityDescription):
    """A channel's "configured" sensor, fed by `GET /channels` (spec 4.2)."""

    channel: str


CHANNEL_SENSORS = (
    ChannelBinarySensorEntityDescription(
        key="sms_configured",
        translation_key="sms_configured",
        entity_category=EntityCategory.DIAGNOSTIC,
        channel="sms",
    ),
    ChannelBinarySensorEntityDescription(
        key="telegram_configured",
        translation_key="telegram_configured",
        entity_category=EntityCategory.DIAGNOSTIC,
        channel="telegram",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotificationApiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            NotificationApiConnectedSensor(coordinator, CONNECTED),
            *(
                NotificationApiChannelSensor(coordinator, description)
                for description in CHANNEL_SENSORS
            ),
        ]
    )


class NotificationApiConnectedSensor(
    NotificationApiEntity[NotificationApiCoordinator], BinarySensorEntity
):
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


class NotificationApiChannelSensor(
    NotificationApiEntity[NotificationApiCoordinator], BinarySensorEntity
):
    """On while the channel's credentials are saved in the add-on.

    Says nothing about whether they are valid: a bad key shows up as failed
    deliveries. Unavailable while the add-on is down, while `/channels` fails, and
    on add-ons older than v0.7.0 (404).
    """

    entity_description: ChannelBinarySensorEntityDescription

    @property
    def available(self) -> bool:
        """Return whether the last poll reported this channel."""
        return super().available and self._channel is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the channel is configured."""
        channel = self._channel
        return channel.configured if channel is not None else None

    @property
    def _channel(self) -> Channel | None:
        data = self.coordinator.data
        if data is None or data.channels is None:
            return None
        return data.channels.get(self.entity_description.channel)
