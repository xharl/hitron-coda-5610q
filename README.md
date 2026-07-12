# Hitron CODA-5610Q — Home Assistant Integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for the
**Rogers / Fizz Hitron CODA-5610Q** DOCSIS 3.1 cable modem / WiFi 6 router.

This is the only integration that works with the CODA-5610Q. The built-in
`hitron_coda` integration targets the older CODA-4582U and does not work
with this model.

## Features

- **Device tracker** — presence detection via WiFi-MAC tracking (one entity
  per connected device, `SourceType.ROUTER`)
- **DOCSIS diagnostics** *(planned)* — downstream SNR, upstream power,
  channel frequencies
- **Reboot button** *(planned)* — reboot the router from HA
- **Pause/resume client** *(planned)* — pause a device's internet access

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
| Password | — | Router admin password (usually the WiFi password) |

## Supported devices

- Hitron CODA-5610Q (hardware rev 1A, firmware 7.3.5.1.2b22, API 1.12.1)
- Rogers Canada / Fizz Canada deployments

## Troubleshooting

- **"Invalid password"** — the CODA-5610Q uses the WiFi password as the admin
  password (not a separate web admin password like older models)
- **Devices not appearing** — the router's connected device list takes a few
  seconds to load. The integration polls every 30 seconds by default
- **Connection drops** — the PHPSESSID cookie can expire. The integration
  re-logs in automatically on 401/403

## Development

```bash
pip install -r requirements_test.txt
pytest tests/ -v
```

## License

MIT