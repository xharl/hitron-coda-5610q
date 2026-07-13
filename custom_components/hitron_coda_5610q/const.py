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