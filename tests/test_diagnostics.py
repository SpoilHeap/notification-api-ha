"""Tests for diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant

from .const import HOST, TOKEN, VERSION


async def test_diagnostics_redacts_token(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """The API token never appears in diagnostics."""
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    assert result["entry"]["data"]["token"] == "**REDACTED**"
    assert result["entry"]["data"]["host"] == HOST
    assert result["coordinator"] == {
        "last_update_success": True,
        "last_exception": None,
        "data": {"ok": True, "version": VERSION},
    }
    assert TOKEN not in str(result)
