"""Tests for setup, unload, removal, webhook registration and the coordinators."""

from __future__ import annotations

import base64
from datetime import timedelta
from http import HTTPStatus
import logging
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.http import ApiConfig
from homeassistant.config_entries import SOURCE_IGNORE, SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.notification_api.const import (
    CONF_INSTANCE_ID,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DOMAIN,
    RECIPIENTS_UPDATE_INTERVAL,
    UPDATE_INTERVAL,
)
from custom_components.notification_api.receiver import build_webhook_url

from .common import calls, mock_addon
from .const import (
    INSTANCE_ID,
    NEW_INSTANCE_ID,
    PING_OK,
    PING_URL,
    PING_V020,
    RECIPIENTS,
    RECIPIENTS_URL,
    REVISION,
    WEBHOOK_URL,
)

CONNECTED = "binary_sensor.notification_api_connected"
VERSION_SENSOR = "sensor.notification_api_add_on_version"
RECIPIENTS_SENSOR = "sensor.notification_api_recipients"


def _webhook_registered_in_ha(hass: HomeAssistant, webhook_id: str) -> bool:
    return webhook_id in hass.data.get("webhook", {})


async def _tick(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    interval: timedelta = UPDATE_INTERVAL,
) -> None:
    freezer.tick(interval)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _mock_v020(aioclient_mock: AiohttpClientMocker) -> None:
    """Mock the released add-on v0.2.0: no instance_id, /webhook or /recipients."""
    mock_addon(
        aioclient_mock,
        ping=PING_V020,
        webhook_status=HTTPStatus.NOT_FOUND,
        recipients_status=HTTPStatus.NOT_FOUND,
    )


# Setup and unload


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads, listens on its webhook, and unregisters it on unload."""
    assert init_integration.state is ConfigEntryState.LOADED
    data = init_integration.runtime_data
    assert data.coordinator.data.version == "0.1.0"
    assert data.webhook_registered
    webhook_id = init_integration.data[CONF_WEBHOOK_ID]
    assert _webhook_registered_in_ha(hass, webhook_id)

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert not _webhook_registered_in_ha(hass, webhook_id)


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
    mock_addon(aioclient_mock, **mock_kwargs)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not calls(aioclient_mock, "PUT", WEBHOOK_URL)


async def test_setup_auth_failed_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A 401 during setup starts reauth."""
    mock_addon(aioclient_mock, status=HTTPStatus.UNAUTHORIZED)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == mock_config_entry.entry_id


# Webhook registration (AC35)


