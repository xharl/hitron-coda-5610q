# Changelog

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