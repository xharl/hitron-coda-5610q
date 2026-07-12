"""Sensors for the Hitron CODA-5610Q.

Exposes DOCSIS diagnostics (downstream SNR/power, upstream power),
router system stats (WAN/LAN uptime, traffic), and device count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import HitronCodaCoordinator


@dataclass(frozen=True)
class HitronSensorEntityDescription(SensorEntityDescription):
    """Describes a Hitron sensor."""


ROUTER_SENSORS: tuple[HitronSensorEntityDescription, ...] = (
    HitronSensorEntityDescription(
        key="wan_uptime",
        name="WAN Uptime",
        native_unit_of_measurement="s",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="lan_uptime",
        name="LAN Uptime",
        native_unit_of_measurement="s",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="connected_devices",
        name="Connected Devices",
        native_unit_of_measurement="devices",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:router-network",
    ),
    HitronSensorEntityDescription(
        key="wan_ip",
        name="WAN IP Address",
        icon="mdi:ip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="wan_rx",
        name="WAN RX",
        icon="mdi:download-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="wan_tx",
        name="WAN TX",
        icon="mdi:upload-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="firewall_level",
        name="Firewall Level",
        icon="mdi:shield",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="cm_ip",
        name="Cable Modem IP",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="ds_data_rate",
        name="Downstream Data Rate",
        native_unit_of_measurement="bps",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="us_data_rate",
        name="Upstream Data Rate",
        native_unit_of_measurement="bps",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="lan_rx",
        name="LAN RX",
        icon="mdi:download-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitronSensorEntityDescription(
        key="lan_tx",
        name="LAN TX",
        icon="mdi:upload-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: HitronCodaCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Router-level sensors
    entities: list[SensorEntity] = [
        HitronRouterSensor(coordinator, desc) for desc in ROUTER_SENSORS
    ]

    # Per-downstream-channel sensors (SNR, power)
    for i, channel in enumerate(coordinator.data.downstream_channels):
        entities.append(
            HitronDownstreamSensor(
                coordinator,
                HitronSensorEntityDescription(
                    key=f"ds_{channel.channel_id}_snr",
                    name=f"DS Channel {channel.channel_id} SNR",
                    native_unit_of_measurement="dB",
                    device_class=SignalToNoiseRatio,
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    icon="mdi:signal-variant",
                ),
                channel_index=i,
                attr="snr",
            )
        )
        entities.append(
            HitronDownstreamSensor(
                coordinator,
                HitronSensorEntityDescription(
                    key=f"ds_{channel.channel_id}_power",
                    name=f"DS Channel {channel.channel_id} Power",
                    native_unit_of_measurement="dBmV",
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    icon="mdi:flash",
                ),
                channel_index=i,
                attr="signal_strength",
            )
        )

    # Per-upstream-channel sensors (power)
    for i, channel in enumerate(coordinator.data.upstream_channels):
        entities.append(
            HitronUpstreamSensor(
                coordinator,
                HitronSensorEntityDescription(
                    key=f"us_{channel.channel_id}_power",
                    name=f"US Channel {channel.channel_id} Power",
                    native_unit_of_measurement="dBmV",
                    state_class=SensorStateClass.MEASUREMENT,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    icon="mdi:flash",
                ),
                channel_index=i,
                attr="signal_strength",
            )
        )

    async_add_entities(entities)


# SignalToNoiseRatio isn't in older HA versions — use a string fallback
try:
    from homeassistant.components.sensor import SensorDeviceClass
    SignalToNoiseRatio = SensorDeviceClass.SIGNAL_STRENGTH
except AttributeError:
    SignalToNoiseRatio = None


class HitronRouterSensor(CoordinatorEntity[HitronCodaCoordinator], SensorEntity):
    """Router-level sensor (uptime, WAN IP, traffic, device count)."""

    entity_description: HitronSensorEntityDescription

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        description: HitronSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.data.system_info.serial_number)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
            sw_version=self.coordinator.data.system_info.software_version,
        )

    @property
    def native_value(self) -> StateType:
        key = self.entity_description.key
        data = self.coordinator.data

        if key == "connected_devices":
            return len(data.devices)
        elif key == "wan_uptime":
            return data.router_sys_info.get("systemWanUptime")
        elif key == "lan_uptime":
            return data.router_sys_info.get("systemLanUptime")
        elif key == "wan_ip":
            wan_ips = data.router_sys_info.get("wanIP", [])
            return wan_ips[0] if wan_ips else None
        elif key == "wan_rx":
            return data.router_sys_info.get("wanRx")
        elif key == "wan_tx":
            return data.router_sys_info.get("wanTx")
        elif key == "firewall_level":
            return data.firewall_status.get("securityLevel")
        elif key == "cm_ip":
            ips = data.cm_sys_info.get("ip", [])
            return ips[0] if ips else None
        elif key == "ds_data_rate":
            return data.cm_sys_info.get("DsDataRate")
        elif key == "us_data_rate":
            return data.cm_sys_info.get("UsDataRate")
        elif key == "lan_rx":
            return data.router_sys_info.get("lanRx")
        elif key == "lan_tx":
            return data.router_sys_info.get("lanTx")
        return None


class HitronDownstreamSensor(CoordinatorEntity[HitronCodaCoordinator], SensorEntity):
    """Per-downstream-channel sensor (SNR or power)."""

    entity_description: HitronSensorEntityDescription

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        description: HitronSensorEntityDescription,
        channel_index: int,
        attr: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"
        self._channel_index = channel_index
        self._attr = attr

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.data.system_info.serial_number)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
        )

    @property
    def native_value(self) -> StateType:
        channels = self.coordinator.data.downstream_channels
        if self._channel_index < len(channels):
            return getattr(channels[self._channel_index], self._attr)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        channels = self.coordinator.data.downstream_channels
        if self._channel_index < len(channels):
            ch = channels[self._channel_index]
            return {
                "frequency": ch.frequency,
                "modulation": ch.modulation,
                "channel_id": ch.channel_id,
                "port_id": ch.port_id,
                "correcteds": ch.correcteds,
                "uncorrectables": ch.uncorrectables,
            }
        return {}


class HitronUpstreamSensor(CoordinatorEntity[HitronCodaCoordinator], SensorEntity):
    """Per-upstream-channel sensor (power)."""

    entity_description: HitronSensorEntityDescription

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        description: HitronSensorEntityDescription,
        channel_index: int,
        attr: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"
        self._channel_index = channel_index
        self._attr = attr

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.data.system_info.serial_number)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name="Hitron CODA-5610Q",
        )

    @property
    def native_value(self) -> StateType:
        channels = self.coordinator.data.upstream_channels
        if self._channel_index < len(channels):
            return getattr(channels[self._channel_index], self._attr)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        channels = self.coordinator.data.upstream_channels
        if self._channel_index < len(channels):
            ch = channels[self._channel_index]
            return {
                "frequency": ch.frequency,
                "modulation_type": ch.modulation_type,
                "channel_id": ch.channel_id,
                "port_id": ch.port_id,
                "bandwidth": ch.bandwidth,
            }
        return {}