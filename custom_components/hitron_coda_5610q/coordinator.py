"""DataUpdateCoordinator for the Hitron CODA-5610Q."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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
    HitronAuthError,
    HitronConnectionError,
    HitronCodaAPI,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class HitronCodaData:
    devices: list[ConnectedDevice]
    sys_info: dict


class HitronCodaCoordinator(DataUpdateCoordinator[HitronCodaData]):
    """Single coordinator shared by all entity platforms."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: HitronCodaAPI,
        scan_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
        )
        self.api = api
        self._failed_auth = False

    async def _async_update_data(self) -> HitronCodaData:
        try:
            devices, sys_info = await asyncio.gather(
                self.api.get_connected_devices(),
                self.api.get_system_info(),
            )
        except HitronAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except HitronConnectionError as err:
            raise UpdateFailed(f"Router error: {err}") from err

        self._failed_auth = False
        return HitronCodaData(devices=devices, sys_info=sys_info)