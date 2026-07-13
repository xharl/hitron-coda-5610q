"""Test the device_tracker platform.

Regression for v0.2.11: TrackerEntity.state in HA 2026.x returns None
unless the entity has a location_name or is a member of a configured
zone. Router-based device trackers have neither, so the state was
always 'unknown'. The fix overrides `state` in the subclass to return
STATE_HOME / STATE_NOT_HOME based on the device's Active/Paused status
from the router.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.device_tracker import SourceType
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
import pytest

from custom_components.hitron_coda_5610q.const import DOMAIN
from custom_components.hitron_coda_5610q.device_tracker import (
    HitronCodaDeviceTracker,
)
from custom_components.hitron_coda_5610q.api import ConnectedDevice, SystemInfo


def _make_coordinator(devices: list[ConnectedDevice]):
    """Build a minimal mock coordinator with the given devices."""
    coord = MagicMock()
    coord.hass = MagicMock()
    coord.data = MagicMock()
    coord.data.devices = devices
    coord.data.system_info = SystemInfo(
        serial_number="AN1234",
        software_version="7.3.5.1.2b22",
        hardware_version="Rev. 1",
        model_name="CODA5610Q",
        api_version="1.0",
        vendor_name="Hitron Technologies",
        device_id="AA:BB:CC:DD:EE:FF",
        deployment_name="default",
        wifi_chip="BCM4366",
    )
    coord.data.wifi_clients = []
    return coord


def _device(mac: str, status: bool, hostname: str = "test-device") -> ConnectedDevice:
    return ConnectedDevice(
        hostname=hostname,
        ip_address="192.168.0.10",
        mac_address=mac,
        interface="WiFi 2.4G",
        address_source="DHCP-IP",
        status=status,
        action="Resume" if status else "Pause",
    )


async def test_state_home_when_device_active():
    """A device with status=True should report STATE_HOME."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=True, hostname="kitchen-laptop")])
    tracker = HitronCodaDeviceTracker(coord, mac)
    assert tracker.state == STATE_HOME
    assert tracker.is_connected is True
    assert tracker.hostname == "kitchen-laptop"
    assert tracker.source_type == SourceType.ROUTER
    assert tracker.mac_address == mac


async def test_state_not_home_when_device_paused():
    """A device with status=False should report STATE_NOT_HOME."""
    mac = "11:22:33:44:55:66"
    coord = _make_coordinator([_device(mac, status=False)])
    tracker = HitronCodaDeviceTracker(coord, mac)
    assert tracker.state == STATE_NOT_HOME
    assert tracker.is_connected is False


async def test_state_not_home_when_device_disappeared():
    """A device that was seen once but no longer in coordinator.data
    should report STATE_NOT_HOME (not 'unknown')."""
    mac = "AA:BB:CC:DD:EE:FF"
    # No devices match this MAC in coordinator.data
    coord = _make_coordinator([_device("00:00:00:00:00:01", status=True)])
    tracker = HitronCodaDeviceTracker(coord, mac)
    assert tracker.state == STATE_NOT_HOME
    assert tracker.is_connected is False


async def test_unique_id_includes_mac():
    """Each device tracker should have a stable unique_id based on the MAC."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=True)])
    tracker = HitronCodaDeviceTracker(coord, mac)
    assert tracker.unique_id == f"{DOMAIN}_{mac}"


async def test_device_info_creates_per_device_device_entry():
    """Each tracked LAN device should be its own HA device (identified by
    its MAC) with the router as via_device — NOT collapsed under a
    single 'Hitron CODA-5610Q' device.

    Regression for v0.2.12: all 21 device_tracker entities were sharing
    the same DeviceInfo.identifiers set, so HA collapsed them under one
    device with the same name. The fix uses each device's MAC as the
    identifier and via_device=(DOMAIN, router_serial).
    """
    mac_a = "AA:BB:CC:DD:EE:01"
    mac_b = "AA:BB:CC:DD:EE:02"
    coord = _make_coordinator([
        _device(mac_a, status=True, hostname="kitchen-laptop"),
        _device(mac_b, status=True, hostname="office-printer"),
    ])
    tracker_a = HitronCodaDeviceTracker(coord, mac_a)
    tracker_b = HitronCodaDeviceTracker(coord, mac_b)

    info_a = tracker_a.device_info
    info_b = tracker_b.device_info

    # Each tracker has its own identifier (its MAC) — they're not
    # collapsed into a single device. (DeviceInfo is a TypedDict at
    # runtime, so use [] access, not attribute access.)
    assert info_a["identifiers"] == {(DOMAIN, mac_a)}
    assert info_b["identifiers"] == {(DOMAIN, mac_b)}
    assert info_a["identifiers"] != info_b["identifiers"]

    # Each has a mac connection (so HA's device registry can dedupe by
    # MAC if the same device shows up via another integration).
    assert ("mac", mac_a) in info_a["connections"]
    assert ("mac", mac_b) in info_b["connections"]

    # Each names itself after the LAN device's hostname.
    assert info_a["name"] == "kitchen-laptop"
    assert info_b["name"] == "office-printer"

    # Both are children of the router device, not standalone.
    router_serial = coord.data.system_info.serial_number
    assert info_a["via_device"] == (DOMAIN, router_serial)
    assert info_b["via_device"] == (DOMAIN, router_serial)


async def test_device_info_falls_back_to_mac_when_no_hostname():
    """Devices without a hostname (the router returned an empty string)
    should still get a meaningful device name — fall back to the MAC."""
    mac = "AA:BB:CC:DD:EE:FF"
    # Construct a device with empty hostname
    device = ConnectedDevice(
        hostname="",  # router returned empty
        ip_address="192.168.0.10",
        mac_address=mac,
        interface="WiFi 2.4G",
        address_source="DHCP-IP",
        status=True,
        action="Resume",
    )
    coord = _make_coordinator([device])
    tracker = HitronCodaDeviceTracker(coord, mac)
    assert tracker.device_info["name"] == mac  # fell back to MAC
    # But _hostname (the property used by the device name) returns the
    # MAC when the hostname is empty.
    assert tracker._hostname == mac


async def test_extra_state_attributes_includes_wifi_info():
    """The extra_state_attributes should pull RSSI/SSID/etc from wifi_clients
    when the device is a WiFi client."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=True)])
    coord.data.wifi_clients = [
        {
            "mac_address": mac,
            "rssi": "-45",
            "band": "2.4G",
            "ssid": "Shulgin",
            "bitrate": "72 Mbps",
            "channel": "6",
        }
    ]
    tracker = HitronCodaDeviceTracker(coord, mac)
    attrs = tracker.extra_state_attributes
    assert attrs["interface"] == "WiFi 2.4G"
    assert attrs["address_source"] == "DHCP-IP"
    assert attrs["rssi"] == "-45"
    assert attrs["wifi_ssid"] == "Shulgin"
    assert attrs["wifi_channel"] == "6"
