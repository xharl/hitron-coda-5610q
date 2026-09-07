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
from .const import (
    CONF_PRESENCE_GRACE,
    DEFAULT_PRESENCE_GRACE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_MIGRATE_TO_V0_2_13,
)
from .coordinator import HitronCodaCoordinator
from .device_tracker import register_services as _register_dt_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    # v0.2.14: removed Platform.BUTTON. The pause/resume buttons were
    # dropped from the integration, and an empty button.py module
    # would cause HA to log "module has no attribute async_setup_entry"
    # on every startup. The cleanest fix is to remove the platform
    # entirely so HA doesn't try to set it up.
    Platform.BINARY_SENSOR,
]

# No YAML config — config flow only
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hitron CODA-5610Q from a config entry."""
    _LOGGER.debug(
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
        _LOGGER.debug("hitron_coda_5610q: logging in")
        # v0.3.2: explicit login BEFORE the first data fetch. The CODA
        # answers a cookie-less request with the SPA login page (HTML),
        # never 401/403 — verified live + browser HAR. Without this,
        # boot-time setup ran with no session: every endpoint returned
        # HTML-as-degraded, the device list stayed empty, and the whole
        # integration wedged until the next restart.
        await api.login()
        _LOGGER.debug("hitron_coda_5610q: starting first refresh")
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug("hitron_coda_5610q: first refresh OK")

        # v0.3.0: presence hysteresis config rides on the coordinator
        try:
            grace = max(0, int(entry.options.get(CONF_PRESENCE_GRACE, DEFAULT_PRESENCE_GRACE)))
        except (TypeError, ValueError):
            grace = DEFAULT_PRESENCE_GRACE
        coordinator.presence_grace = grace  # type: ignore[attr-defined]

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

        # v0.2.13: register the one-shot migration service once per
        # integration load. The service walks the entity_registry and
        # renames MAC-keyed device_tracker unique_ids to hostname-keyed
        # ones, then reloads the config entry.
        _register_dt_services(hass)

        _LOGGER.debug("hitron_coda_5610q: forwarding setups to %s", PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("hitron_coda_5610q: forwards OK")

        # v0.3.2: start the explicit polling loop. This replaces the
        # v0.2.x-era double _schedule_refresh() hack: the parent's
        # reschedule is listener-gated and the timer chain died twice in
        # production (2026-09-07), freezing every entity. The owned loop
        # refreshes on the fast cadence and sends the router keepalive,
        # and survives listenerless windows by construction.
        coordinator.start_polling()

        entry.async_on_unload(entry.add_update_listener(_async_update_listener))

        # v0.3.2: stop the poll loop when the entry is unloaded; the
        # session closes when HA itself shuts down.
        entry.async_on_unload(coordinator.stop_polling)

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
        # v0.3.2: stop + await the explicit poll loop before dropping
        # the coordinator, so no cycle can run against a torn-down hass.
        coordinator = hass.data[DOMAIN].get(entry.entry_id)
        if coordinator is not None:
            coordinator.stop_polling()
            poll_task = coordinator._poll_task
            if poll_task is not None:
                try:
                    await asyncio.wait_for(poll_task, timeout=5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # v0.3.0: drop the live tracker-instance cache on unload. On a
        # config-entry RELOAD (options change, file redeploy + reload) the
        # old instances belong to the unloaded coordinator; keeping them
        # made the re-setup see them as "existing" and add nothing, so
        # every tracker stayed bound to the dead coordinator → all
        # unavailable. Fresh setup re-creates them from the sticky store.
        hass.data[DOMAIN].pop(f"{entry.entry_id}_trackers", None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)