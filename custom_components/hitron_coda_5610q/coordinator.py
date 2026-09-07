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

v0.3.1: graceful DOCSIS degradation.

- The modem firmware intermittently serves the SPA login page (HTML)
  instead of JSON on the /1/Device/CM/ endpoints while Login and the
  host list keep working; re-login does not clear it (verified live).
  A slow-tier endpoint failing with HitronEndpointDegradedError no
  longer fails the whole update: the field keeps its previous value
  and the coordinator tracks the degraded endpoints plus the start of
  the degradation window, so the docsis_data_ok binary sensor and the
  sensors' docsis_stale/last_good attributes can report it. Any other
  failure class keeps the v0.3.0 semantics unchanged — including mixed
  cycles where one endpoint is degraded and another hard-fails.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    ConfigEntryAuthFailed,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import (
    ConnectedDevice,
    DownstreamChannel,
    HitronAuthError,
    HitronConnectionError,
    HitronCodaAPI,
    HitronEndpointDegradedError,
    SystemInfo,
    UpstreamChannel,
)
from .const import (
    CONF_FAST_INTERVAL,
    CONF_SLOW_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOCSIS_ENDPOINT_FIELDS,
    DOMAIN,
    MODEL,
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

    # Slow-tier fetch plan: HitronCodaData field name -> API method name.
    # The order defines the gather order and therefore the order of the
    # per-endpoint degradation report.
    _SLOW_FETCHERS: tuple[tuple[str, str], ...] = (
        ("system_info", "get_system_info"),
        ("router_sys_info", "get_router_sys_info"),
        ("downstream_channels", "get_downstream_channels"),
        ("upstream_channels", "get_upstream_channels"),
        ("dhcp_reservations", "get_dhcp_reservations"),
        ("docsis_provisioning", "get_docsis_provisioning"),
        ("cm_sys_info", "get_cm_sys_info"),
        ("wifi_radios", "get_wifi_radios"),
        ("firewall_status", "get_firewall_status"),
        ("ethernet_ports", "get_ethernet_ports"),
    )

    # v0.3.1: fallback factories for slow-tier fields that degrade
    # before any successful fetch (first refresh during an outage).
    # Empty containers keep the update alive — presence and the
    # docsis_data_ok binary sensor keep working — while the affected
    # sensors simply have nothing to show yet.
    _SLOW_EMPTY: dict[str, Callable[[], Any]] = {
        "system_info": lambda: SystemInfo(
            serial_number="",
            model_name=MODEL,
            hardware_version="",
            software_version="",
            api_version="",
            vendor_name="",
            device_id="",
            deployment_name="",
            wifi_chip="",
        ),
        "router_sys_info": dict,
        "downstream_channels": list,
        "upstream_channels": list,
        "dhcp_reservations": list,
        "docsis_provisioning": dict,
        "cm_sys_info": dict,
        "wifi_radios": list,
        "firewall_status": dict,
        "ethernet_ports": list,
    }

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

        # v0.3.1: per-endpoint degradation tracking. ``_degraded`` maps
        # HitronCodaData field names to the endpoint path currently
        # serving HTML instead of JSON; ``_degraded_since`` marks the
        # start of the continuous degradation window (reported by the
        # docsis_data_ok binary sensor); ``_field_good_at`` remembers
        # when each slow-tier field was last fetched successfully so
        # sensors can expose a last_good timestamp.
        self._degraded: dict[str, str] = {}
        self._degraded_since: datetime | None = None
        self._field_good_at: dict[str, datetime] = {}

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

        prev = self._last_good

        # ---- fast tier (always) ----
        # Presence runs first and on its own wave: its two requests fit
        # the semaphore in one pass, so trackers see the freshest host
        # list before the slow tier starts.
        try:
            # v0.3.1: fast tier gets the same per-endpoint degradation
            # tolerance as the slow tier — a total firmware degradation
            # (host list included) must not crash setup/updates; the
            # trackers' presence grace window covers the gap.
            fast_results = await asyncio.gather(
                _bounded(self.api.get_connected_devices()),
                _bounded(self.api.get_wifi_clients()),
                return_exceptions=True,
            )
            _fast_degraded: dict[str, str] = {}
            _fast_values: dict[str, Any] = {}
            _fast_first_err: BaseException | None = None
            for _fname, _res in zip(
                ("devices", "wifi_clients"), fast_results
            ):
                if isinstance(_res, HitronEndpointDegradedError):
                    _fast_degraded[_fname] = _res.endpoint
                    _fast_values[_fname] = (
                        getattr(prev, _fname) if prev is not None else []
                    )
                elif isinstance(_res, BaseException):
                    if _fast_first_err is None:
                        _fast_first_err = _res
                else:
                    _fast_values[_fname] = _res
            if _fast_degraded:
                self._apply_degraded_state(_fast_degraded, plane="fast")
            elif self._degraded:
                # fast tier was fully probed this cycle; clear its entries
                self._apply_degraded_state(
                    {k: v for k, v in self._degraded.items()
                     if k not in self._FAST_FIELDS},
                    plane="fast",
                )
            if _fast_first_err is not None:
                raise _fast_first_err
            devices = _fast_values["devices"]
            wifi_clients = _fast_values["wifi_clients"]

            # ---- slow tier ----
            # The first cycle always does a full fetch (nothing to reuse).
            if slow_due or prev is None:
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
                ) = await self._async_fetch_slow_tier(
                    prev,
                    {
                        field: _bounded(getattr(self.api, method)())
                        for field, method in self._SLOW_FETCHERS
                    },
                )
            else:
                # Reuse last slow-tier data
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

    async def _async_fetch_slow_tier(
        self,
        prev: HitronCodaData | None,
        fetches: dict[str, Coroutine[Any, Any, Any]],
    ) -> tuple[Any, ...]:
        """Run the slow-tier fetches with per-endpoint degradation tolerance.

        v0.3.1: a ``HitronEndpointDegradedError`` (firmware serving the
        SPA login page instead of JSON) is absorbed — the field falls
        back to its previous value, or to an empty container when no
        previous value exists yet, and the endpoint is recorded as
        degraded. Every other failure is re-raised after the degradation
        state is updated, so the caller's existing last-good window and
        auth handling apply unchanged: mixed cycles (one endpoint
        degraded, another hard-failing) keep the v0.3.0 failure
        semantics for the non-degraded part.
        """
        results = await asyncio.gather(*fetches.values(), return_exceptions=True)
        now = dt_util.utcnow()
        values: dict[str, Any] = {}
        degraded: dict[str, str] = {}
        first_error: BaseException | None = None
        for field, result in zip(fetches, results):
            if isinstance(result, HitronEndpointDegradedError):
                degraded[field] = result.endpoint
                values[field] = (
                    getattr(prev, field)
                    if prev is not None
                    else self._SLOW_EMPTY[field]()
                )
            elif isinstance(result, BaseException):
                if first_error is None:
                    first_error = result
            else:
                values[field] = result
                self._field_good_at[field] = now
        self._apply_degraded_state(degraded)
        if first_error is not None:
            raise first_error
        return tuple(values[field] for field in fetches)

    _FAST_FIELDS = frozenset({"devices", "wifi_clients"})

    def _apply_degraded_state(
        self, degraded: dict[str, str], plane: str = "slow"
    ) -> None:
        """Record the degradation observed by a finished fetch wave.

        ``degraded`` maps HitronCodaData field names to the endpoint
        path that served HTML. Fast-tier endpoints are re-probed every
        cycle, slow-tier ones only on slow cycles — so each plane's
        entries are authoritative for that plane and merges never
        clobber the other plane's state. ``_degraded_since`` marks the
        START of the continuous degradation window and is only cleared
        when a plane comes back fully healthy on a cycle that probed
        it.
        """
        fresh = dict(self._degraded)
        if plane == "fast":
            fresh = {
                k: v for k, v in fresh.items() if k not in self._FAST_FIELDS
            }
        if degraded:
            if not self._degraded:
                self._degraded_since = dt_util.utcnow()
            fresh.update(degraded)
            self._degraded = fresh
        else:
            probed = (
                self._FAST_FIELDS
                if plane == "fast"
                else {f for f, _ in self._SLOW_FETCHERS}
            )
            remaining = {
                k: v
                for k, v in fresh.items()
                if k not in probed
            }
            self._degraded = remaining
            if not remaining:
                self._degraded_since = None

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

    # ---- v0.3.1: degradation reporting surface ----

    @property
    def degraded_endpoints(self) -> list[str]:
        """All endpoint paths currently serving HTML instead of JSON."""
        return list(self._degraded.values())

    @property
    def degraded_since(self) -> datetime | None:
        """Start of the current degradation window (None = healthy)."""
        return self._degraded_since

    @property
    def docsis_degraded_endpoints(self) -> list[str]:
        """Degraded endpoints belonging to the modem's DOCSIS plane.

        Filters the full degraded set down to the /1/Device/CM/ family
        (see DOCSIS_ENDPOINT_FIELDS) so a degraded host list — a
        different problem with different remediation — does not trip
        the docsis_data_ok binary sensor.
        """
        return [
            endpoint
            for field, endpoint in self._degraded.items()
            if field in DOCSIS_ENDPOINT_FIELDS
        ]

    @property
    def docsis_degraded(self) -> bool:
        """True while one or more DOCSIS endpoints serve degraded data."""
        return bool(self.docsis_degraded_endpoints)

    def is_degraded(self, field: str) -> bool:
        """True when the field is being served from the last-good window."""
        return field in self._degraded

    def field_last_good(self, field: str) -> datetime | None:
        """When the field was last fetched successfully (None = never)."""
        return self._field_good_at.get(field)