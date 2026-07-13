# Changelog

## 0.2.14 — 2026-07-13

### Changed
- **Per-channel DOCSIS power/SNR sensors are now opt-in.** The
  v0.2.13 default of always-on created 32+ entities on a healthy
  cable plant (16 DS power + 16 DS SNR), plus 4 upstream power
  sensors. These are now behind a `CONF_EXPOSE_DIAGNOSTICS` config
  option that defaults to **off**. Toggle it on in the integration's
  options panel only when you need to debug cable plant issues.

### Removed
- **Per-device pause/resume buttons (42 entities).** v0.2.12 added
  these for parental-controls use, but they were rarely used and
  the v0.2.13 device_tracker change broke their unique_id keys
  (collisions in the HA log). If you actually need to pause a
  device's internet access from HA, do it through the router's
  web UI, or call the `hitron_coda_5610q.pause_device` service
  directly.
- **DOCSIS provisioning step binary sensors (7 entities).** The
  `docsis_hwinit` / `docsis_ranging` / `docsis_dhcp` / etc.
  binary sensors were always on during normal operation and
  unreachable when the modem was down — pure UI noise. The fact
  that the integration is talking to the router is the actual
  signal that the modem is up.

## 0.2.13 — 2026-07-13

### Fixed
- **MAC-rotation drift in Leave Home / Arrival automations.** v0.2.12
  keyed each device_tracker entity on its MAC address. Devices that
  use "Private WiFi Address" (iOS / macOS) or randomized MAC
  (Android 10+) would create a *new* entity on every reconnect, and
  the old entity stayed in the entity registry forever in
  `not_home` state. When the device_tracker was added to a person
  entity (e.g. `person.xavier`), the person-picker would oscillate
  between the old (always not_home) and new entities, causing
  the Leave Home automation to misfire even when the user was
  physically at home.

  v0.2.13 keys entities on the router-reported **hostname** by
  default. The hostname is stable for any device that doesn't
  override it, so the entity_id never changes when the device
  rotates its MAC. The current MAC is exposed as the
  `current_mac` attribute and updated in place on every scan.

  Migration: a one-shot service `hitron_coda_5610q.migrate_to_v0_2_13`
  renames existing v0.2.12 (MAC-keyed) entities to v0.2.13
  (hostname-keyed) unique_ids and reloads the config entry.
  See `MIGRATION.md` for the full procedure.

- **Hostname normalization.** Routers sometimes report hostnames
  with leading/trailing whitespace, mixed case, or unicode
  characters. `normalize_hostname()` strips and lower-cases, and
  replaces non-alphanumeric characters with `_` so the resulting
  unique_id is filesystem-safe and stable across scans.

### Notes
- The v0.2.13 default tracking mode is `hostname`. Users who want
  the v0.2.12 (MAC-keyed) behavior can set
  `track_by: mac` in the config-entry options.
- Hostname collisions (e.g. two devices both reporting
  `android` as their hostname) are disambiguated with `_1`, `_2`
  suffixes on the identity key. Rare in practice.


### Fixed
- **All 21 device_tracker entities were collapsed under a single device
  named "Hitron CODA-5610Q"** because they all returned the same
  `DeviceInfo.identifiers={(DOMAIN, router_serial)}`. Each LAN device
  is now its own HA device, identified by its MAC address, with the
  router as the parent via `via_device=(DOMAIN, router_serial)`. The
  device name is the LAN device's hostname (falling back to the MAC
  if the router didn't report a hostname). The corresponding
  pause/resume buttons in `button.py` are now grouped under the same
  per-device identifier so they show up in the device's own card
  in the UI.

## 0.2.11 — 2026-07-13

### Fixed
- **Device tracker entities always showed state `unknown`** —
  `TrackerEntity.state` in HA 2026.x returns `None` (which becomes
  `unknown`) unless the entity has a `location_name` set or is
  member of a configured zone. The router-based integration has
  neither — devices don't have GPS coordinates. Override `state`
  in `HitronCodaDeviceTracker` to return `STATE_HOME` if
  `is_connected` else `STATE_NOT_HOME`, based on the device's
  Active/Paused status from the router's `/1/Device/Hosts/1`
  endpoint.
