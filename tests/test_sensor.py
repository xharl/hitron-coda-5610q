"""Test the sensor platform, especially the v0.2.14 opt-in flag.

Background: the v0.2.14 release moved the per-channel DOCSIS power/SNR
sensors (32 entities on a healthy cable plant) behind a
``CONF_EXPOSE_DIAGNOSTICS`` option that defaults to False. The test
suite covers the boundary:
  - With the flag off, only router-level sensors are created.
  - With the flag on, per-channel sensors are created.

v0.3.1 additionally covers the DOCSIS degradation UX: the per-channel
sensors' docsis_stale/last_good attributes and the docsis_data_ok
binary sensor that flips off while the modem serves HTML instead of
JSON.
"""
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.util import dt as dt_util

from custom_components.hitron_coda_5610q.binary_sensor import (
    HitronDocsisOkBinarySensor,
    async_setup_entry as async_setup_binary_sensor,
)
from custom_components.hitron_coda_5610q.const import (
    CONF_EXPOSE_DIAGNOSTICS,
    DOMAIN,
)
from custom_components.hitron_coda_5610q.sensor import (
    HitronDownstreamSensor,
    HitronRouterSensor,
    HitronSensorEntityDescription,
    HitronUpstreamSensor,
    ROUTER_SENSORS,
    async_setup_entry,
)


class _Channel:
    """Stand-in for a DownstreamChannel / UpstreamChannel."""

    def __init__(self, channel_id: int = 1, **kwargs):
        self.channel_id = channel_id
        self.signal_strength = kwargs.get("signal_strength", 5.0)
        self.snr = kwargs.get("snr", 35.0)
        self.frequency = kwargs.get("frequency", 591_000_000)
        self.modulation = kwargs.get("modulation", "QAM256")
        self.modulation_type = kwargs.get("modulation_type", "ATDMA")
        self.port_id = kwargs.get("port_id", 1)
        self.correcteds = kwargs.get("correcteds", 0)
        self.uncorrectables = kwargs.get("uncorrectables", 0)
        self.bandwidth = kwargs.get("bandwidth", 6_400_000)


def _make_coordinator_data(
    downstream: int = 0, upstream: int = 0
) -> MagicMock:
    """Build a coordinator.data object with N DS and N US channels."""
    sys_info = MagicMock()
    sys_info.serial_number = "AN6025101226"
    sys_info.software_version = "7.3.5.1.2b22"

    data = MagicMock()
    data.system_info = sys_info
    data.devices = []
    data.downstream_channels = [_Channel(channel_id=i + 1) for i in range(downstream)]
    data.upstream_channels = [_Channel(channel_id=i + 5) for i in range(upstream)]
    data.router_sys_info = {
        "systemWanUptime": 100000, "systemLanUptime": 200000,
        "wanIP": ["144.172.149.172"],
        "wanRx": 100, "wanTx": 50, "lanRx": 30, "lanTx": 20,
    }
    data.firewall_status = {"securityLevel": "Minimum", "fw_status": "Enable"}
    data.cm_sys_info = {
        "ip": ["10.96.17.40"],
        "DsDataRate": 1180000000, "UsDataRate": 59000000,
    }
    return data


def _make_entry(options: dict | None = None) -> MagicMock:
    """Build a config entry stub."""
    entry = MagicMock()
    entry.options = options or {}
    return entry


def _make_hass(coordinator_data, entry_id: str = "test-entry-id"):
    """Build a hass stub with a coordinator ready to use.

    `entry_id` is the string the entry's `entry_id` attribute will
    return. Must match between _make_hass and _make_entry so the
    `hass.data[DOMAIN][entry.entry_id]` lookup in async_setup_entry
    finds the coordinator.
    """
    coordinator = MagicMock()
    coordinator.data = coordinator_data
    hass = MagicMock()
    hass.data = {DOMAIN: {entry_id: coordinator}}
    return hass, coordinator


def _make_entry(options: dict | None = None, entry_id: str = "test-entry-id"):
    """Build a config entry stub."""
    entry = MagicMock()
    entry.options = options or {}
    entry.entry_id = entry_id
    return entry


