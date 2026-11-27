# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added

- Setup through Supervisor discovery of the Notification API add-on: confirm once,
  no token to type.
- Manual setup (host, port, API token) as a fallback.
- Re-authentication when the add-on rejects the token. A rediscovery with a new
  token or host updates the existing entry and reloads it.
- `Connected` binary sensor and `Add-on version` sensor on a Notification API
  service device, polled every 60 seconds.
- Diagnostics, with the API token redacted.
