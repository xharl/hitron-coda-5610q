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