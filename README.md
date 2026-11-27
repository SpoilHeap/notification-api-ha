# Notification API for Home Assistant

A Home Assistant custom integration for the **Notification API** add-on, which sends
Telegram and SMS notifications to named contacts and groups. Home Assistant says
*who* (`contact:alice`, `group:family`), *what* and *how urgent*; the add-on decides
how to deliver it.

This first version (0.1.0) connects to the add-on and reports its status:

| Entity | Description |
|---|---|
| `binary_sensor.notification_api_connected` | On while the add-on answers with a valid token. Stays available, so it reads *Disconnected* when the add-on is down. |
| `sensor.notification_api_add_on_version` | The add-on version. Unavailable while the add-on can't be reached. |

Sending (`notify.<handle>` entities and the `notification_api.send` action) and
delivery events come in later versions, once the add-on exposes those endpoints.

## Requirements

- Home Assistant OS or Supervised, **2026.10** or newer.
- The Notification API add-on, installed and running.

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
existing entry is updated in place.

### Manual

If discovery doesn't appear, add the integration from **Settings → Devices &
services → Add integration → Notification API** and enter the host, port and API
token shown on the add-on's **Integration** page.

### Re-authentication

If the add-on rejects the stored token, Home Assistant asks you to re-authenticate.
Enter the current token from the add-on's Integration page, or restart Home
Assistant while the add-on is running so discovery delivers it again.

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
