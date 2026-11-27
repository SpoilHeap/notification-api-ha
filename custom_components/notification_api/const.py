"""Constants for the Notification API integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "notification_api"
LOGGER: Final = logging.getLogger(__package__)

# Entry data key for the add-on slug, stored for discovered entries only.
CONF_ADDON_SLUG: Final = "addon_slug"

DEFAULT_NAME: Final = "Notification API"
DEFAULT_PORT: Final = 8080

# How often the coordinator pings the add-on.
UPDATE_INTERVAL: Final = timedelta(seconds=60)
# Per-request timeout for calls to the add-on (it lives on the local Supervisor network).
REQUEST_TIMEOUT: Final = 10
