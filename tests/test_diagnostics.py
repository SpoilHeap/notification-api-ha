"""Tests for diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant

from custom_components.notification_api.const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
)

from .const import CHANNELS_OK, HOST, INSTANCE_ID, RECIPIENTS, REVISION, TOKEN, VERSION

REDACTED = "**REDACTED**"


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """The API token, webhook secret and webhook id never appear in diagnostics."""
    secret = init_integration.data[CONF_WEBHOOK_SECRET]
    webhook_id = init_integration.data[CONF_WEBHOOK_ID]

    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    data = result["entry"]["data"]
    assert data["token"] == REDACTED
    assert data[CONF_WEBHOOK_SECRET] == REDACTED
    assert data[CONF_WEBHOOK_ID] == REDACTED
    assert data["host"] == HOST
    assert data["instance_id"] == INSTANCE_ID
    assert result["coordinator"] == {
        "last_update_success": True,
        "last_exception": None,
        "data": {
            "ping": {"ok": True, "version": VERSION, "instance_id": INSTANCE_ID},
            "channels": CHANNELS_OK,
            "channels_supported": True,
        },
    }
    assert result["recipients_coordinator"] == {
        "last_update_success": True,
        "last_exception": None,
        "data": {"revision": REVISION, "recipients": RECIPIENTS},
    }
    assert result["webhook_registered"] is True
    for value in (TOKEN, secret, webhook_id):
        assert value not in str(result)
