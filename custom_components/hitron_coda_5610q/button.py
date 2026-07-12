"""Button entities for the Hitron CODA-5610Q.

Provides per-device pause/resume buttons.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
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
    """Set up button entities for each connected device."""
    coordinator: HitronCodaCoordinator = hass.data[DOMAIN][entry.entry_id]

    buttons: list[ButtonEntity] = []
    for device in coordinator.data.devices:
        # Pause button
        buttons.append(
            HitronDeviceButton(
                coordinator,
                ButtonEntityDescription(
                    key=f"pause_{device.mac_address}",
                    name=f"Pause {device.hostname}",
                    icon="mdi:pause",
                ),
                device.mac_address,
                action="pause",
            )
        )
        # Resume button
        buttons.append(
            HitronDeviceButton(
                coordinator,
                ButtonEntityDescription(
                    key=f"resume_{device.mac_address}",
                    name=f"Resume {device.hostname}",
                    icon="mdi:play",
                ),
                device.mac_address,
                action="resume",
            )
        )

    async_add_entities(buttons)


class HitronDeviceButton(
    CoordinatorEntity[HitronCodaCoordinator], ButtonEntity
):
    """A button to pause/resume a device."""

    entity_description: ButtonEntityDescription

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        description: ButtonEntityDescription,
        mac_address: str,
        action: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._mac_address = mac_address
        self._action = action
        self._attr_unique_id = f"{DOMAIN}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.data.system_info.serial_number)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
        )

    @property
    def available(self) -> bool:
        """Available only if the device is currently in the device list."""
        return any(
            d.mac_address == self._mac_address for d in self.coordinator.data.devices
        )

    async def async_press(self) -> None:
        """Press the button — pause or resume the device."""
        if self._action == "pause":
            await self.coordinator.api.pause_device(self._mac_address)
        else:
            await self.coordinator.api.resume_device(self._mac_address)
        await self.coordinator.async_request_refresh()