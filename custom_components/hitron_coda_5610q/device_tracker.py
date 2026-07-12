"""Device tracker for the Hitron CODA-5610Q."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import HitronCodaCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker entities."""
    coordinator: HitronCodaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        HitronCodaDeviceTracker(coordinator, mac)
        for mac in {d.mac_address for d in coordinator.data.devices}
    )


class HitronCodaDeviceTracker(CoordinatorEntity[HitronCodaCoordinator], TrackerEntity):
    """A device seen on the LAN."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.ROUTER

    def __init__(self, coordinator: HitronCodaCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{DOMAIN}_{mac}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "router")},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
        )

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def hostname(self) -> str | None:
        for d in self.coordinator.data.devices:
            if d.mac_address == self._mac:
                return d.hostname
        return None

    @property
    def is_connected(self) -> bool:
        for d in self.coordinator.data.devices:
            if d.mac_address == self._mac:
                return d.status == "Active"
        return False

    @property
    def ip_address(self) -> str | None:
        for d in self.coordinator.data.devices:
            if d.mac_address == self._mac:
                return d.ip_address
        return None