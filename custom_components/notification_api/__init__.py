"""The Notification API integration.

Connects Home Assistant to the Notification API add-on, which delivers Telegram and
SMS notifications to named contacts and groups.
"""

from __future__ import annotations

from homeassistant.components import webhook
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.hass_dict import HassKey

from .api import (
    NotificationApiAuthError,
    NotificationApiClient,
    NotificationApiConnectionError,
    NotificationApiError,
    NotificationApiNotFoundError,
)
from .const import CONF_WEBHOOK_ID, CONF_WEBHOOK_SECRET, DOMAIN, LOGGER
from .coordinator import (
    NotificationApiConfigEntry,
    NotificationApiCoordinator,
    NotificationApiData,
    NotificationApiRecipientsCoordinator,
)
from .receiver import NotificationApiWebhookHandler, build_webhook_url, generate_secret

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Set once the "add-on doesn't support webhooks" warning was logged in this run.
_WARNED_WEBHOOK_UNSUPPORTED: HassKey[bool] = HassKey(f"{DOMAIN}_webhook_unsupported")


def _client(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> NotificationApiClient:
    return NotificationApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> bool:
    """Set up Notification API from a config entry."""
    client = _client(hass, entry)
    coordinator = NotificationApiCoordinator(hass, entry, client)
    recipients = NotificationApiRecipientsCoordinator(hass, entry, client)
    entry.runtime_data = NotificationApiData(
        client=client, coordinator=coordinator, recipients_coordinator=recipients
    )
    # Raises ConfigEntryAuthFailed (-> reauth) on 401, ConfigEntryNotReady otherwise.
    # Also stores the add-on's instance_id in the entry data.
    await coordinator.async_config_entry_first_refresh()

    _async_ensure_webhook_data(hass, entry)
    # Listen before registering, so the add-on's first event can't be missed.
    # The HA webhook is unregistered on unload, and if setup fails below.
    NotificationApiWebhookHandler(hass, entry).async_register()
    # Every setup registers again: it's idempotent, and it is what re-registers
    # after a token change (reauth and rediscovery reload the entry) or a reinstall.
    entry.runtime_data.webhook_registered = await _async_register_webhook(
        hass, entry, client
    )

    # Never fails setup: on add-on v0.2.0 (404) the Recipients sensor is
    # unavailable, and polling keeps retrying.
    await recipients.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_ensure_webhook_data(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> None:
    """Create the webhook id and secret once, and keep them across restarts.

    Entries are created without them (by the config flow, or by v0.1.0), so the
    first setup generates them. Later setups reuse them: a reload or restart must
    not invalidate the registration the add-on holds.
    """
    updates: dict[str, str] = {}
    if CONF_WEBHOOK_ID not in entry.data:
        updates[CONF_WEBHOOK_ID] = webhook.async_generate_id()
    if CONF_WEBHOOK_SECRET not in entry.data:
        updates[CONF_WEBHOOK_SECRET] = generate_secret()
    if updates:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})


async def _async_register_webhook(
    hass: HomeAssistant,
    entry: NotificationApiConfigEntry,
    client: NotificationApiClient,
) -> bool:
    """Register the webhook with the add-on (`PUT /webhook`). Return True on 204."""
    url = build_webhook_url(hass, entry.data[CONF_WEBHOOK_ID])
    try:
        await client.async_put_webhook(url, entry.data[CONF_WEBHOOK_SECRET])
    except NotificationApiNotFoundError:
        # Add-on v0.2.0. Setup continues without events; the ping coordinator
        # reloads the entry (so this retries) when the add-on version changes.
        if not hass.data.get(_WARNED_WEBHOOK_UNSUPPORTED):
            hass.data[_WARNED_WEBHOOK_UNSUPPORTED] = True
            LOGGER.warning(
                "The Notification API add-on doesn't support webhooks yet, so "
                "delivery events won't be fired until it is updated"
            )
        else:
            LOGGER.debug("The Notification API add-on still has no webhook endpoint")
        return False
    except NotificationApiAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except NotificationApiConnectionError as err:
        # The ping just succeeded, so this is transient: retry setup with backoff.
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="webhook_register_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    except NotificationApiError as err:
        # E.g. 400: the add-on rejected the URL or secret. Retrying won't help.
        LOGGER.error("The add-on rejected the webhook registration: %s", err)
        return False
    LOGGER.debug("Registered webhook %s with the add-on", url)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> bool:
    """Unload a config entry. The HA webhook is unregistered by `async_on_unload`."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: NotificationApiConfigEntry
) -> None:
    """Remove the webhook registration from the add-on, best-effort.

    Runs after unload. Never raises, so removal can't be blocked by an add-on
    that is stopped, already uninstalled, or older than `/webhook`.
    """
    # HA doesn't call this for ignored discoveries, so the entry has a token.
    try:
        await _client(hass, entry).async_delete_webhook()
    except NotificationApiNotFoundError:
        LOGGER.debug("The add-on has no webhook endpoint; nothing to remove")
    except NotificationApiError as err:
        LOGGER.warning(
            "Could not remove the webhook from the Notification API add-on: %s", err
        )
    except Exception:  # noqa: BLE001  # removal must never be blocked
        LOGGER.exception("Unexpected error removing the webhook from the add-on")
