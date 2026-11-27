"""Fixtures for the Notification API tests."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import SOURCE_HASSIO
from homeassistant.core import HomeAssistant

from custom_components.notification_api.const import DOMAIN

from .const import ENTRY_DATA, PING_OK, PING_URL


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let HA load integrations from custom_components/."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a discovered config entry (not yet added to hass)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Notification API",
        unique_id=DOMAIN,
        source=SOURCE_HASSIO,
        data=dict(ENTRY_DATA),
    )


@pytest.fixture
def mock_ping(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Make the add-on answer ping with 200."""
    aioclient_mock.get(PING_URL, json=PING_OK)
    return aioclient_mock


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_ping: None
) -> MockConfigEntry:
    """Set up the integration with a reachable add-on."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