def _capture_callback(added: list):
    """Return a callable that appends entities to `added`.

    The async_setup_entry signature is async_setup_entry(hass, entry,
    async_add_entities: AddEntitiesCallback). AddEntitiesCallback is a
    sync function that takes new_entities (an iterable of Entity).
    """
    def _add(new_entities):
        added.extend(new_entities)
    return _add


async def test_router_sensors_always_created():
    """v0.2.14: the 12 router-level sensors are always created regardless
    of the diagnostics flag."""
    data = _make_coordinator_data(downstream=0, upstream=0)
    hass, coordinator = _make_hass(data)
    entry = _make_entry()

    added: list = []
    await async_setup_entry(hass, entry, _capture_callback(added))

    # 12 router sensors from ROUTER_SENSORS, no DS/US channel sensors.
    assert len(added) == len(ROUTER_SENSORS)
    assert all(isinstance(s, HitronRouterSensor) for s in added)


async def test_per_channel_sensors_omitted_by_default():
    """v0.2.14: without CONF_EXPOSE_DIAGNOSTICS, no per-channel sensors
    are created even if the coordinator has channel data."""
    data = _make_coordinator_data(downstream=8, upstream=4)
    hass, coordinator = _make_hass(data)
    entry = _make_entry()  # no options at all

    added: list = []
    await async_setup_entry(hass, entry, _capture_callback(added))

    # Only the router sensors — no DS or US channel sensors.
    assert all(isinstance(s, HitronRouterSensor) for s in added)
    assert not any(isinstance(s, HitronDownstreamSensor) for s in added)
    assert not any(isinstance(s, HitronUpstreamSensor) for s in added)


async def test_per_channel_sensors_created_when_flag_on():
    """v0.2.14: with CONF_EXPOSE_DIAGNOSTICS=True, the per-channel DOCSIS
    sensors are created. 8 DS channels = 16 sensors (SNR + power),
    4 US channels = 4 sensors (power)."""
    data = _make_coordinator_data(downstream=8, upstream=4)
    hass, coordinator = _make_hass(data)
    entry = _make_entry({CONF_EXPOSE_DIAGNOSTICS: True})

    added: list = []
    await async_setup_entry(hass, entry, _capture_callback(added))

    router_count = sum(1 for s in added if isinstance(s, HitronRouterSensor))
    ds_count = sum(1 for s in added if isinstance(s, HitronDownstreamSensor))
    us_count = sum(1 for s in added if isinstance(s, HitronUpstreamSensor))

    assert router_count == len(ROUTER_SENSORS)
    assert ds_count == 16  # 8 channels * (snr + power)
    assert us_count == 4   # 4 channels * (power)


async def test_per_channel_sensors_explicitly_off():
    """v0.2.14: CONF_EXPOSE_DIAGNOSTICS=False is the same as absent."""
    data = _make_coordinator_data(downstream=8, upstream=4)
    hass, coordinator = _make_hass(data)
    entry = _make_entry({CONF_EXPOSE_DIAGNOSTICS: False})

    added: list = []
    await async_setup_entry(hass, entry, _capture_callback(added))

    assert len(added) == len(ROUTER_SENSORS)
    assert not any(isinstance(s, HitronDownstreamSensor) for s in added)
    assert not any(isinstance(s, HitronUpstreamSensor) for s in added)


# ---- v0.3.1: DOCSIS degradation staleness attributes ----


def _channel_sensor_stub(coordinator):
    """Build a HitronDownstreamSensor/HitronUpstreamSensor pair directly."""
    ds = HitronDownstreamSensor(
        coordinator,
        HitronSensorEntityDescription(key="ds_1_snr", name="DS Channel 1 SNR"),
        channel_index=0,
        attr="snr",
    )
    us = HitronUpstreamSensor(
        coordinator,
        HitronSensorEntityDescription(key="us_5_power", name="US Channel 5 Power"),
        channel_index=0,
        attr="signal_strength",
    )
    return ds, us


