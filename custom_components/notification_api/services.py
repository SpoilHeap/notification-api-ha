"""Sending: the `notification_api.send` action, and the send path shared with notify.

Both the action and the notify entities go through `async_send`, so they map the
add-on's errors (spec 4.1) to the same Home Assistant exceptions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import probatio

from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    EVENT_CALL_SERVICE,
)
from homeassistant.core import (
    Context,
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
    valid_entity_id,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .api import (
    NotificationApiAuthError,
    NotificationApiConnectionError,
    NotificationApiError,
    NotificationApiNoDeliveriesError,
    NotificationApiNotFoundError,
    NotificationApiValidationError,
    NotifyResult,
)
from .const import (
    ATTR_CHANNELS,
    ATTR_IDEMPOTENCY_KEY,
    ATTR_MESSAGE,
    ATTR_PRIORITY,
    ATTR_TAG,
    ATTR_TITLE,
    ATTR_TO,
    CHANNELS,
    DEFAULT_PRIORITY,
    DOMAIN,
    PRIORITIES,
    SERVICE_SEND,
    SOURCE_SEND_ACTION,
)
from .coordinator import NotificationApiConfigEntry

# Events that start an automation or script run. Their context is the one the
# run's actions are called with, so they name the caller (see `source_from_context`).
_EVENT_AUTOMATION_TRIGGERED = "automation_triggered"
_EVENT_SCRIPT_STARTED = "script_started"
_SCRIPT_DOMAIN = "script"

SEND_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_TO): probatio.All(
            cv.ensure_list, [cv.string], probatio.Length(min=1)
        ),
        probatio.Required(ATTR_MESSAGE): cv.string,
        probatio.Optional(ATTR_TITLE): cv.string,
        probatio.Optional(ATTR_PRIORITY, default=DEFAULT_PRIORITY): probatio.In(
            PRIORITIES
        ),
        probatio.Optional(ATTR_CHANNELS): probatio.All(
            cv.ensure_list, [probatio.In(CHANNELS)]
        ),
        probatio.Optional(ATTR_TAG): cv.string,
        probatio.Optional(ATTR_IDEMPOTENCY_KEY): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the `notification_api.send` action (once, from `async_setup`)."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND,
        _async_handle_send,
        schema=SEND_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    # Home Assistant only builds an event (and so sets `Context.origin_event`) when
    # something listens to it. The recorder and logbook usually do; these no-op
    # listeners make `source_from_context` work without them.
    for event_type in (_EVENT_AUTOMATION_TRIGGERED, _EVENT_SCRIPT_STARTED):
        hass.bus.async_listen(event_type, _async_noop)


@callback
def _async_noop(event: Event) -> None:
    """Do nothing; see `async_setup_services`."""


async def _async_handle_send(call: ServiceCall) -> ServiceResponse:
    """Handle `notification_api.send`."""
    entry = _async_get_loaded_entry(call.hass)
    data = call.data
    result = await async_send(
        call.hass,
        entry,
        to=_async_resolve_targets(call.hass, entry, data[ATTR_TO]),
        message=data[ATTR_MESSAGE],
        title=data.get(ATTR_TITLE),
        priority=data[ATTR_PRIORITY],
        channels=data.get(ATTR_CHANNELS),
        tag=data.get(ATTR_TAG),
        idempotency_key=data.get(ATTR_IDEMPOTENCY_KEY),
        source=source_from_context(call.context, SOURCE_SEND_ACTION),
    )
    return result.as_dict() if call.return_response else None


@callback
def _async_get_loaded_entry(hass: HomeAssistant) -> NotificationApiConfigEntry:
    """Return the config entry (one per HA install), or raise if it isn't loaded."""
    entries: list[NotificationApiConfigEntry] = (
        hass.config_entries.async_loaded_entries(DOMAIN)
    )
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )
    return entries[0]