- Cleaned up the v0.2.10 debug logging from `coordinator.__init__`
  and `__init__.py` — fix is verified working on the live Pi, no
  longer need the verbose output.

## 0.2.10 — 2026-07-13

### Fixed
- **Coordinator only ran the initial first_refresh, never periodic updates**
  — the actual root cause of "entities exist but no state is ever recorded".
  `DataUpdateCoordinator._async_refresh` only calls `self._schedule_refresh()`
  at the END of the method (in a `finally` block), and ONLY if
  `self._listeners` is non-empty. If no entities have called
  `async_add_listener()` yet — which is the case at first setup, BEFORE
  `async_forward_entry_setups()` has wired up the platform entities — the
  periodic update loop never registers. The first refresh succeeds, the
  forwards happen, but the loop is never started.
- Explicitly call `self._schedule_refresh()` in the coordinator's
  `__init__` (it will be a no-op if listeners haven't registered yet, but
  the call is safe) and again in `__init__.py`'s `async_setup_entry` AFTER
  `async_forward_entry_setups()` returns, once all entity platforms have
  registered their listeners.

## 0.2.9 — 2026-07-13

### Fixed
- **v0.2.8 was insufficient** — even passing `config_entry` explicitly
  didn't fix the scheduling because the `update_interval` setter runs
  BEFORE `self.config_entry` is assigned in `DataUpdateCoordinator.__init__`.
  Set `self.config_entry = config_entry` before calling `super().__init__()`
  so the setter's `_schedule_refresh()` can correctly read it and register
  the periodic update loop.

## 0.2.8 — 2026-07-13

### Fixed
- **Coordinator ran only once at setup and never again** — entities existed in
  the registry but no state was ever written to the state machine. Root cause:
  the `DataUpdateCoordinator` in HA 2026.7+ uses the `update_interval` setter
  internally to schedule the periodic refresh, but that setter calls
  `_schedule_refresh()` which reads `self.config_entry` — which is only set
  AFTER `update_interval` in `__init__`. Since the integration was using
  the deprecated `config_entry=UNDEFINED` form, `self.config_entry` was
  `None` when the setter ran, and the scheduling silently failed.
  The first refresh succeeded (it doesn't go through the update_interval
  setter), but the update loop was never started.
- Pass `config_entry` explicitly to the `DataUpdateCoordinator` super
  initializer. This both silences the deprecation warning and ensures the
  periodic update loop is properly registered.

## 0.2.7 — 2026-07-13

### Fixed
- **First refresh still failed intermittently with empty/malformed responses**
  even with retry logic. The router can't handle 12 parallel requests
  reliably, so the requests pile up and the router returns empty bodies
  for many of them. Cap the parallel gather at 3 concurrent requests
  (using `asyncio.Semaphore(3)`), which combined with the v0.2.6 retry
  logic gives reliable first-refresh success.

## 0.2.6 — 2026-07-13

### Fixed
- **First refresh intermittently failed with empty/malformed responses from
  the router** (`Bad response from ...: Expecting value: line 1 column 1
  (char 0)`). The CODA-5610Q's tiny web server occasionally returns empty
  bodies when under concurrent load, which caused 1-2 of the 12 endpoints
  in the coordinator's parallel gather to fail on each attempt. The config
  entry was created but no entities were registered because the
  `asyncio.gather` raised on the first failure.
- Added retry-with-relogin to `_request_json`: up to 3 attempts, with
  exponential backoff (0.5s, 1.0s) and a fresh login before each retry.
  Transient empty responses are now recovered automatically.

## 0.2.5 — 2026-07-13

