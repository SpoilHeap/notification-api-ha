"""Diagnostics for the Notification API integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import NotificationApiConfigEntry

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with the API token redacted."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": {
            "title": entry.title,
            "source": entry.source,
            "unique_id": entry.unique_id,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "data": asdict(coordinator.data) if coordinator.data else None,
        },
    }
