"""Tests for the Notification API config flow."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import (
    SOURCE_HASSIO,
    SOURCE_IGNORE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from custom_components.notification_api.const import CONF_ADDON_SLUG, DOMAIN

from .const import (
    ENTRY_DATA,
    HOST,
    NEW_TOKEN,
    PING_OK,
    PING_URL,
    PORT,
    SLUG,
    TOKEN,
    UUID,
)

ADDON_NAME = "Notification API"


def _discovery(**config_overrides: Any) -> HassioServiceInfo:
    """Discovery as the Supervisor delivers it (it adds `addon`)."""
    config: dict[str, Any] = {
        CONF_HOST: HOST,
        CONF_PORT: PORT,
        CONF_TOKEN: TOKEN,
        "addon": ADDON_NAME,
    }
    config.update(config_overrides)
    return HassioServiceInfo(config=config, name=ADDON_NAME, slug=SLUG, uuid=UUID)


async def _start_discovery(
    hass: HomeAssistant, info: HassioServiceInfo | None = None
) -> Any:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=info or _discovery()
    )


# Discovery


async def test_ac91_discovery_sets_up_without_manual_token(
    hass: HomeAssistant, mock_ping: AiohttpClientMocker
) -> None:
    """AC91: discovery shows a confirm form, and confirming creates the entry."""
    result = await _start_discovery(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    assert result["description_placeholders"] == {"addon": ADDON_NAME}
    assert result["data_schema"] is None or not result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == ADDON_NAME
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_PORT: PORT,
        CONF_TOKEN: TOKEN,
        CONF_ADDON_SLUG: SLUG,
    }
    assert result["result"].unique_id == DOMAIN
    assert result["result"].state is ConfigEntryState.LOADED
    _, _, _, headers = mock_ping.mock_calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_discovery_bad_token_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A discovered token the add-on rejects can't be fixed in the form: abort."""
    aioclient_mock.get(PING_URL, status=HTTPStatus.UNAUTHORIZED)

    result = await _start_discovery(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "discovery_invalid_auth"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_discovery_cannot_connect_then_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If the add-on is still starting, the user can retry from the same form."""
    aioclient_mock.get(PING_URL, exc=TimeoutError())

    result = await _start_discovery(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, json=PING_OK)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "info",
    [
        HassioServiceInfo(config={CONF_HOST: HOST}, name="x", slug=SLUG, uuid=UUID),
        _discovery(port="not-a-port"),
    ],
    ids=["missing_keys", "bad_port"],
)
async def test_discovery_malformed(
    hass: HomeAssistant, info: HassioServiceInfo
) -> None:
    """Malformed discovery is rejected without contacting the add-on."""
    result = await _start_discovery(hass, info)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_discovery_updates_existing_entry_and_reloads(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """New host/token from discovery update the existing entry; no duplicate."""
    new_url = f"http://new-host:{PORT}/api/v1/ping"
    aioclient_mock.get(new_url, json=PING_OK)

    with patch(
        "custom_components.notification_api.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await _start_discovery(
            hass, _discovery(host="new-host", token=NEW_TOKEN)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert init_integration.data[CONF_HOST] == "new-host"
    assert init_integration.data[CONF_TOKEN] == NEW_TOKEN
    assert len(mock_setup.mock_calls) == 1  # reloaded


async def test_discovery_unchanged_does_not_reload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Re-published discovery with the same data (HA restart) is a no-op."""
    with patch(
        "custom_components.notification_api.async_setup_entry", return_value=True
    ) as mock_setup:
        result = await _start_discovery(hass)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not mock_setup.mock_calls


async def test_discovery_adopts_manual_entry(
    hass: HomeAssistant, mock_ping: AiohttpClientMocker
) -> None:
    """A manual entry is adopted by later discovery (gains the add-on slug)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        source=SOURCE_USER,
        data={CONF_HOST: HOST, CONF_PORT: PORT, CONF_TOKEN: TOKEN},
    )
    entry.add_to_hass(hass)

    result = await _start_discovery(hass)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert entry.data == ENTRY_DATA
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_discovery_respects_ignored_entry(hass: HomeAssistant) -> None:
    """If the user ignored the discovery, it stays ignored and untouched."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, source=SOURCE_IGNORE, data={}
    )
    entry.add_to_hass(hass)

    result = await _start_discovery(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {}


async def test_discovery_fixes_entry_in_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A rotated token arriving via discovery recovers a failed entry and ends reauth."""
    aioclient_mock.get(PING_URL, status=HTTPStatus.UNAUTHORIZED)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        f["context"]["source"] == "reauth"
        for f in hass.config_entries.flow.async_progress()
    )

    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, json=PING_OK)
    result = await _start_discovery(hass, _discovery(token=NEW_TOKEN))
    await hass.async_block_till_done()

    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_TOKEN] == NEW_TOKEN
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress()


# Manual setup


async def test_user_happy_path(
    hass: HomeAssistant, mock_ping: AiohttpClientMocker
) -> None:
    """Manual setup with host, port and token."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: f" {HOST} ", CONF_PORT: PORT, CONF_TOKEN: f"{TOKEN}\n"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Notification API"
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: PORT, CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == DOMAIN


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"status": HTTPStatus.UNAUTHORIZED}, "invalid_auth"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"status": HTTPStatus.BAD_GATEWAY}, "cannot_connect"),
        ({"status": HTTPStatus.NOT_FOUND}, "unknown"),
    ],
    ids=["invalid_auth", "timeout", "5xx", "unexpected_status"],
)
async def test_user_errors_then_recover(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, Any],
    error: str,
) -> None:
    """Each failure maps to an error key, and the user can fix it and retry."""
    aioclient_mock.get(PING_URL, **mock_kwargs)
    user_input = {CONF_HOST: HOST, CONF_PORT: PORT, CONF_TOKEN: TOKEN}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, json=PING_OK)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_unexpected_exception(hass: HomeAssistant) -> None:
    """A bug in the client surfaces as `unknown`, not a crashed flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "custom_components.notification_api.config_flow.NotificationApiClient.async_ping",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_PORT: PORT, CONF_TOKEN: TOKEN}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_user_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Only one entry: manual setup aborts if one exists."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# Reauth


async def test_reauth(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Reauth asks for a new token, validates it and reloads the entry."""
    result = await init_integration.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    placeholders = result["description_placeholders"]
    assert placeholders["host"] == HOST
    assert placeholders["port"] == str(PORT)

    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, status=HTTPStatus.UNAUTHORIZED)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "still-wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, json=PING_OK)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: NEW_TOKEN}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert init_integration.data[CONF_TOKEN] == NEW_TOKEN
    assert init_integration.data[CONF_HOST] == HOST
    assert init_integration.state is ConfigEntryState.LOADED
    _, _, _, headers = aioclient_mock.mock_calls[-1]
    assert headers["Authorization"] == f"Bearer {NEW_TOKEN}"


async def test_reauth_cannot_connect(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A network error during reauth is reported on the form."""
    result = await init_integration.start_reauth_flow(hass)
    aioclient_mock.clear_requests()
    aioclient_mock.get(PING_URL, exc=TimeoutError())

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: NEW_TOKEN}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert init_integration.data[CONF_TOKEN] == TOKEN