### Fixed
- **Integration created config entry but no entities ever appeared** — the
  coordinator's first refresh failed because the shared HA `aiohttp.ClientSession`
  (from `async_get_clientsession`) has a cookie jar that interferes with our
  PHPSESSID cookie, causing the router to return the HTML login page instead of
  JSON for the GET endpoints. Switched to creating a fresh `aiohttp.ClientSession`
  per config entry (and per config-flow attempt). This is a known pattern for
  integrations that have their own authentication and need cookie isolation.

## 0.2.4 — 2026-07-13

### Changed
- Added verbose `WARNING`-level logging throughout `async_setup_entry` so
  HA logs show every step (start, first refresh, platform forward, any
  exception with full traceback). Helps diagnose silent setup failures.

## 0.2.3 — 2026-07-13

### Fixed
- **Config flow raised `TypeError: 'SystemInfo' object is not subscriptable`** on
  every successful login. The config flow treated `get_system_info()`'s return
  value as a dict (`info["SerialNum"]`) but it is a `SystemInfo` dataclass.
  Use `info.serial_number` instead.

## 0.2.2 — 2026-07-13

### Fixed
- **Config flow showed "Unknown error" instead of "Failed to connect" for
  unreachable hosts.** aiohttp raises `aiohttp.ClientError` subclasses
  (`ConnectionTimeoutError`, `ClientConnectorError`, etc.) on network
  failures. The config flow only caught `HitronConnectionError`, so any
  aiohttp client error escaped as an unhandled exception. Now `login()`
  and `_request_json()` translate aiohttp client errors and malformed
  payloads into `HitronConnectionError` at the API boundary.

## 0.2.1 — 2026-07-13

### Fixed
- **Config-flow login accepted wrong passwords as valid** (`HitronCodaAPI.login`).
  The CODA-5610Q returns `{"errCode": "000", "result": "Error_Password_Wrong"}`
  for bad credentials — `errCode` alone is not a reliable success indicator.
  The fix also reads the `PHPSESSID` cookie from the response `Set-Cookie`
  header instead of the session jar (which aiohttp does not populate for
  `FormData` POSTs in the default configuration). Without this fix the
  config flow would silently "succeed" login with any password and then
  fail on the next request with a misleading "cannot connect" error.
- Added regression test `test_login_fails_wrong_password_real_response`
  using the actual router response shape.

## 0.2.0 — 2026-07-13

### Added
- Real device-list endpoint (`GET /1/Device/Hosts/1`) — no longer a placeholder
- WiFi client info: RSSI, band, SSID, bitrate, channel per device
- DOCSIS downstream channels: SNR, power, frequency, modulation per channel
- DOCSIS upstream channels: power, frequency, modulation per channel
- DOCSIS provisioning status: 7 binary sensors (hwInit, ranging, dhcp, etc.)
- Cable modem system info: CM IP, data rates, lease
- WiFi radio status: 2.4G / 5G on/off binary sensors
- Ethernet port link status: per-port binary sensors
- Firewall level sensor and enabled binary sensor
- Pause/resume buttons per device (POST /1/Device/Hosts/Pause with CSRF)
- DHCP reservations in diagnostics
- 12 router-level sensors (WAN/LAN uptime, traffic, IP, data rates, firewall)
- Test fixture from real router response (23 devices)

### Changed
- API client uses `_request_json` helper with X-Requested-With + Referer headers
- Coordinator fetches 12 endpoints in parallel via asyncio.gather
- Device tracker enriched with WiFi client data (RSSI, SSID, bitrate)
- Diagnostics dump includes all new data

## 0.1.0 — 2026-07-12

- Initial scaffold
- API client with login, get_connected_devices (placeholder), get_system_info
- DataUpdateCoordinator
- Device tracker platform
- Config flow with host/username/password
- Diagnostics support
- GitHub Actions CI (pytest, hassfest, HACS validation)
- Tests for login success, login failure, system info fetch
- Translations (strings.json + en.json)
- HACS metadata (hacs.json with country=CA)