@callback
def _async_resolve_targets(
    hass: HomeAssistant, entry: NotificationApiConfigEntry, targets: Sequence[str]
) -> list[str]:
    """Turn notify entity ids of this entry into handles; pass handles through.

    Order and duplicates are kept: the add-on doesn't deduplicate (AC04), so
    neither does the integration. Anything that isn't an entity id is sent as a
    handle, and the add-on rejects the unknown ones (AC09).
    """
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    handles: list[str] = []
    for target in targets:
        if not valid_entity_id(target):
            handles.append(target)
            continue
        reg_entry = registry.async_get(target)
        if (
            reg_entry is None
            or reg_entry.domain != NOTIFY_DOMAIN
            or reg_entry.platform != DOMAIN
            or reg_entry.config_entry_id != entry.entry_id
            or not reg_entry.unique_id.startswith(prefix)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entity",
                translation_placeholders={"entity_id": target},
            )
        handles.append(reg_entry.unique_id.removeprefix(prefix))
    return handles


def source_from_context(context: Context | None, default: str) -> str:
    """Return what started this call, for the notification's `source` field.

    `Context.origin_event` is the first event built with the call's context:
    - an automation: `automation_triggered`, whose `entity_id` is the automation.
    - a script run by its own action (`script.front_door`): the `call_service`
      event for that action, or `script_started` when nothing listens to
      `call_service` (no recorder). By `script.turn_on`: the `call_service`
      event, with the script's entity id in the service data.
    - a script called by an automation runs in the automation's context, so
      the automation that started it all is reported.
    Anything else (Developer tools, the dashboard, a direct API call) gets
    `default`.
    """
    origin = context.origin_event if context is not None else None
    if origin is None:
        return default
    data: dict[str, Any] = origin.data
    if origin.event_type in (_EVENT_AUTOMATION_TRIGGERED, _EVENT_SCRIPT_STARTED):
        entity_id = data.get(ATTR_ENTITY_ID)
        if isinstance(entity_id, str):
            return entity_id
    elif (
        origin.event_type == EVENT_CALL_SERVICE
        and data.get(ATTR_DOMAIN) == _SCRIPT_DOMAIN
    ):
        service = data.get(ATTR_SERVICE)
        if service in ("turn_on", "toggle"):
            target = (data.get(ATTR_SERVICE_DATA) or {}).get(ATTR_ENTITY_ID)
            if isinstance(target, list) and len(target) == 1:
                target = target[0]
            if isinstance(target, str):
                return target
        else:
            return f"{_SCRIPT_DOMAIN}.{service}"
    return default


async def async_send(
    hass: HomeAssistant,
    entry: NotificationApiConfigEntry,
    *,
    to: Sequence[str],
    message: str,
    title: str | None = None,
    priority: str = DEFAULT_PRIORITY,
    channels: Sequence[str] | None = None,
    tag: str | None = None,
    idempotency_key: str | None = None,
    source: str,
) -> NotifyResult:
    """Send through the add-on, mapping its errors to Home Assistant exceptions."""
    try:
        return await entry.runtime_data.client.async_notify(
            to,
            message,
            title=title,
            priority=priority,
            channels=channels,
            tag=tag,
            source=source,
            idempotency_key=idempotency_key,
        )
    except NotificationApiValidationError as err:
        # Unknown handles (AC09) or an invalid payload (AC10).
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="send_invalid",
            translation_placeholders={"errors": err.problem.describe()},
        ) from err
    except NotificationApiNoDeliveriesError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="send_no_deliveries"
        ) from err
    except NotificationApiAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except NotificationApiNotFoundError as err:
        # `/notify` comes with the add-on's delivery feature (after v0.3.0).
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="send_unsupported"
        ) from err
    except NotificationApiConnectionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="addon_unreachable",
            translation_placeholders={"error": str(err)},
        ) from err
    except NotificationApiError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="send_failed",
            translation_placeholders={"error": str(err)},
        ) from err
