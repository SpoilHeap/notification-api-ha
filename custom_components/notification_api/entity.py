"""Base entity for the Notification API integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ADDON_SLUG, DEFAULT_NAME, DOMAIN
from .coordinator import NotificationApiCoordinator


class NotificationApiEntity(CoordinatorEntity[NotificationApiCoordinator]):
    """An entity on the Notification API service device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NotificationApiCoordinator,
        description: EntityDescription,
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        # The config entry's unique_id is the fixed domain (single instance), so the
        # entry_id is what keeps entity and device ids distinct per entry.
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DEFAULT_NAME,
            entry_type=DeviceEntryType.SERVICE,
            sw_version=coordinator.data.version if coordinator.data else None,
        )
        # Known only for discovered entries: the slug includes the repository hash.
        if slug := entry.data.get(CONF_ADDON_SLUG):
            self._attr_device_info["configuration_url"] = (
                f"homeassistant://hassio/ingress/{slug}"
            )
