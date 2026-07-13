# Changelog

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