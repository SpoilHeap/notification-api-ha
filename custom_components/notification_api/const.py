"""Constants for the Notification API integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "notification_api"
LOGGER: Final = logging.getLogger(__package__)

# Entry data key for the add-on slug, stored for discovered entries only.
CONF_ADDON_SLUG: Final = "addon_slug"
# Entry data keys for the webhook (spec 4.3) and the add-on's database identity
# (spec 4.2). All three are written by async_setup_entry, never by the config flow.
CONF_WEBHOOK_ID: Final = "webhook_id"
CONF_WEBHOOK_SECRET: Final = "webhook_secret"  # noqa: S105  # a key name
CONF_INSTANCE_ID: Final = "instance_id"

DEFAULT_NAME: Final = "Notification API"
DEFAULT_PORT: Final = 8080

# How often the coordinator pings the add-on.
UPDATE_INTERVAL: Final = timedelta(seconds=60)
# How often the recipients list is polled (spec 11). Cheap: usually a 304.
RECIPIENTS_UPDATE_INTERVAL: Final = timedelta(seconds=30)
# Per-request timeout for calls to the add-on (it lives on the local Supervisor network).
REQUEST_TIMEOUT: Final = 10

# Webhook security (spec 4.4, AC37).
WEBHOOK_SECRET_BYTES: Final = 32
SIGNATURE_HEADER: Final = "X-Notification-Api-Signature"
TIMESTAMP_HEADER: Final = "X-Notification-Api-Timestamp"
SIGNATURE_PREFIX: Final = "sha256="
# Maximum clock difference accepted, in seconds (replay protection).
TIMESTAMP_TOLERANCE: Final = 300
# How many recent event ids are remembered for deduplication.
DEDUPE_SIZE: Final = 1024

# Webhook event types (spec 4.4).
EVENT_TYPE_DELIVERY_SENT: Final = "delivery.sent"
EVENT_TYPE_DELIVERY_FAILED: Final = "delivery.failed"
EVENT_TYPE_RECIPIENTS_CHANGED: Final = "recipients.changed"
EVENT_TYPE_WEBHOOK_TEST: Final = "webhook.test"

# Home Assistant events fired for webhook events.
EVENT_DELIVERY_SENT: Final = f"{DOMAIN}_delivery_sent"
EVENT_DELIVERY_FAILED: Final = f"{DOMAIN}_delivery_failed"
EVENT_WEBHOOK_TEST: Final = f"{DOMAIN}_webhook_test"
