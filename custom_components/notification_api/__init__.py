"""The Notification API integration.

Connects Home Assistant to the Notification API add-on, which delivers Telegram and
SMS notifications to named contacts and groups.
"""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NotificationApiClient
from .coordinator import (
    NotificationApiConfigEntry,
    NotificationApiCoordinator,
    NotificationApiData,
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> bool:
    """Set up Notification API from a config entry."""
    client = NotificationApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    coordinator = NotificationApiCoordinator(hass, entry, client)
    # Raises ConfigEntryAuthFailed (-> reauth) on 401, ConfigEntryNotReady otherwise.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = NotificationApiData(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
