"""Diagnostics support — dump coordinator data for debugging."""
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
        "system_info": {
            "serial_number": data.system_info.serial_number,
            "model_name": data.system_info.model_name,
            "software_version": data.system_info.software_version,
            "hardware_version": data.system_info.hardware_version,
            "api_version": data.system_info.api_version,
            "deployment_name": data.system_info.deployment_name,
        },
        "router_sys_info": data.router_sys_info,
        "devices": [
            {
                "hostname": d.hostname,
                "ip_address": d.ip_address,
                "mac_address": d.mac_address,
                "interface": d.interface,
                "address_source": d.address_source,
                "status": d.status,
                "action": d.action,
            }
            for d in data.devices
        ],
        "downstream_channels": [
            {
                "channel_id": ch.channel_id,
                "frequency": ch.frequency,
                "modulation": ch.modulation,
                "signal_strength": ch.signal_strength,
                "snr": ch.snr,
                "correcteds": ch.correcteds,
                "uncorrectables": ch.uncorrectables,
            }
            for ch in data.downstream_channels
        ],
        "upstream_channels": [
            {
                "channel_id": ch.channel_id,
                "frequency": ch.frequency,
                "modulation_type": ch.modulation_type,
                "signal_strength": ch.signal_strength,
                "bandwidth": ch.bandwidth,
            }
            for ch in data.upstream_channels
        ],
        "dhcp_reservations": data.dhcp_reservations,
    }