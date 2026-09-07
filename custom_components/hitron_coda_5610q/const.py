"""Constants for the Hitron CODA-5610Q integration."""
from datetime import timedelta

DOMAIN = "hitron_coda_5610q"
MANUFACTURER = "Hitron Technologies"
MODEL = "CODA-5610Q"

DEFAULT_USERNAME = "cusadmin"
DEFAULT_PORT = 80

# Don't hammer the router. The web UI itself takes 1-3s per page load.
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
MIN_SCAN_INTERVAL = timedelta(seconds=10)
MAX_SCAN_INTERVAL = timedelta(minutes=5)

# Config-entry data keys
CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"

# v0.2.14: when True, expose the per-channel DOCSIS power/SNR sensors
# (32 sensors on a healthy cable plant: 16 DS power + 16 DS SNR). These
# are useful for diagnosing cable plant issues but most users don't
# want them in their UI. Default False keeps the integration polite.
CONF_EXPOSE_DIAGNOSTICS = "expose_diagnostics"

# Device tracker identity strategy. v0.2.13+ default to hostname so
# devices with rotating MACs (iOS Private WiFi Address, Android 10+
# randomized MAC) get a stable entity_id.
TRACK_BY_MAC = "mac"
TRACK_BY_HOSTNAME = "hostname"
DEFAULT_TRACK_BY = TRACK_BY_HOSTNAME
CONF_TRACK_BY = "track_by"

# Service names
SERVICE_MIGRATE_TO_V0_2_13 = "migrate_to_v0_2_13"

# New in v0.2.15: optional mDNS discovery to find friendlier names
# for devices that don't advertise hostnames to the router via DHCP.
CONF_ENABLE_MDNS = "enable_mdns"
# New in v0.2.15: user-defined aliases for MAC or current identity.
CONF_DEVICE_ALIASES = "device_aliases"
# New in v0.2.15: when True, include the device OUI/manufacturer in the
# display name for still-unidentified devices.
CONF_USE_OUI_LABEL = "use_oui_label"

# v0.3.0: presence hysteresis + tiered polling.
# CONF_PRESENCE_GRACE: seconds to keep reporting "home" after a device
# last appeared in the router's host list. This absorbs the CODA's
# transient host-list drops (WiFi power-save, band steering, empty-body
# hiccups) that caused instant not_home flapping.
CONF_PRESENCE_GRACE = "presence_grace_seconds"
DEFAULT_PRESENCE_GRACE = 240

# v0.3.0: tiered polling. The host list (the only time-critical endpoint)
# polls at fast_interval; DOCSIS/diagnostic endpoints poll at
# slow_interval, cutting total request volume ~3x.
CONF_FAST_INTERVAL = "fast_interval"
DEFAULT_FAST_INTERVAL = 30
CONF_SLOW_INTERVAL = "slow_interval"
DEFAULT_SLOW_INTERVAL = 300

# v0.3.1: graceful DOCSIS degradation. The modem firmware intermittently
# stops serving DOCSIS data: the /1/Device/CM/ endpoints answer HTTP 200
# with the SPA login page (HTML) instead of JSON, while Login and the
# host list keep working and re-login does NOT clear the condition.
# These HitronCodaData field names map to endpoints under /1/Device/CM/ —
# the modem's DOCSIS data plane. While any of them is degraded the
# coordinator keeps serving the last-good values and the docsis_data_ok
# binary sensor turns off, so the user can automate a "modem needs a
# reboot" notification.
DOCSIS_ENDPOINT_FIELDS = (
    "system_info",           # GET /1/Device/CM/Version
    "downstream_channels",   # GET /1/Device/CM/DsInfo
    "upstream_channels",     # GET /1/Device/CM/UsInfo
    "docsis_provisioning",   # GET /1/Device/CM/DocsisProvision
    "cm_sys_info",           # GET /1/Device/CM/SysInfo
)