async def test_ac35_setup_registers_webhook(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """AC35: setup creates the webhook id and secret and registers them."""
    webhook_id = init_integration.data[CONF_WEBHOOK_ID]
    secret = init_integration.data[CONF_WEBHOOK_SECRET]
    assert len(base64.b64decode(secret, validate=True)) == 32
    assert init_integration.data[CONF_INSTANCE_ID] == INSTANCE_ID

    [(_, _, body, _)] = calls(aioclient_mock, "PUT", WEBHOOK_URL)
    assert body == {
        "url": f"http://homeassistant:8123/api/webhook/{webhook_id}",
        "secret": secret,
    }


async def test_ac35_reload_registers_again_with_same_secret(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Every setup PUTs again (idempotent), keeping the id and secret stable."""
    before = dict(init_integration.data)
    mock_addon(aioclient_mock)

    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    [(_, _, body, _)] = calls(aioclient_mock, "PUT", WEBHOOK_URL)
    assert body["secret"] == before[CONF_WEBHOOK_SECRET]
    assert init_integration.data[CONF_WEBHOOK_ID] == before[CONF_WEBHOOK_ID]
    assert init_integration.data[CONF_WEBHOOK_SECRET] == before[CONF_WEBHOOK_SECRET]


async def test_webhook_url_follows_http_config(hass: HomeAssistant) -> None:
    """The URL uses Core's own port and scheme, on the Supervisor hostname."""
    with patch.object(
        hass.config, "api", ApiConfig("172.30.32.1", "172.30.32.1", 8443, True)
    ):
        assert build_webhook_url(hass, "abc") == (
            "https://homeassistant:8443/api/webhook/abc"
        )
    with patch.object(
        hass.config, "api", ApiConfig("172.30.32.1", "172.30.32.1", 8124, False)
    ):
        assert build_webhook_url(hass, "abc") == (
            "http://homeassistant:8124/api/webhook/abc"
        )
    with patch.object(hass.config, "api", None):
        assert build_webhook_url(hass, "abc") == (
            "http://homeassistant:8123/api/webhook/abc"
        )


async def test_webhook_put_connection_error_retries_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A failed PUT after a good ping is transient: retry setup, stop listening."""
    mock_addon(aioclient_mock, webhook_status=HTTPStatus.SERVICE_UNAVAILABLE)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not _webhook_registered_in_ha(hass, mock_config_entry.data[CONF_WEBHOOK_ID])


async def test_webhook_put_401_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A token rejected by PUT /webhook starts reauth."""
    mock_addon(aioclient_mock, webhook_status=HTTPStatus.UNAUTHORIZED)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_webhook_put_rejected_continues_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 400 (validation) is logged; retrying wouldn't help, so setup continues."""
    mock_addon(aioclient_mock, webhook_status=HTTPStatus.BAD_REQUEST)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not mock_config_entry.runtime_data.webhook_registered
    assert "rejected the webhook registration" in caplog.text


# Compatibility with add-on v0.2.0


async def test_compat_addon_v020(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No instance_id, no /webhook, no /recipients: setup still succeeds."""
    _mock_v020(aioclient_mock)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert CONF_INSTANCE_ID not in mock_config_entry.data
    assert not mock_config_entry.runtime_data.webhook_registered
    assert hass.states.get(CONNECTED).state == STATE_ON
    assert hass.states.get(VERSION_SENSOR).state == "0.2.0"
    assert hass.states.get(RECIPIENTS_SENSOR).state == STATE_UNAVAILABLE
    assert caplog.text.count("doesn't support webhooks yet") == 1

    # Recipients keep polling (and failing) without disturbing connectivity.
    await _tick(hass, freezer, RECIPIENTS_UPDATE_INTERVAL)
    assert len(calls(aioclient_mock, "GET", RECIPIENTS_URL)) == 2
    assert hass.states.get(CONNECTED).state == STATE_ON
    assert hass.states.get(RECIPIENTS_SENSOR).state == STATE_UNAVAILABLE

    # The warning is logged once per run; a reload retries the PUT quietly.
    caplog.clear()
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert len(calls(aioclient_mock, "PUT", WEBHOOK_URL)) == 2
    assert "doesn't support webhooks yet" not in caplog.text


async def test_compat_addon_updated_from_v020_registers(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """When the add-on is updated past v0.2.0, the entry reloads and registers."""
    _mock_v020(aioclient_mock)
    await _setup(hass, mock_config_entry)
    assert not mock_config_entry.runtime_data.webhook_registered

    mock_addon(aioclient_mock)  # The updated add-on, with a new version.
    await _tick(hass, freezer)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.webhook_registered
    assert len(calls(aioclient_mock, "PUT", WEBHOOK_URL)) == 1
    assert mock_config_entry.data[CONF_INSTANCE_ID] == INSTANCE_ID
    assert hass.states.get(RECIPIENTS_SENSOR).state == str(len(RECIPIENTS))


# instance_id (AC44)


async def test_ac44_instance_id_change_reregisters_and_reloads(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC44: a new instance_id (fresh database) reloads and re-registers."""
    secret = init_integration.data[CONF_WEBHOOK_SECRET]
    caplog.set_level(logging.INFO)
    mock_addon(aioclient_mock, ping={**PING_OK, "instance_id": NEW_INSTANCE_ID})

    await _tick(hass, freezer)

    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.data[CONF_INSTANCE_ID] == NEW_INSTANCE_ID
    [(_, _, body, _)] = calls(aioclient_mock, "PUT", WEBHOOK_URL)
    assert body["secret"] == secret
    assert "reinstalled with a fresh database" in caplog.text


async def test_ac44_instance_id_changed_while_stopped(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A change seen during setup is stored; that setup registers, no reload."""
    caplog.set_level(logging.INFO)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_INSTANCE_ID: NEW_INSTANCE_ID},
    )
    mock_addon(aioclient_mock)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.data[CONF_INSTANCE_ID] == INSTANCE_ID
    assert len(calls(aioclient_mock, "PUT", WEBHOOK_URL)) == 1
    assert len(calls(aioclient_mock, "GET", PING_URL)) == 1
    assert "reinstalled with a fresh database" in caplog.text


async def test_ac44_same_instance_id_no_reload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """AC44: the same instance_id (restart or update) changes nothing."""
    mock_addon(aioclient_mock)
    await _tick(hass, freezer)

    assert not calls(aioclient_mock, "PUT", WEBHOOK_URL)


# Ping coordinator


async def test_coordinator_401_triggers_reauth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A token rejected while running (rotation) starts reauth."""
    mock_addon(aioclient_mock, status=HTTPStatus.UNAUTHORIZED)

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

    mock_addon(aioclient_mock, exc=TimeoutError())
    await _tick(hass, freezer)

    assert hass.states.get(CONNECTED).state == STATE_OFF
    assert hass.states.get(VERSION_SENSOR).state == STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress()

    mock_addon(aioclient_mock)
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
    """A new add-on version updates the sensor and the device's sw_version.

    The webhook is already registered, so there is no reload.
    """
    mock_addon(aioclient_mock, ping={**PING_OK, "version": "0.2.1"})
    await _tick(hass, freezer)

    assert hass.states.get(VERSION_SENSOR).state == "0.2.1"
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert [d.identifiers for d in devices] == [{(DOMAIN, init_integration.entry_id)}]
    assert devices[0].sw_version == "0.2.1"
    assert not calls(aioclient_mock, "PUT", WEBHOOK_URL)


# Recipients coordinator (AC43, AC93)


async def test_ac43_recipients_304_keeps_cached_data(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """AC43: polling sends If-None-Match; a 304 keeps the data, writes nothing."""
    before = hass.states.get(RECIPIENTS_SENSOR)
    assert before.state == "2"
    mock_addon(aioclient_mock, recipients_status=HTTPStatus.NOT_MODIFIED)

    await _tick(hass, freezer, RECIPIENTS_UPDATE_INTERVAL)

    [(_, _, _, headers)] = calls(aioclient_mock, "GET", RECIPIENTS_URL)
    assert headers["If-None-Match"] == f'"{REVISION}"'
    after = hass.states.get(RECIPIENTS_SENSOR)
    assert after.state == "2"
    assert after.last_reported == before.last_reported
    coordinator = init_integration.runtime_data.recipients_coordinator
    assert coordinator.last_update_success
    assert coordinator.data.revision == REVISION


async def test_ac93_recipients_change_is_picked_up_within_30s(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """AC93: a new group shows up on the next 30 s poll, without a restart."""
    kids = {"handle": "group:kids", "name": "Kids", "type": "group"}
    mock_addon(
        aioclient_mock, recipients={"revision": "18", "recipients": [*RECIPIENTS, kids]}
    )

    await _tick(hass, freezer, RECIPIENTS_UPDATE_INTERVAL)

    state = hass.states.get(RECIPIENTS_SENSOR)
    assert state.state == "3"
    assert state.attributes["handles"] == [
        "contact:alice",
        "group:family",
        "group:kids",
    ]


async def test_recipients_401_starts_reauth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A token rejected by /recipients starts reauth too."""
    mock_addon(aioclient_mock, recipients_status=HTTPStatus.UNAUTHORIZED)

    await _tick(hass, freezer, RECIPIENTS_UPDATE_INTERVAL)

    [flow] = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_recipients_error_leaves_connectivity_alone(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failing /recipients only makes the Recipients sensor unavailable."""
    mock_addon(aioclient_mock, recipients_status=HTTPStatus.INTERNAL_SERVER_ERROR)

    await _tick(hass, freezer, RECIPIENTS_UPDATE_INTERVAL)

    assert hass.states.get(RECIPIENTS_SENSOR).state == STATE_UNAVAILABLE
    assert hass.states.get(CONNECTED).state == STATE_ON


async def test_recipients_304_without_revision_is_an_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A 304 to an unconditional request is treated as a failed update."""
    mock_addon(aioclient_mock, recipients_status=HTTPStatus.NOT_MODIFIED)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(RECIPIENTS_SENSOR).state == STATE_UNAVAILABLE


# Removal


async def test_remove_entry_deletes_webhook(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Removing the entry calls DELETE /webhook."""
    mock_addon(aioclient_mock)

    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert len(calls(aioclient_mock, "DELETE", WEBHOOK_URL)) == 1
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("mock_kwargs", "log"),
    [
        ({"exc": TimeoutError()}, "Could not remove the webhook"),
        ({"status": HTTPStatus.UNAUTHORIZED}, "Could not remove the webhook"),
        ({"status": HTTPStatus.NOT_FOUND}, "nothing to remove"),
    ],
    ids=["addon_down", "401", "404_addon_v020"],
)
async def test_remove_entry_never_blocked(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    *,
    mock_kwargs: dict[str, object],
    log: str,
) -> None:
    """Removal succeeds even when the add-on is down or old."""
    caplog.set_level(logging.DEBUG)
    aioclient_mock.clear_requests()
    aioclient_mock.delete(WEBHOOK_URL, **mock_kwargs)

    await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(DOMAIN)
    assert log in caplog.text


async def test_remove_entry_unexpected_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even a bug in the client doesn't block removal."""
    with patch(
        "custom_components.notification_api.NotificationApiClient.async_delete_webhook",
        side_effect=RuntimeError("boom"),
    ):
        await hass.config_entries.async_remove(init_integration.entry_id)
        await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(DOMAIN)
    assert "Unexpected error removing the webhook" in caplog.text


async def test_remove_ignored_entry_calls_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An ignored discovery has no token; HA doesn't call async_remove_entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, source=SOURCE_IGNORE, data={}
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert not aioclient_mock.mock_calls
