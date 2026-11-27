"""Tests for setup, unload and the coordinator."""

from __future__ import annotations

from http import HTTPStatus

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.notification_api.const import DOMAIN, UPDATE_INTERVAL

from .const import PING_URL

CONNECTED = "binary_sensor.notification_api_connected"
VERSION_SENSOR = "sensor.notification_api_add_on_version"


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads, exposes runtime data and unloads cleanly."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.runtime_data.coordinator.data.version == "0.1.0"

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    "mock_kwargs",
    [{"exc": TimeoutError()}, {"status": HTTPStatus.INTERNAL_SERVER_ERROR}],
    ids=["timeout", "5xx"],
)
async def test_setup_retry_when_unreachable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
) -> None:
    """An unreachable add-on puts the entry in setup retry (ConfigEntryNotReady)."""
    aioclient_mock.get(PING_URL, **mock_kwargs)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failed_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A 401 during setup starts reauth."""
    aioclient_mock.get(PING_URL, status=HTTPStatus.UNAUTHORIZED)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == mock_config_entry.entry_id


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_coordinator_401_triggers_reauth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A token rejected while running (rotation) starts reauth."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, status=HTTPStatus.UNAUTHORIZED)

    await _tick(hass, freezer)

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert hass.states.get(CONNECTED).state == STATE_OFF


async def test_coordinator_connection_error_and_recovery(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """When the add-on goes away the version is unavailable and Connected is off."""
    assert hass.states.get(CONNECTED).state == STATE_ON

    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, exc=TimeoutError())
    await _tick(hass, freezer)

    assert hass.states.get(CONNECTED).state == STATE_OFF
    assert hass.states.get(VERSION_SENSOR).state == STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress()

    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, json={"ok": True, "version": "0.1.0"})
    await _tick(hass, freezer)

    assert hass.states.get(CONNECTED).state == STATE_ON
    assert hass.states.get(VERSION_SENSOR).state == "0.1.0"


async def test_addon_update_refreshes_device_version(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new add-on version updates the sensor and the device's sw_version."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, json={"ok": True, "version": "0.2.0"})
    await _tick(hass, freezer)

    assert hass.states.get(VERSION_SENSOR).state == "0.2.0"
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert [d.identifiers for d in devices] == [{(DOMAIN, init_integration.entry_id)}]
    assert devices[0].sw_version == "0.2.0"
