"""Webhook receiver for events from the add-on (spec 4.3, 4.4).

The integration owns both ends of the registration: it registers an HA webhook,
generates the shared secret, and hands both to the add-on with `PUT /webhook`.
The add-on then POSTs signed event envelopes to that URL.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import hmac
from http import HTTPStatus
import json
import secrets
from typing import Any

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_SECRET,
    DEDUPE_SIZE,
    DEFAULT_NAME,
    DOMAIN,
    EVENT_DELIVERY_FAILED,
    EVENT_DELIVERY_SENT,
    EVENT_TYPE_DELIVERY_FAILED,
    EVENT_TYPE_DELIVERY_SENT,
    EVENT_TYPE_RECIPIENTS_CHANGED,
    EVENT_TYPE_WEBHOOK_TEST,
    EVENT_WEBHOOK_TEST,
    LOGGER,
    SIGNATURE_HEADER,
    SIGNATURE_PREFIX,
    TIMESTAMP_HEADER,
    TIMESTAMP_TOLERANCE,
    WEBHOOK_SECRET_BYTES,
)
from .coordinator import NotificationApiConfigEntry

# Event types that map one-to-one to an HA bus event.
_BUS_EVENTS = {
    EVENT_TYPE_DELIVERY_SENT: EVENT_DELIVERY_SENT,
    EVENT_TYPE_DELIVERY_FAILED: EVENT_DELIVERY_FAILED,
    EVENT_TYPE_WEBHOOK_TEST: EVENT_WEBHOOK_TEST,
}


def generate_secret() -> str:
    """Return a new shared secret: 32 random bytes as standard base64."""
    return base64.b64encode(secrets.token_bytes(WEBHOOK_SECRET_BYTES)).decode("ascii")


def build_webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Return the URL the add-on calls to reach this webhook.

    The add-on runs in a container on the Supervisor network, where Core is always
    `homeassistant` (spec 9.3; the Supervisor's DNS resolves it), on the port and
    scheme Core itself serves (`http: server_port` / `ssl_certificate`, exposed as
    `hass.config.api`). `get_url()` is deliberately not used: the internal URL is
    whatever the user typed for browsers on the LAN (often a `.local` name or LAN
    IP that the container can't resolve or that NATs badly), the external URL goes
    out to the internet and back (or via a reverse proxy/Nabu Casa), and neither
    is guaranteed to exist. The Supervisor network path is the one stable route.
    Not yet verified on a real HA OS box (in particular with `ssl_certificate`
    set, where the certificate won't name `homeassistant`).
    """
    api = hass.config.api
    port = api.port if api is not None else 8123
    scheme = "https" if api is not None and api.use_ssl else "http"
    return f"{scheme}://homeassistant:{port}{webhook.async_generate_path(webhook_id)}"


def compute_signature(key: bytes, timestamp: str, body: bytes) -> str:
    """Return the signature header value for an event (spec 4.4, AC37)."""
    digest = hmac.new(key, timestamp.encode("ascii") + b"." + body, hashlib.sha256)
    return f"{SIGNATURE_PREFIX}{digest.hexdigest()}"


class NotificationApiWebhookHandler:
    """Verifies, deduplicates and dispatches webhook events for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: NotificationApiConfigEntry) -> None:
        """Initialise the handler with the entry's shared secret."""
        self._hass = hass
        self._entry = entry
        # The HMAC key is the raw 32 bytes, not the base64 text.
        self._key = base64.b64decode(entry.data[CONF_WEBHOOK_SECRET])
        # Recently seen event ids, oldest first. In memory only: a restart or
        # reload forgets them, which the 300 s timestamp window bounds.
        self._seen: OrderedDict[str, None] = OrderedDict()

    @callback
    def async_register(self) -> None:
        """Register the HA webhook, and unregister it when the entry unloads."""
        webhook_id: str = self._entry.data[CONF_WEBHOOK_ID]
        webhook.async_register(
            self._hass,
            DOMAIN,
            DEFAULT_NAME,
            webhook_id,
            self.async_handle,
            local_only=True,
            allowed_methods=["POST"],
        )
        self._entry.async_on_unload(
            lambda: webhook.async_unregister(self._hass, webhook_id)
        )

    async def async_handle(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle one POST from the add-on."""
        body = await request.read()
        if not self._is_authentic(request, body):
            return web.Response(status=HTTPStatus.UNAUTHORIZED)

        envelope = _parse_envelope(body)
        if envelope is None:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        event_id, event_type, occurred_at, data = envelope

        if event_id in self._seen:
            LOGGER.debug("Ignoring duplicate webhook event %s", event_id)
            return web.Response(status=HTTPStatus.OK)
        self._remember(event_id)

        if bus_event := _BUS_EVENTS.get(event_type):
            hass.bus.async_fire(
                bus_event, {**data, "event_id": event_id, "occurred_at": occurred_at}
            )
        elif event_type == EVENT_TYPE_RECIPIENTS_CHANGED:
            # Don't make the add-on wait (it times out after 5 s). The debouncer
            # runs the first request immediately and coalesces bursts.
            coordinator = self._entry.runtime_data.recipients_coordinator
            self._entry.async_create_task(
                hass, coordinator.async_request_refresh(), "recipients refresh"
            )
        else:
            LOGGER.debug("Ignoring webhook event %s of type %s", event_id, event_type)
        return web.Response(status=HTTPStatus.OK)

    def _is_authentic(self, request: web.Request, body: bytes) -> bool:
        """Check the signature and the timestamp window (AC37)."""
        timestamp = request.headers.get(TIMESTAMP_HEADER)
        signature = request.headers.get(SIGNATURE_HEADER)
        if not (
            timestamp and signature and timestamp.isascii() and timestamp.isdigit()
        ):
            LOGGER.warning("Rejected a webhook call without a valid signature header")
            return False
        expected = compute_signature(self._key, timestamp, body)
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            LOGGER.warning("Rejected a webhook call with a bad signature")
            return False
        skew = abs(dt_util.utcnow().timestamp() - int(timestamp))
        if skew > TIMESTAMP_TOLERANCE:
            LOGGER.warning(
                "Rejected a webhook call with a timestamp %.0f s off; "
                "check the clocks, or this is a replay",
                skew,
            )
            return False
        return True

    def _remember(self, event_id: str) -> None:
        self._seen[event_id] = None
        if len(self._seen) > DEDUPE_SIZE:
            self._seen.popitem(last=False)


def _parse_envelope(body: bytes) -> tuple[str, str, str, dict[str, Any]] | None:
    """Return (id, type, occurred_at, data), or None if the envelope is invalid."""
    try:
        envelope = json.loads(body)
    except ValueError:
        LOGGER.warning("Rejected a webhook call with an invalid JSON body")
        return None
    if isinstance(envelope, dict):
        event_id = envelope.get("id")
        event_type = envelope.get("type")
        occurred_at = envelope.get("occurred_at")
        data = envelope.get("data")
        if (
            isinstance(event_id, str)
            and event_id
            and isinstance(event_type, str)
            and isinstance(occurred_at, str)
            and isinstance(data, dict)
        ):
            return event_id, event_type, occurred_at, data
    LOGGER.warning("Rejected a webhook call with an incomplete event envelope")
    return None
