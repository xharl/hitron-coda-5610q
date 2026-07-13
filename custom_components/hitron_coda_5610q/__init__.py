"""The Hitron CODA-5610Q integration."""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api import HitronCodaAPI
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .coordinator import HitronCodaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
]

# No YAML config — config flow only
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hitron CODA-5610Q from a config entry."""
    _LOGGER.warning(
        "hitron_coda_5610q.async_setup_entry START entry_id=%s data_keys=%s",
        entry.entry_id,
        list(entry.data.keys()),
    )
    # Use a fresh aiohttp.ClientSession per entry instead of HA's shared
    # session. The shared session (from async_get_clientsession) merges
    # cookies from other integrations and HA's own state, which can
    # cause the router to return an HTML login page instead of JSON
    # when our PHPSESSID is sent alongside stale session cookies.
    session = aiohttp.ClientSession()
    try:
        api = HitronCodaAPI(
            session,
            entry.data["host"],
            entry.data["username"],
            entry.data["password"],
        )

        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )

        coordinator = HitronCodaCoordinator(hass, entry, api, scan_interval)
        _LOGGER.warning("hitron_coda_5610q: starting first refresh")
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.warning("hitron_coda_5610q: first refresh OK")

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

        _LOGGER.warning("hitron_coda_5610q: forwarding setups to %s", PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.warning("hitron_coda_5610q: forwards OK")

        # The DataUpdateCoordinator only schedules its periodic update
        # loop in _async_refresh's finally block, AND only if
        # self._listeners is non-empty. At this point (after
        # async_forward_entry_setups), entity platforms have registered
        # their listeners — so trigger an explicit schedule to make
        # sure the loop is now in place. This is the v0.2.10 fix for
        # "entities exist but never update".
        coordinator._schedule_refresh()

        entry.async_on_unload(entry.add_update_listener(_async_update_listener))

        # Close the session when the entry is unloaded
        async def _close_session(event):
            await session.close()
        entry.async_on_unload(
            hass.bus.async_listen_once(
                f"homeassistant_close", _close_session
            )
        )
        return True
    except Exception as err:
        await session.close()
        _LOGGER.exception("hitron_coda_5610q.async_setup_entry FAILED: %s", err)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)