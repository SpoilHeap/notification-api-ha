# Notification API for Home Assistant

A Home Assistant custom integration for the **Notification API** add-on, which sends
Telegram and SMS notifications to named contacts and groups. Home Assistant says
*who* (`contact:alice`, `group:family`), *what* and *how urgent*; the add-on decides
how to deliver it.

The integration connects to the add-on, reports its status, lists its recipients
and turns the add-on's delivery events into Home Assistant events:

| Entity | Description |
|---|---|
| `binary_sensor.notification_api_connected` | On while the add-on answers with a valid token. Stays available, so it reads *Disconnected* when the add-on is down. |
| `sensor.notification_api_add_on_version` | The add-on version. Unavailable while the add-on can't be reached. |
| `sensor.notification_api_recipients` | How many contacts and groups the add-on has. The `handles` attribute lists them (`contact:alice`, `group:family`, ...). Refreshed every 30 seconds, and immediately when the add-on reports a change. |

Sending (`notify.<handle>` entities and the `notification_api.send` action) comes
in a later version.

## Events

The integration registers a webhook with the add-on when it is set up, and fires
these events on the Home Assistant event bus. Each event's data is the add-on's
event data plus `event_id` (unique per event) and `occurred_at` (ISO 8601, UTC).

| Event | When | Data |
|---|---|---|
| `notification_api_delivery_sent` | A delivery reached `sent`. | `notification_id`, `delivery_id`, `contact`, `via`, `channel`, `tag`, `source` |
| `notification_api_delivery_failed` | A delivery failed after all retries. | The same, plus `error` |
| `notification_api_webhook_test` | You pressed **Send test event** on the add-on's **Integration** page. | Only `event_id` and `occurred_at` |

To react to failed deliveries:

```yaml
triggers:
  - trigger: event
    event_type: notification_api_delivery_failed
actions:
  - action: persistent_notification.create
    data:
      title: "Notification to {{ trigger.event.data.contact }} failed"
      message: "{{ trigger.event.data.channel }}: {{ trigger.event.data.error }}"
```

To check that events reach Home Assistant, open **Settings → Developer tools → Events**
(`/config/developer-tools/event`; Developer tools moved into Settings in 2026), start
listening to `notification_api_webhook_test`, and *then* press **Send test event** on the
add-on's Integration page. The Events tool only shows events that arrive while it's listening.

Events are best-effort: the add-on sends each one once and doesn't retry. Every
call is signed with a secret shared only between the integration and the add-on,
calls older than five minutes or repeated are ignored, and the webhook only
accepts calls from the local network.

## Compatibility

| Add-on version | What works |
|---|---|
| 0.2.0 | Connected and Add-on version. The add-on has no webhook or recipients endpoints yet, so no events are fired and the Recipients sensor is unavailable. A warning is logged once. |
| Newer | Everything above. When the add-on is updated from 0.2.0, the integration notices the new version, reloads, and registers its webhook. |

## Requirements

- Home Assistant OS or Supervised, **2026.10** or newer.
- The Notification API add-on, installed and running.
- Home Assistant serving **plain HTTP** on its own port. The add-on calls the webhook at
  `http://homeassistant:<port>`. If Home Assistant itself has `ssl_certificate` set, the
  certificate won't match the `homeassistant` hostname, so events can't be delivered. That setup is
  not supported in this version. HTTPS through Nabu Casa or a reverse proxy in front of Home
  Assistant is fine.

## Installation

### Home Assistant 2026.11 and newer (Marketplace)

HACS is built into Home Assistant as the **Marketplace**.

1. Go to **Settings → Marketplace** and choose **Add from link** (top right).
2. Enter `SpoilHeap/notification-api-ha` and choose **Add**. If asked for a **Type**, pick **Integration**.
   Custom repositories need a GitHub connection: if prompted, choose **Continue** and enter the code on GitHub.
3. Open **Notification API** in the list, install it, and restart Home Assistant.

### Home Assistant 2026.10 (HACS)

1. In HACS, open the menu → **Custom repositories**.
2. Add `https://github.com/SpoilHeap/notification-api-ha` with type **Integration**.
3. Install **Notification API** and restart Home Assistant.

## Setup

### Automatic (Supervisor discovery)

When the add-on starts it announces itself to Home Assistant. Go to
**Settings → Devices & services**: *Notification API* appears under **Discovered**.
Select **Add** and confirm. The host, port and API token come from the add-on;
there is nothing to type.

If the add-on rotates its token, or is reinstalled, it re-announces itself and the
existing entry is updated in place. The integration then registers its webhook
again with the new token. A reinstalled add-on (a fresh database, detected by its
instance id) gets the webhook registered again too.

### Manual

If discovery doesn't appear, add the integration from **Settings → Devices &
services → Add integration → Notification API** and enter the host, port and API
token shown on the add-on's **Integration** page.

### Re-authentication

If the add-on rejects the stored token, Home Assistant asks you to re-authenticate.
Enter the current token from the add-on's Integration page, or restart Home
Assistant while the add-on is running so discovery delivers it again.

### Removal

Deleting the integration also removes its webhook from the add-on. If the add-on
is stopped at the time, the integration is still removed.

## Development

Python 3.14 is required (it matches the Home Assistant version pinned by
`pytest-homeassistant-custom-component`).

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt

.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy custom_components
.venv/bin/pytest --cov=custom_components.notification_api --cov-report=term-missing --cov-fail-under=90
```

CI also runs [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest/)
and the [HACS action](https://hacs.xyz/docs/publish/action/).

Commits follow [Conventional Commits](https://www.conventionalcommits.org/), versions
follow [SemVer](https://semver.org/), and user-visible changes go in
[CHANGELOG.md](CHANGELOG.md). Releases bump `version` in
`custom_components/notification_api/manifest.json` and are tagged `vX.Y.Z`.

## Licence

[MIT](LICENSE)
