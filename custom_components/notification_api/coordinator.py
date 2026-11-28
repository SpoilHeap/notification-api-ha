"""Coordinators: one pings the add-on (and reads its channels), one polls recipients.

Two coordinators rather than one, because the two endpoints differ in every way
that matters to a coordinator:
- Interval: ping every 60 s, recipients every 30 s (spec 11, AC93).
- Failure meaning: a failed ping means "add-on down" and drives the Connected
  sensor. A failed `/recipients` (404 on add-on v0.2.0) must only make the
  Recipients sensor unavailable, not mark the add-on disconnected or fail setup.
- Change detection: recipients use `If-None-Match` and `always_update=False`, so a
  304 keeps the cached data without notifying entities.

`GET /channels` rides on the ping poll (spec 11): it changes rarely and needs no
interval of its own. Only the ping decides whether that poll succeeded, so a failing
`/channels` (404 on add-on v0.6.0 and older) makes just the channel sensors
unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ChannelsResult,
    NotificationApiAuthError,
    NotificationApiClient,
    NotificationApiError,
    NotificationApiNotFoundError,
    PingResult,
    RecipientsResult,
)
from .const import (
    CONF_INSTANCE_ID,
    DOMAIN,
    LOGGER,
    RECIPIENTS_UPDATE_INTERVAL,
    UPDATE_INTERVAL,
)


@dataclass(frozen=True, slots=True)
class ConnectivityData:
    """What the ping coordinator polls: `/ping`, then `/channels`."""

    ping: PingResult
    # None while `/channels` fails or isn't supported.
    channels: ChannelsResult | None = None
    # False when `/channels` answered 404: the add-on predates v0.7.0.
    channels_supported: bool = True


@dataclass(slots=True)
class NotificationApiData:
    """Runtime data stored on the config entry."""

    client: NotificationApiClient
    coordinator: NotificationApiCoordinator
    recipients_coordinator: NotificationApiRecipientsCoordinator
    # False while the add-on doesn't support `/webhook` (v0.2.0) or the PUT failed.
    webhook_registered: bool = False


type NotificationApiConfigEntry = ConfigEntry[NotificationApiData]


def _auth_failed() -> ConfigEntryAuthFailed:
    # Starts the reauth flow (token rotated or the add-on was reinstalled).
    return ConfigEntryAuthFailed(
        translation_domain=DOMAIN, translation_key="auth_failed"
    )


def _update_failed(err: Exception) -> UpdateFailed:
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="update_failed",
        translation_placeholders={"error": str(err)},
    )


class NotificationApiCoordinator(DataUpdateCoordinator[ConnectivityData]):
    """Polls `GET /api/v1/ping` to track connectivity, version and instance id.

    After a successful ping it also reads `GET /api/v1/channels`.
    """

    config_entry: NotificationApiConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: NotificationApiConfigEntry,
        client: NotificationApiClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client

    @override
    async def _async_update_data(self) -> ConnectivityData:
        """Ping the add-on, then read its channels."""
        try:
            result = await self.client.async_ping()
        except NotificationApiAuthError as err:
            raise _auth_failed() from err
        except NotificationApiError as err:
            raise _update_failed(err) from err

        reinstalled = self._async_check_instance_id(result.instance_id)
        if self.data is not None:
            previous = self.data.ping
            if previous.version != result.version:
                self._async_update_device_version(result.version)
            # Reloading re-registers the webhook and reloads the recipients. Needed
            # after a reinstall (fresh database), and after an add-on update while
            # the webhook isn't registered (an update from v0.2.0 adds `/webhook`).
            if reinstalled or (
                previous.version != result.version
                and not self.config_entry.runtime_data.webhook_registered
            ):
                self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
                )
        return await self._async_fetch_channels(result)

    async def _async_fetch_channels(self, ping: PingResult) -> ConnectivityData:
        """Read `/channels`. Never fails the poll, except on a rejected token."""
        try:
            channels = await self.client.async_get_channels()
        except NotificationApiAuthError as err:
            # The ping just accepted the token, so this is a rotation in between.
            raise _auth_failed() from err
        except NotificationApiNotFoundError:
            # Add-on v0.6.0 and older. Keep asking: an add-on update is then
            # picked up on the next poll.
            LOGGER.debug("The add-on has no channels endpoint (older than v0.7.0)")
            return ConnectivityData(ping=ping, channels_supported=False)
        except NotificationApiError as err:
            if self.data is None or self.data.channels is not None:
                LOGGER.warning(
                    "Could not read the Notification API add-on's channels: %s", err
                )
            return ConnectivityData(ping=ping)
        if self.data is not None and self.data.channels is None:
            LOGGER.debug("Read the Notification API add-on's channels again")
        return ConnectivityData(ping=ping, channels=channels)

    def _async_check_instance_id(self, instance_id: str | None) -> bool:
        """Store the add-on's instance id (spec 4.2). Return True if it changed.

        A different id means the add-on was reinstalled with a fresh database: it
        has no webhook registration, and its recipients may differ. During setup
        (no data yet) the setup that follows re-registers anyway.
        """
        if instance_id is None:
            return False  # Add-on v0.2.0 doesn't report one.
        stored = self.config_entry.data.get(CONF_INSTANCE_ID)
        if stored == instance_id:
            return False
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_INSTANCE_ID: instance_id},
        )
        if stored is None:
            return False  # First seen: a new entry, or an update from v0.2.0.
        LOGGER.info(
            "The Notification API add-on was reinstalled with a fresh database "
            "(instance id %s, was %s); re-registering the webhook",
            instance_id,
            stored,
        )
        return True

    def _async_update_device_version(self, version: str) -> None:
        """Keep the service device's software version current after an add-on update."""
        registry = dr.async_get(self.hass)
        # The entry owns exactly one (service) device.
        for device in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            registry.async_update_device(device.id, sw_version=version)


class NotificationApiRecipientsCoordinator(DataUpdateCoordinator[RecipientsResult]):
    """Polls `GET /api/v1/recipients` with `If-None-Match` (spec 11, AC43)."""

    config_entry: NotificationApiConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: NotificationApiConfigEntry,
        client: NotificationApiClient,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_recipients",
            update_interval=RECIPIENTS_UPDATE_INTERVAL,
            # A 304 returns the cached object, so entities are not written again.
            always_update=False,
        )
        self.client = client

    @override
    async def _async_update_data(self) -> RecipientsResult:
        """Fetch the recipients, unless the cached revision is still current."""
        # `data` is None until the first success (and after none has happened).
        cached: RecipientsResult | None = self.data
        try:
            result = await self.client.async_get_recipients(
                cached.revision if cached is not None else None
            )
        except NotificationApiAuthError as err:
            raise _auth_failed() from err
        except NotificationApiNotFoundError as err:
            # Add-on v0.2.0. Keep polling: an add-on update is then picked up.
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="recipients_unsupported"
            ) from err
        except NotificationApiError as err:
            raise _update_failed(err) from err
        if result is not None:
            return result
        if cached is None:
            # A 304 to an unconditional request: the add-on is misbehaving.
            raise _update_failed(ValueError("HTTP 304 without If-None-Match"))
        return cached  # 304 Not Modified
