"""DataUpdateCoordinator for the Hitron CODA-5610Q."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
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
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


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
        # IMPORTANT: assign self.config_entry BEFORE super().__init__()
        # so the update_interval setter can read it. Then, after
        # super().__init__() returns, schedule the first refresh
        # EXPLICITLY — the DataUpdateCoordinator only schedules the
        # periodic update loop at the END of _async_refresh(), and
        # only if self._listeners is non-empty. If no entities have
        # called async_add_listener() yet (which is common at setup
        # time, before the entity platforms have been forwarded),
        # the periodic update never starts and the coordinator
        # only runs the initial first_refresh once.
        self.config_entry = config_entry
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self.api = api
        # Register the periodic update loop. The first call inside
        # __init__ may no-op if self._listeners is empty, but
        # _schedule_refresh is safe to call multiple times — it
        # cancels any prior schedule before installing a new one.
        # The platform's async_setup_entry calls
        # async_forward_entry_setups() which sets up entities that
        # register themselves as listeners; we re-schedule after
        # that to make sure the loop is in place once listeners
        # are registered. See __init__.py.
        self._schedule_refresh()

    async def _async_update_data(self) -> HitronCodaData:
        # The CODA-5610Q's web server cannot reliably handle 12
        # parallel requests — it returns empty/malformed bodies when
        # overloaded. Use a semaphore to cap concurrency at 3, which
        # empirically keeps the router happy. Combined with the retry
        # logic in _request_json, this gives a near-100% success rate.
        sem = asyncio.Semaphore(3)

        async def _bounded(coro):
            async with sem:
                return await coro

        try:
            (
                devices,
                system_info,
                router_sys_info,
                ds_channels,
                us_channels,
                reservations,
                wifi_clients,
                docs_prov,
                cm_sys,
                wifi_radios,
                firewall,
                eth_ports,
            ) = await asyncio.gather(
                _bounded(self.api.get_connected_devices()),
                _bounded(self.api.get_system_info()),
                _bounded(self.api.get_router_sys_info()),
                _bounded(self.api.get_downstream_channels()),
                _bounded(self.api.get_upstream_channels()),
                _bounded(self.api.get_dhcp_reservations()),
                _bounded(self.api.get_wifi_clients()),
                _bounded(self.api.get_docsis_provisioning()),
                _bounded(self.api.get_cm_sys_info()),
                _bounded(self.api.get_wifi_radios()),
                _bounded(self.api.get_firewall_status()),
                _bounded(self.api.get_ethernet_ports()),
            )
        except HitronAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except HitronConnectionError as err:
            raise UpdateFailed(f"Router error: {err}") from err

        return HitronCodaData(
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