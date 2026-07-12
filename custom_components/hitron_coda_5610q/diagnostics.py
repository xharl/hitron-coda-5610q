"""Diagnostics support — dump coordinator data for debugging.

This bumps the integration from Bronze to Silver on the HA
quality scale. Returns the latest coordinator.data as JSON when
the user clicks "Download diagnostics" in HA.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import HitronCodaCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: HitronCodaCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    return {
        "entry": entry.as_dict(),
        "devices": [
            {
                "hostname": d.hostname,
                "ip_address": d.ip_address,
                "mac_address": d.mac_address,
                "interface": d.interface,
                "status": d.status,
            }
            for d in data.devices
        ],
        "sys_info": data.sys_info,
    }