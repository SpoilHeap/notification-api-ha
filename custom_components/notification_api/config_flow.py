"""Config flow for the Notification API integration.

Setup paths:
- Supervisor discovery (spec 9.4): the add-on publishes `{host, port, token}` for the
  `notification_api` service; HA starts this flow with source `hassio`.
- Manual (`user`): host, port and token, as shown on the add-on's Integration page.
- Reauth: the token was rejected (rotated, or add-on reinstalled); ask for a new one.

Unique ID strategy: a fixed value (the domain). One HA has at most one Notification
API add-on (it is a Supervisor add-on, one install per host), so all three paths
must converge on the same entry:
- A rediscovery after a token rotation, an add-on reinstall (new discovery uuid) or
  a hostname change updates the existing entry instead of adding a second one.
- A manual entry created before discovery worked is adopted by later discovery.
The Supervisor discovery uuid is deliberately not used: it changes on reinstall,
and it would not match a manually created entry. `single_config_entry` is not set
in the manifest because HA then aborts *every* discovery flow once an entry exists,
which would stop discovery from delivering a rotated token.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import probatio

from homeassistant.config_entries import SOURCE_IGNORE, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import (
    NotificationApiAuthError,
    NotificationApiClient,
    NotificationApiConnectionError,
    NotificationApiError,
)
from .const import CONF_ADDON_SLUG, DEFAULT_NAME, DEFAULT_PORT, DOMAIN, LOGGER

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): str,
        probatio.Required(CONF_PORT, default=DEFAULT_PORT): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1, max=65535)
        ),
        probatio.Required(probatio.Secret(CONF_TOKEN)): str,
    }
)
REAUTH_SCHEMA = probatio.Schema({probatio.Required(probatio.Secret(CONF_TOKEN)): str})


class NotificationApiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Notification API."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovered: dict[str, Any] = {}
        self._addon_name = DEFAULT_NAME

    async def _async_validate(self, host: str, port: int, token: str) -> str | None:
        """Ping the add-on. Return an error key, or None on success."""
        client = NotificationApiClient(
            async_get_clientsession(self.hass), host, port, token
        )
        try:
            await client.async_ping()
        except NotificationApiAuthError:
            return "invalid_auth"
        except NotificationApiConnectionError:
            return "cannot_connect"
        except NotificationApiError:
            LOGGER.exception("Unexpected response from the Notification API add-on")
            return "unknown"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error validating the Notification API add-on")
            return "unknown"
        return None

    # Supervisor discovery

    @override
    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle Supervisor discovery from the add-on."""
        config = discovery_info.config
        try:
            self._discovered = {
                CONF_HOST: str(config[CONF_HOST]),
                CONF_PORT: int(config[CONF_PORT]),
                CONF_TOKEN: str(config[CONF_TOKEN]),
                CONF_ADDON_SLUG: discovery_info.slug,
            }
        except KeyError, TypeError, ValueError:
            LOGGER.warning(
                "Ignoring malformed discovery from add-on %s", discovery_info.slug
            )
            return self.async_abort(reason="invalid_discovery_info")
        self._addon_name = discovery_info.name or DEFAULT_NAME

        existing = await self.async_set_unique_id(DOMAIN)
        if existing is not None:
            if existing.source == SOURCE_IGNORE:
                return self.async_abort(reason="already_configured")
            # The Supervisor is the source of truth for host/port/token: apply them
            # and reload if anything changed. Unlike _abort_if_unique_id_configured
            # this also reloads an entry stuck in SETUP_ERROR after a 401, and the
            # reload aborts any reauth flow that is waiting for the user.
            return self.async_update_reload_and_abort(
                existing,
                data_updates=self._discovered,
                reason="already_configured",
                reload_even_if_entry_is_unchanged=False,
            )

        self.context["title_placeholders"] = {"name": self._addon_name}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm the discovered add-on, then validate it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_validate(
                self._discovered[CONF_HOST],
                self._discovered[CONF_PORT],
                self._discovered[CONF_TOKEN],
            )
            if error == "invalid_auth":
                # The user can't fix a discovered token from this form.
                return self.async_abort(reason="discovery_invalid_auth")
            if error is None:
                return self.async_create_entry(
                    title=self._addon_name, data=self._discovered
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="hassio_confirm",
            description_placeholders={"addon": self._addon_name},
            errors=errors,
        )

    # Manual setup

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual setup."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            token = user_input[CONF_TOKEN].strip()
            error = await self._async_validate(host, port, token)
            if error is None:
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    # Reauth

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after the add-on rejected the token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token and validate it against the existing host/port."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            error = await self._async_validate(
                entry.data[CONF_HOST], entry.data[CONF_PORT], token
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={
                "host": entry.data[CONF_HOST],
                "port": str(entry.data[CONF_PORT]),
            },
        )
