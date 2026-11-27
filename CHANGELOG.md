# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-27

### Added

- Delivery events: the integration registers a signed webhook with the add-on and
  fires `notification_api_delivery_sent`, `notification_api_delivery_failed` and
  `notification_api_webhook_test` (the add-on's **Send test event** button).
  Calls with a missing or wrong signature, a timestamp more than 300 seconds off,
  or a repeated event id are rejected or ignored.
- `Recipients` sensor: the number of contacts and groups, with their handles as an
  attribute. Polled every 30 seconds with `If-None-Match` (a 304 costs nothing),
  and refreshed immediately on `recipients.changed`.
- The webhook is registered on every setup, so it is registered again after a
  reauth or a rediscovery with a new token. A reinstalled add-on (new
  `instance_id`) makes the entry reload and register again.
- Removing the integration removes the webhook from the add-on (best-effort).
- Diagnostics include the recipients and the webhook status; the webhook secret
  and id are redacted.

### Changed

- The integration now depends on Home Assistant's `webhook` integration.

### Compatibility

- Works with add-on 0.2.0, which has no `instance_id`, `/webhook` or
  `/recipients`: setup succeeds, a warning is logged once, and the Recipients
  sensor is unavailable. Updating the add-on reloads the entry, which then
  registers the webhook.

## [0.1.0] - 2026-11-27

### Added

- Setup through Supervisor discovery of the Notification API add-on: confirm once,
  no token to type.
- Manual setup (host, port, API token) as a fallback.
- Re-authentication when the add-on rejects the token. A rediscovery with a new
  token or host updates the existing entry and reloads it.
- `Connected` binary sensor and `Add-on version` sensor on a Notification API
  service device, polled every 60 seconds.
- Diagnostics, with the API token redacted.
