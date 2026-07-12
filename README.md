# Hitron CODA-5610Q — Home Assistant Integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for the
**Rogers / Fizz Hitron CODA-5610Q** DOCSIS 3.1 cable modem / WiFi 6 router.

The only integration that works with the CODA-5610Q. The built-in
`hitron_coda` integration targets the older CODA-4582U and does not work
with this model.

## Features

### Device Tracker
- One `device_tracker` entity per connected device (MAC-based)
- WiFi RSSI, band, SSID, bitrate, channel as extra attributes
- Source type: `router`
- Pause/resume buttons per device

### Sensors
- **Connected devices count** — total devices on LAN
- **WAN/LAN uptime** — seconds
- **WAN/LAN traffic** — RX/TX bytes
- **WAN IP address** — current public IP
- **Cable Modem IP** — CM-side IP from CMTS
- **Firewall level** — current security level
- **DOCSIS data rates** — downstream/upstream rates
- **Per-downstream-channel SNR** — dB per channel
- **Per-downstream-channel power** — dBmV per channel
- **Per-upstream-channel power** — dBmV per channel

### Binary Sensors
- **DOCSIS provisioning steps** — hwInit, findDownstream, ranging, dhcp, timeOfday, downloadCfg, registration
- **Network access** — whether the CMTS permits traffic
- **Firewall enabled** — on/off
- **WiFi radio on/off** — per band (2.4G / 5G)
- **Ethernet port link** — per port (with WAN port identified)

### Buttons
- **Pause** / **Resume** per device — control internet access per MAC

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

## Development

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```

## License

MIT