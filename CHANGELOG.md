# Changelog

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