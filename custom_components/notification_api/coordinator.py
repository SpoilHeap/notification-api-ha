"""Coordinator that polls the add-on's ping endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    NotificationApiAuthError,
    NotificationApiClient,
    NotificationApiError,
    PingResult,
)
from .const import DOMAIN, LOGGER, UPDATE_INTERVAL


@dataclass(slots=True)
class NotificationApiData:
    """Runtime data stored on the config entry."""

    client: NotificationApiClient
    coordinator: NotificationApiCoordinator


type NotificationApiConfigEntry = ConfigEntry[NotificationApiData]


class NotificationApiCoordinator(DataUpdateCoordinator[PingResult]):
    """Polls `GET /api/v1/ping` to track connectivity and the add-on version."""

    config_entry: NotificationApiConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: NotificationApiConfigEntry,
        client: NotificationApiClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client

    @override
    async def _async_update_data(self) -> PingResult:
        """Ping the add-on."""
        try:
            result = await self.client.async_ping()
        except NotificationApiAuthError as err:
            # Starts the reauth flow (token rotated or the add-on was reinstalled).
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except NotificationApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        if self.data is not None and self.data.version != result.version:
            self._async_update_device_version(result.version)
        return result

    def _async_update_device_version(self, version: str) -> None:
        """Keep the service device's software version current after an add-on update."""
        registry = dr.async_get(self.hass)
        # The entry owns exactly one (service) device.
        for device in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            registry.async_update_device(device.id, sw_version=version)
