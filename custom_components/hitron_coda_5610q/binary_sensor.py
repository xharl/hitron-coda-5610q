"""Binary sensors for the Hitron CODA-5610Q.

Exposes DOCSIS provisioning steps, firewall status, and WiFi radio status.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import HitronCodaCoordinator

DOCSIS_STEPS = (
    "hwInit",
    "findDownstream",
    "ranging",
    "dhcp",
    "timeOfday",
    "downloadCfg",
    "registration",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: HitronCodaCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []

    # DOCSIS provisioning steps
    for step in DOCSIS_STEPS:
        entities.append(
            HitronDocsisStepSensor(
                coordinator,
                BinarySensorEntityDescription(
                    key=f"docsis_{step}",
                    name=f"DOCSIS {step.replace('_', ' ').title()}",
                    icon="mdi:check-network",
                ),
                step,
            )
        )

    # Network access permitted
    entities.append(
        HitronBinarySensor(
            coordinator,
            BinarySensorEntityDescription(
                key="network_access",
                name="Network Access",
                icon="mdi:lock-open-check",
            ),
            lambda data: data.docsis_provisioning.get("networkAccess") == "Permitted",
        )
    )

    # Firewall enabled
    entities.append(
        HitronBinarySensor(
            coordinator,
            BinarySensorEntityDescription(
                key="firewall_enabled",
                name="Firewall Enabled",
                icon="mdi:shield-check",
            ),
            lambda data: data.firewall_status.get("fw_status") == "Enable",
        )
    )

    # WiFi radio on/off per band
    for radio in coordinator.data.wifi_radios:
        band = radio.get("band", "unknown")
        entities.append(
            HitronBinarySensor(
                coordinator,
                BinarySensorEntityDescription(
                    key=f"wifi_radio_{band}",
                    name=f"WiFi {band} Radio",
                    icon="mdi:wifi",
                ),
                lambda data, b=band: next(
                    (r.get("on_off") == "ON" for r in data.wifi_radios if r.get("band") == b),
                    False,
                ),
            )
        )

    # Ethernet ports linked
    for port in coordinator.data.ethernet_ports:
        port_id = port.get("port_id", "0")
        is_wan = port.get("is_wan", False)
        entities.append(
            HitronBinarySensor(
                coordinator,
                BinarySensorEntityDescription(
                    key=f"eth_port_{port_id}",
                    name=f"Ethernet Port {port_id}{' (WAN)' if is_wan else ''}",
                    icon="mdi:ethernet",
                ),
                lambda data, pid=port_id: next(
                    (p.get("linked") == "Linked" for p in data.ethernet_ports if p.get("port_id") == pid),
                    False,
                ),
            )
        )

    async_add_entities(entities)


class HitronBinarySensor(
    CoordinatorEntity[HitronCodaCoordinator], BinarySensorEntity
):
    """Generic binary sensor driven by a lambda on coordinator data."""

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        description: BinarySensorEntityDescription,
        value_fn,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"
        self._value_fn = value_fn

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.data.system_info.serial_number)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
        )

    @property
    def is_on(self) -> bool:
        return bool(self._value_fn(self.coordinator.data))


class HitronDocsisStepSensor(
    CoordinatorEntity[HitronCodaCoordinator], BinarySensorEntity
):
    """DOCSIS provisioning step status (Success = on)."""

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        description: BinarySensorEntityDescription,
        step: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"
        self._step = step

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.data.system_info.serial_number)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.docsis_provisioning.get(self._step) == "Success"