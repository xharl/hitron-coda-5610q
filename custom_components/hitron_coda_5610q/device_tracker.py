"""Device tracker for the Hitron CODA-5610Q."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
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
            identifiers={(DOMAIN, self.coordinator.data.system_info.serial_number)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
            sw_version=self.coordinator.data.system_info.software_version,
            hw_version=self.coordinator.data.system_info.hardware_version,
        )

    @property
    def state(self) -> str:
        # TrackerEntity.state (in HA 2026.x) returns None unless the
        # entity has a location_name or a configured zone. For
        # router-based tracking (no GPS, no zones), we have to override
        # state ourselves to return STATE_HOME / STATE_NOT_HOME based
        # on the device's Active/Paused status from the router.
        return STATE_HOME if self.is_connected else STATE_NOT_HOME

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
                return d.status
        return False

    @property
    def ip_address(self) -> str | None:
        for d in self.coordinator.data.devices:
            if d.mac_address == self._mac:
                return d.ip_address
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return extra attributes for the device tracker."""
        attrs: dict[str, str] = {}
        for d in self.coordinator.data.devices:
            if d.mac_address == self._mac:
                attrs["interface"] = d.interface
                attrs["address_source"] = d.address_source
                attrs["action"] = d.action
                break
        # Enrich with WiFi client info (RSSI, channel, bitrate) if available
        for wc in self.coordinator.data.wifi_clients:
            if wc.get("mac_address") == self._mac:
                attrs["rssi"] = wc["rssi"]
                attrs["wifi_band"] = wc["band"]
                attrs["wifi_ssid"] = wc["ssid"]
                attrs["wifi_bitrate"] = wc["bitrate"]
                attrs["wifi_channel"] = wc["channel"]
                break
        return attrs