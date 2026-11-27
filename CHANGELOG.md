# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Notify entities, one per contact and group, named after the recipient and with
  `handle` and `type` attributes. Entity ids come from the handle, so a contact
  and a group with the same name don't collide:
  `notify.notification_api_contact_alice`, `notify.notification_api_group_family`.
  `notify.send_message` sends at normal priority, with an optional title.
- The entities follow the recipients list: new ones appear within 30 seconds (or
  immediately on `recipients.changed`), renames update the name, and deleted
  recipients lose their entity and its registry entry, including ones deleted
  while Home Assistant was off. While the list can't be loaded the entities are
  unavailable, not removed.
- `notification_api.send` action: `to` (handles and/or notify entity ids),
  `message`, `title`, `priority`, `channels`, `tag` and `idempotency_key`. It
  returns the add-on's `notification_id` and `deliveries` as response data.
- Each notification's `source` is the calling automation or script, or the
  action's name when called from elsewhere.
- Errors with their own messages: rejected request (with the add-on's field
  errors), no channel to deliver on, add-on unreachable, and add-on too old to
  send. A rejected token starts re-authentication.

### Compatibility

- Sending needs an add-on version with the delivery feature (after 0.3.0). With
  0.2.0 and 0.3.0 every send fails with "the add-on doesn't support sending
  yet". Add-on 0.2.0 has no recipients list, so no notify entities either.

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
