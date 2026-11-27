"""Shared test values."""

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN

from custom_components.notification_api.const import CONF_ADDON_SLUG

HOST = "local-notification-api"
PORT = 8080
TOKEN = "discovered-token-0123456789abcdefghijklmnopqrstu"
NEW_TOKEN = "rotated-token-0123456789abcdefghijklmnopqrstuvw"
SLUG = "local_notification_api"
UUID = "0123456789abcdef0123456789abcdef"
VERSION = "0.1.0"

PING_URL = f"http://{HOST}:{PORT}/api/v1/ping"
PING_OK = {"ok": True, "version": VERSION}

ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_PORT: PORT,
    CONF_TOKEN: TOKEN,
    CONF_ADDON_SLUG: SLUG,
}
