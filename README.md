# Hitron CODA-5610Q — Home Assistant Integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for the
**Rogers / Fizz Hitron CODA-5610Q** DOCSIS 3.1 cable modem / WiFi 6 router.

The only integration that works with the CODA-5610Q. The built-in
`hitron_coda` integration targets the older CODA-4582U and does not work
with this model.

## Features

### Device Tracker
- One `device_tracker` entity per connected device
- **Hostname-based identity** (v0.2.13+) — survives MAC rotation
  (iOS Private WiFi Address, Android 10+ randomized MACs) so a phone
  that rotates its MAC every few hours keeps a stable entity_id
- Per-device WiFi band, SSID, bitrate, channel, signal strength as
  extra attributes (when the router reports them)
- Source type: `router`

### Sensors
**Always exposed (12 entities):**
- **Connected devices count** — total devices on LAN
- **WAN uptime** — seconds
- **LAN uptime** — seconds
- **WAN traffic** — RX / TX bytes (lifetime)
- **LAN traffic** — RX / TX bytes (lifetime)
- **WAN IP address** — current public IP
- **Cable Modem IP** — CM-side IP from CMTS
- **Firewall level** — current security level
- **Downstream data rate** — bonded DOCSIS rate
- **Upstream data rate** — bonded DOCSIS rate

**Opt-in via `Expose diagnostics` (32+ entities, default off):**
- **Per-downstream-channel SNR** — dB per channel
- **Per-downstream-channel power** — dBmV per channel
- **Per-upstream-channel power** — dBmV per channel

These are useful for diagnosing cable plant issues (degraded SNR,
modem upstreams that are too hot) but most users don't want 32
extra sensors in their UI. Toggle on in the integration's
**Options** panel when you need them.

### Binary Sensors
- **Network access** — whether the CMTS permits traffic
- **Firewall enabled** — on/off
- **WiFi radio on/off** — per band (2.4G / 5G)
- **Ethernet port link** — per port (with WAN port identified)

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations in your HA instance
2. Click ⋮ → **Custom repositories**
3. Paste this repo's GitHub URL → Category: **Integration**
4. Install
5. Restart Home Assistant
6. Settings → Devices & Services → Add Integration → search "Hitron CODA-5610Q"

### Manual

Copy the `custom_components/hitron_coda_5610q/` directory into your
`custom_components/` folder, then restart HA.

## Configuration

| Field | Default | Description |
|-------|---------|-------------|
| Host | — | Router IP address (usually `192.168.0.1`) |
| Username | `cusadmin` | Router admin username |
| Password | — | Router admin password (often the WiFi password) |

### Options

| Field | Default | Description |
|-------|---------|-------------|
| Expose diagnostics | `false` | When true, create per-channel DOCSIS power/SNR sensors (32+ entities). Useful for cable plant troubleshooting. |

## Supported devices

- Hitron CODA-5610Q (hardware rev 1A, firmware 7.3.5.1.2b22, API 1.12.1)
- Rogers Canada / Fizz Canada deployments

## Troubleshooting

- **"Invalid password"** — the CODA-5610Q uses the WiFi password as the admin
  password on ISP-locked units
- **Devices not appearing** — the integration polls every 30 seconds; the
  router's device list takes 1-3 seconds to respond
- **Connection drops** — the PHPSESSID cookie can expire. The integration
  re-logs in automatically on 401/403
- **RSSI not showing** — only WiFi-connected clients have RSSI; Ethernet
  devices don't have this attribute
- **"Person" entity oscillating between home / not_home** — common with
  iOS Private WiFi Address and Android 10+ randomized MACs. As of
  v0.2.13 the device_tracker uses hostname-based identity so the
  entity stays the same across MAC rotations. The router-side MAC
  attribute (`current_mac` in the entity's `extra_state_attributes`)
  will change, but the entity_id and `person.*` state should be
  stable. If you still see drift, the Leave Home automation in
  `/config/automations.yaml` has a 5-minute delay to absorb
  short-term flapping — see the migration guide for the v0.2.13
  service that walks the entity registry.

## Development

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```

## License

MIT
