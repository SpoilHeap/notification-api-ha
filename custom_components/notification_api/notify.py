"""Notify entities: one per contact and group (spec 11, AC92, AC93).

The entity set follows the recipients coordinator: every update adds entities for
new handles, renames entities whose display name changed, and removes the entity
(and its registry entry) of a handle that is gone. While the coordinator has no
current data (add-on down, or v0.2.0 without `/recipients`) nothing is added or
removed, and the existing entities are unavailable.
"""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.notify import (
    DOMAIN as NOTIFY_DOMAIN,
    NotifyEntity,
    NotifyEntityDescription,
    NotifyEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Recipient
from .const import (
    ATTR_HANDLE,
    ATTR_RECIPIENT_TYPE,
    DEFAULT_PRIORITY,
    SOURCE_NOTIFY_ACTION,
)
from .coordinator import (
    NotificationApiConfigEntry,
    NotificationApiRecipientsCoordinator,
)
from .entity import NotificationApiEntity
from .services import async_send, source_from_context

# Sending is a single HTTP call to the add-on, which queues it (AC13).
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NotificationApiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the notify entities, and keep them in step with the recipients."""
    coordinator = entry.runtime_data.recipients_coordinator
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    added: set[str] = set()

    @callback
    def _async_sync_entities() -> None:
        if coordinator.data is None or not coordinator.last_update_success:
            return  # Unavailable: keep the entities, change nothing.
        recipients = {r.handle: r for r in coordinator.data.recipients}

        # Handles are immutable (AC51), so a handle that is gone was deleted.
        # Scanning the registry also cleans up recipients that were deleted while
        # Home Assistant wasn't running. Removing the registry entry removes the
        # entity from Home Assistant too.
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg_entry.domain != NOTIFY_DOMAIN:
                continue
            handle = reg_entry.unique_id.removeprefix(prefix)
            if handle not in recipients:
                registry.async_remove(reg_entry.entity_id)
                added.discard(handle)

        new = [handle for handle in recipients if handle not in added]
        added.update(new)
        async_add_entities(
            NotificationApiNotifyEntity(coordinator, recipients[handle])
            for handle in new
        )

    _async_sync_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_sync_entities))


class NotificationApiNotifyEntity(
    NotificationApiEntity[NotificationApiRecipientsCoordinator], NotifyEntity
):
    """Sends to one contact or group, by its handle."""

    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(
        self, coordinator: NotificationApiRecipientsCoordinator, recipient: Recipient
    ) -> None:
        """Initialise the entity. The unique id is `<entry_id>_<handle>`."""
        super().__init__(
            coordinator,
            NotifyEntityDescription(key=recipient.handle, translation_key="recipient"),
        )
        self._handle = recipient.handle
        self._type = recipient.type
        self._attr_name = recipient.name

    @property
    @override
    def suggested_object_id(self) -> str:
        """Base the entity id on the handle, so `contact:x` and `group:x` differ.

        Home Assistant prefixes the device name, giving for example
        `notify.notification_api_contact_alice` (AC57).
        """
        recipient_type, _, name = self._handle.partition(":")
        return f"{recipient_type} {name}" if name else self._handle

    @property
    @override
    def available(self) -> bool:
        """Available while the recipients list is current and has this handle."""
        return super().available and self._recipient() is not None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the handle and type, for templates and automations."""
        return {ATTR_HANDLE: self._handle, ATTR_RECIPIENT_TYPE: self._type}

    def _recipient(self) -> Recipient | None:
        # Entities are only created from data, and the coordinator keeps its last
        # data on failure, so `data` is never None here.
        recipients = self.coordinator.data.recipients
        return next((r for r in recipients if r.handle == self._handle), None)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Follow renames: the display name can change, the handle can't (AC51)."""
        recipient = self._recipient()
        if recipient is not None and recipient.name != self._attr_name:
            self._attr_name = recipient.name
            # Also update the registry, so the entity settings show the new name.
            er.async_get(self.hass).async_update_entity(
                self.entity_id, original_name=recipient.name
            )
        super()._handle_coordinator_update()

    @override
    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send `message` (and `title`) to this recipient at normal priority."""
        await async_send(
            self.hass,
            self.coordinator.config_entry,
            to=[self._handle],
            message=message,
            title=title,
            priority=DEFAULT_PRIORITY,
            # Set by Home Assistant from the `notify.send_message` call.
            source=source_from_context(self._context, SOURCE_NOTIFY_ACTION),
        )
