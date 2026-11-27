"""Diagnostics for the Notification API integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET
from .coordinator import NotificationApiConfigEntry

# The webhook id is redacted too: it is the only credential in the webhook URL.
TO_REDACT = {CONF_TOKEN, CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET}


def _coordinator(coordinator: DataUpdateCoordinator[Any]) -> dict[str, Any]:
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": repr(coordinator.last_exception)
        if coordinator.last_exception
        else None,
        "data": asdict(coordinator.data) if coordinator.data else None,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with the secrets redacted."""
    data = entry.runtime_data
    return {
        "entry": {
            "title": entry.title,
            "source": entry.source,
            "unique_id": entry.unique_id,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "coordinator": _coordinator(data.coordinator),
        "recipients_coordinator": _coordinator(data.recipients_coordinator),
        "webhook_registered": data.webhook_registered,
    }
