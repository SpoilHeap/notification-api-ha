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
INSTANCE_ID = "8d3f0c3e-5f1b-4a51-9b0e-3c2f7f4d1a10"
NEW_INSTANCE_ID = "1f6d2b7a-0c4e-4f8a-a5d3-9e8b7c6a5f40"

BASE_URL = f"http://{HOST}:{PORT}/api/v1"
PING_URL = f"{BASE_URL}/ping"
WEBHOOK_URL = f"{BASE_URL}/webhook"
RECIPIENTS_URL = f"{BASE_URL}/recipients"
NOTIFY_URL = f"{BASE_URL}/notify"

PING_OK = {"ok": True, "version": VERSION, "instance_id": INSTANCE_ID}
# The released add-on v0.2.0: no instance_id, no /webhook, no /recipients.
PING_V020 = {"ok": True, "version": "0.2.0"}

REVISION = "17"
RECIPIENTS = [
    {"handle": "contact:alice", "name": "Alice", "type": "contact"},
    {"handle": "group:family", "name": "Family", "type": "group"},
]
RECIPIENTS_OK = {"revision": REVISION, "recipients": RECIPIENTS}

NOTIFY_OK = {
    "notification_id": "ntf_01",
    "deliveries": [
        {
            "delivery_id": "dlv_1",
            "contact": "alice",
            "via": "contact:alice",
            "channel": "telegram",
            "status": "queued",
        }
    ],
}

ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_PORT: PORT,
    CONF_TOKEN: TOKEN,
    CONF_ADDON_SLUG: SLUG,
}