async def test_channel_sensors_report_staleness_during_degradation():
    """v0.3.1: while the backing endpoint is degraded, channel sensors
    flag docsis_stale=True and expose a last_good ISO timestamp; the
    non-degraded tier reports docsis_stale=False.
    """
    data = _make_coordinator_data(downstream=1, upstream=1)
    hass, coordinator = _make_hass(data)
    ds_sensor, us_sensor = _channel_sensor_stub(coordinator)

    last_good = dt_util.utcnow()

    def _degraded_fields(field: str) -> bool:
        return field == "downstream_channels"

    def _last_good(field: str):
        return last_good if field == "downstream_channels" else None

    coordinator.is_degraded = _degraded_fields
    coordinator.field_last_good = _last_good

    ds_attrs = ds_sensor.extra_state_attributes
    assert ds_attrs["docsis_stale"] is True
    assert ds_attrs["last_good"] == last_good.isoformat()

    us_attrs = us_sensor.extra_state_attributes
    assert us_attrs["docsis_stale"] is False
    assert "last_good" not in us_attrs


async def test_channel_sensors_report_fresh_when_not_degraded():
    """Healthy cycle: docsis_stale is False and no last_good attribute."""
    data = _make_coordinator_data(downstream=1, upstream=1)
    hass, coordinator = _make_hass(data)
    ds_sensor, us_sensor = _channel_sensor_stub(coordinator)

    coordinator.is_degraded = lambda field: False
    coordinator.field_last_good = lambda field: None

    for sensor in (ds_sensor, us_sensor):
        attrs = sensor.extra_state_attributes
        assert attrs["docsis_stale"] is False
        assert "last_good" not in attrs


# ---- v0.3.1: docsis_data_ok binary sensor ----


async def test_binary_sensor_setup_creates_docsis_data_ok():
    """The docsis_data_ok connectivity entity is created with the rest
    of the binary sensor platform."""
    data = _make_coordinator_data()
    hass, coordinator = _make_hass(data)
    entry = _make_entry()

    added: list = []
    await async_setup_binary_sensor(hass, entry, _capture_callback(added))

    entity = next(
        s for s in added if isinstance(s, HitronDocsisOkBinarySensor)
    )
    assert entity.unique_id == f"{DOMAIN}_docsis_data_ok"
    assert entity.device_class == BinarySensorDeviceClass.CONNECTIVITY


async def test_docsis_data_ok_flips_off_during_degradation_and_back_on():
    """The full automation story: healthy → degraded (the live defect)
    → recovery, with the degraded_endpoints/since attributes following.
    """
    data = _make_coordinator_data()
    hass, coordinator = _make_hass(data)
    entry = _make_entry()

    added: list = []
    await async_setup_binary_sensor(hass, entry, _capture_callback(added))
    entity = next(
        s for s in added if isinstance(s, HitronDocsisOkBinarySensor)
    )

    # Healthy: on, no degraded endpoints, no window start.
    coordinator.docsis_degraded = False
    coordinator.docsis_degraded_endpoints = []
    coordinator.degraded_since = None
    assert entity.is_on is True
    assert entity.extra_state_attributes == {
        "degraded_endpoints": [],
        "since": None,
    }

    # Degraded: DsInfo/UsInfo serve the SPA login page instead of JSON.
    since = dt_util.utcnow()
    degraded = ["/1/Device/CM/DsInfo", "/1/Device/CM/UsInfo"]
    coordinator.docsis_degraded = True
    coordinator.docsis_degraded_endpoints = degraded
    coordinator.degraded_since = since
    assert entity.is_on is False
    attrs = entity.extra_state_attributes
    assert attrs["degraded_endpoints"] == degraded
    assert attrs["since"] == since.isoformat()

    # Recovery: back on with the tracking reset.
    coordinator.docsis_degraded = False
    coordinator.docsis_degraded_endpoints = []
    coordinator.degraded_since = None
    assert entity.is_on is True
    assert entity.extra_state_attributes == {
        "degraded_endpoints": [],
        "since": None,
    }
