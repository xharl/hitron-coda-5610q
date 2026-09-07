"""DataUpdateCoordinator for the Hitron CODA-5610Q.

v0.3.0: tiered polling + single-flight login + last-good data.

- Tiered polling: the host list and WiFi clients (the only endpoints that
  affect presence) poll every fast cycle. DOCSIS/system/diagnostic
  endpoints poll every slow cycle (default 5x fewer requests). The
  CODA-5610Q's web server cannot reliably handle bursts — fewer requests
  per cycle means fewer empty-body failures.
- Single-flight login: concurrent re-auth attempts against the PHP
  backend invalidate each other's PHPSESSID (fresh session per login).
  A login lock ensures only one login() runs at a time and repeats are
  no-ops while a fresh session exists.
- Last-good data: on update failure the previous snapshot is served for
  up to _LAST_GOOD_CYCLES fast cycles before the entities are marked
  unavailable. A single transient router hiccup no longer flips all 47
  trackers to unavailable/not_home.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    ConfigEntryAuthFailed,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    ConnectedDevice,
    DownstreamChannel,
    HitronAuthError,
    HitronConnectionError,
    HitronCodaAPI,
    SystemInfo,
    UpstreamChannel,
)
from .const import (
    CONF_FAST_INTERVAL,
    CONF_SLOW_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_LAST_GOOD_CYCLES = 3


@dataclass
class HitronCodaData:
    """Aggregated data from all router endpoints."""

    devices: list[ConnectedDevice]
    system_info: SystemInfo
    router_sys_info: dict
    downstream_channels: list[DownstreamChannel]
    upstream_channels: list[UpstreamChannel]
    dhcp_reservations: list[dict]
    wifi_clients: list[dict] = field(default_factory=list)
    docsis_provisioning: dict = field(default_factory=dict)
    cm_sys_info: dict = field(default_factory=dict)
    wifi_radios: list[dict] = field(default_factory=list)
    firewall_status: dict = field(default_factory=dict)
    ethernet_ports: list[dict] = field(default_factory=list)


class HitronCodaCoordinator(DataUpdateCoordinator[HitronCodaData]):
    """Single coordinator shared by all entity platforms."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: HitronCodaAPI,
        scan_interval: timedelta,
    ) -> None:
        # Assign self.config_entry BEFORE super().__init__() so the
        # update_interval setter and the per-config-entry async_on_unload
        # registration in the parent class both have it available.
        self.config_entry = config_entry
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self.api = api

        # v0.3.0: tiered polling configuration
        self._fast_interval = self._interval_opt(
            CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL, lo=10, hi=300
        )
        self._slow_interval = self._interval_opt(
            CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL, lo=60, hi=1800
        )
        self._slow_every = max(1, int(self._slow_interval / self._fast_interval))
        self._cycle = 0
        self._last_good: HitronCodaData | None = None
        self._last_good_age_cycles = 0

        # v0.3.0: single-flight login lock. The CODA issues a fresh
        # PHPSESSID on every login; two overlapping logins invalidate each
        # other's session and cause 401 storms under load.
        self._login_lock = asyncio.Lock()

        # Keep the legacy behavior: schedule the loop immediately so the
        # periodic refresh survives the listenerless first-refresh window.
        self._schedule_refresh()

    def _interval_opt(self, key: str, default: int, lo: int, hi: int) -> int:
        try:
            val = int(self.config_entry.options.get(key, default))
        except (TypeError, ValueError):
            val = default
        return max(lo, min(val, hi))

    def _tiered_interval(self) -> timedelta:
        """Effective update interval: fast tier cadence."""
        return timedelta(seconds=self._fast_interval)

    async def _async_update_data(self) -> HitronCodaData:
        self._cycle += 1
        slow_due = (self._cycle % self._slow_every) == 0

        # Semaphore(3): the router's web server returns empty/malformed
        # bodies under higher concurrency. Keep the cap even though the
        # tiered plan already cut per-cycle request count.
        sem = asyncio.Semaphore(3)

        async def _bounded(coro):
            async with sem:
                return await coro

        # ---- fast tier (always) ----
        try:
            devices_task = _bounded(self.api.get_connected_devices())
            wifi_clients_task = _bounded(self.api.get_wifi_clients())
            if slow_due:
                # ---- slow tier (every Nth cycle) ----
                (
                    devices,
                    wifi_clients,
                    system_info,
                    router_sys_info,
                    ds_channels,
                    us_channels,
                    reservations,
                    docs_prov,
                    cm_sys,
                    wifi_radios,
                    firewall,
                    eth_ports,
                ) = await asyncio.gather(
                    devices_task,
                    wifi_clients_task,
                    _bounded(self.api.get_system_info()),
                    _bounded(self.api.get_router_sys_info()),
                    _bounded(self.api.get_downstream_channels()),
                    _bounded(self.api.get_upstream_channels()),
                    _bounded(self.api.get_dhcp_reservations()),
                    _bounded(self.api.get_docsis_provisioning()),
                    _bounded(self.api.get_cm_sys_info()),
                    _bounded(self.api.get_wifi_radios()),
                    _bounded(self.api.get_firewall_status()),
                    _bounded(self.api.get_ethernet_ports()),
                )
            else:
                devices, wifi_clients = await asyncio.gather(
                    devices_task, wifi_clients_task
                )
                # Reuse last slow-tier data
                prev = self._last_good
                if prev is not None:
                    system_info = prev.system_info
                    router_sys_info = prev.router_sys_info
                    ds_channels = prev.downstream_channels
                    us_channels = prev.upstream_channels
                    reservations = prev.dhcp_reservations
                    docs_prov = prev.docsis_provisioning
                    cm_sys = prev.cm_sys_info
                    wifi_radios = prev.wifi_radios
                    firewall = prev.firewall_status
                    eth_ports = prev.ethernet_ports
                else:
                    # First cycle: do a full fetch so nothing is missing.
                    (
                        system_info,
                        router_sys_info,
                        ds_channels,
                        us_channels,
                        reservations,
                        docs_prov,
                        cm_sys,
                        wifi_radios,
                        firewall,
                        eth_ports,
                    ) = await asyncio.gather(
                        _bounded(self.api.get_system_info()),
                        _bounded(self.api.get_router_sys_info()),
                        _bounded(self.api.get_downstream_channels()),
                        _bounded(self.api.get_upstream_channels()),
                        _bounded(self.api.get_dhcp_reservations()),
                        _bounded(self.api.get_docsis_provisioning()),
                        _bounded(self.api.get_cm_sys_info()),
                        _bounded(self.api.get_wifi_radios()),
                        _bounded(self.api.get_firewall_status()),
                        _bounded(self.api.get_ethernet_ports()),
                    )
        except HitronAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except HitronConnectionError as err:
            # v0.3.0: serve last-good data briefly instead of dropping all
            # entities. After _LAST_GOOD_CYCLES consecutive failures, give
            # up and mark everything unavailable.
            if self._last_good is not None and self._last_good_age_cycles < _LAST_GOOD_CYCLES:
                self._last_good_age_cycles += 1
                _LOGGER.warning(
                    "hitron: router update failed (%s); serving last-good data (failure %d/%d)",
                    err, self._last_good_age_cycles, _LAST_GOOD_CYCLES,
                )
                return self._last_good
            raise UpdateFailed(f"Router error: {err}") from err

        data = HitronCodaData(
            devices=devices,
            system_info=system_info,
            router_sys_info=router_sys_info,
            downstream_channels=ds_channels,
            upstream_channels=us_channels,
            dhcp_reservations=reservations,
            wifi_clients=wifi_clients,
            docsis_provisioning=docs_prov,
            cm_sys_info=cm_sys,
            wifi_radios=wifi_radios,
            firewall_status=firewall,
            ethernet_ports=eth_ports,
        )
        self._last_good = data
        self._last_good_age_cycles = 0
        return data

    async def async_login_locked(self) -> None:
        """Re-authenticate under the single-flight lock."""
        async with self._login_lock:
            await self.api.login()

    @property
    def slow_interval(self) -> int:
        return self._slow_interval

    @property
    def fast_interval(self) -> int:
        return self._fast_interval