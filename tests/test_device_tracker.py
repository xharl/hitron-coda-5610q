"""Test the device_tracker platform.

Covers:
  - v0.2.11: state override (returns STATE_HOME/STATE_NOT_HOME, not None)
  - v0.2.12: per-MAC device registry entries (no collapse under router)
  - v0.2.13: hostname-based identity, MAC rotation, hostname uniqueness

The constructor signature changed in v0.2.13 — the entity now takes
a ``DeviceIdentity`` (key, current_mac, track_by, hostname) rather
than a raw MAC string. These tests use a small helper to build
``DeviceIdentity`` instances.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from homeassistant.components.device_tracker import SourceType
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hitron_coda_5610q.api import ConnectedDevice, SystemInfo
from custom_components.hitron_coda_5610q.const import DOMAIN
from custom_components.hitron_coda_5610q.device_tracker import (
    DeviceIdentity,
    HitronCodaDeviceTracker,
    async_setup_entry,
    make_entity_unique_id,
)


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
    # v0.3.0: MagicMock auto-creates attributes — explicitly None out the
    # hysteresis store so tests exercise the no-store fallback path.
    coord.identity_store = None
    coord.presence_grace = 240
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


def _identity(mac: str, hostname: str = "test-device", track_by: str = "hostname") -> DeviceIdentity:
    """Build a DeviceIdentity in the v0.2.13+ form.

    The `key` is the hostname (for hostname tracking) or the MAC (for
    mac tracking). Default to hostname tracking since that's the
    v0.2.13 default.
    """
    key = hostname if track_by == "hostname" else mac
    return DeviceIdentity(
        key=key,
        current_mac=mac,
        track_by=track_by,
        hostname=hostname if hostname else None,
    )


async def test_state_home_when_device_active():
    """A device with status=True should report STATE_HOME."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=True, hostname="phone")])
    tracker = HitronCodaDeviceTracker(coord, _identity(mac, "phone"))
    assert tracker.state == STATE_HOME


async def test_state_not_home_when_device_paused():
    """A device with status=False (paused at the router) reports not_home."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=False, hostname="phone")])
    tracker = HitronCodaDeviceTracker(coord, _identity(mac, "phone"))
    assert tracker.state == STATE_NOT_HOME


async def test_state_not_home_when_device_disappeared():
    """A device that was once seen but is no longer in the active
    list reports not_home (the entity stays around, just not_home)."""
    mac = "AA:BB:CC:DD:EE:FF"
    # No devices in the active list
    coord = _make_coordinator([])
    tracker = HitronCodaDeviceTracker(coord, _identity(mac, "phone"))
    assert tracker.state == STATE_NOT_HOME


async def test_unique_id_uses_hostname_when_available():
    """v0.2.13: when hostname is known, the unique_id is keyed on
    hostname, not MAC. Two devices with the same hostname but
    different MACs share a unique_id (the later one wins, but the
    entity_id stays the same)."""
    mac1 = "AA:BB:CC:DD:EE:01"
    mac2 = "AA:BB:CC:DD:EE:02"  # rotated MAC, same hostname
    coord = _make_coordinator([_device(mac1, status=True, hostname="pixel-6")])
    identity = _identity(mac1, "pixel-6")
    tracker = HitronCodaDeviceTracker(coord, identity)
    expected = make_entity_unique_id("hostname", "pixel-6", "pixel-6", mac1)
    assert tracker.unique_id == expected
    # And rotating the MAC produces the same unique_id
    identity.current_mac = mac2
    assert tracker.unique_id == expected


async def test_unique_id_falls_back_to_mac_when_no_hostname():
    """If a device has no hostname, the unique_id is keyed on MAC."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=True, hostname="")])
    identity = _identity(mac, "")
    tracker = HitronCodaDeviceTracker(coord, identity)
    # When hostname is None/empty, make_entity_unique_id takes the
    # MAC-tracking branch and returns ``f"{DOMAIN}_{mac}"``.
    expected = make_entity_unique_id("hostname", None, None, mac)
    assert tracker.unique_id == expected


async def test_device_info_creates_per_device_device_entry():
    """v0.2.12: each device gets its own DeviceInfo (no collapse
    under the router device)."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=True, hostname="pixel-6")])
    tracker = HitronCodaDeviceTracker(coord, _identity(mac, "pixel-6"))
    info = tracker.device_info
    # The identifier is the key (hostname) — so the device is
    # registered under "pixel-6" in the device registry, not under
    # the router's serial number.
    assert (DOMAIN, "pixel-6") in info["identifiers"]


async def test_extra_state_attributes_includes_wifi_info():
    """The extra_state_attributes expose MAC and hostname for
    automations."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=True, hostname="phone")])
    tracker = HitronCodaDeviceTracker(coord, _identity(mac, "phone"))
    attrs = tracker.extra_state_attributes
    # The attribute is `current_mac` (not `mac`) so that automations
    # can detect when a device has rotated its MAC — the value will
    # change while the entity stays the same.
    assert attrs["current_mac"] == mac
    assert attrs["interface"] == "WiFi 2.4G"
    assert attrs["action"] == "Resume"


async def test_grace_window_keeps_device_home_after_disappearance():
    """v0.3.0: a device that drops off the host list stays 'home' within
    the grace window when the identity store has a fresh last_seen."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([])  # device gone from the list
    store = MagicMock()
    store.seen_within = MagicMock(return_value=True)  # seen 10s ago
    coord.identity_store = store
    coord.presence_grace = 240
    tracker = HitronCodaDeviceTracker(coord, _identity(mac, "phone"))
    assert tracker.state == STATE_HOME
    assert store.seen_within.call_count >= 1
    store.seen_within.assert_any_call(mac, 240)


async def test_router_pause_is_immediate_not_home():
    """v0.3.0: router-reported Pause (status=False, still listed) is
    respected immediately — no grace window applies."""
    mac = "AA:BB:CC:DD:EE:FF"
    coord = _make_coordinator([_device(mac, status=False, hostname="phone")])
    store = MagicMock()
    store.seen_within = MagicMock(return_value=True)  # even with fresh last-seen
    coord.identity_store = store
    coord.presence_grace = 240
    tracker = HitronCodaDeviceTracker(coord, _identity(mac, "phone"))
    assert tracker.state == STATE_NOT_HOME


# ---- v0.3.2: empty→non-empty entity adoption ----


async def test_setup_with_devices_adds_entities(hass):
    """Guard: a healthy setup still adopts entities immediately."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.0.1", "username": "cusadmin", "password": "pw"},
    )
    entry.add_to_hass(hass)
    coord = _make_coordinator(
        [_device("AA:BB:CC:DD:EE:01", True, "pixel-6"),
         _device("AA:BB:CC:DD:EE:02", True, "watch")]
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []

    def _add(new_entities, update_before_add=False):
        added.extend(new_entities)

    await async_setup_entry(hass, entry, _add)
    assert len(added) == 2


async def test_empty_setup_adopts_when_data_arrives(hass):
    """v0.3.2 regression (the 2026-09-07 wedge): platform setup against
    an empty device list must NOT be terminal — when real device data
    arrives, the watcher adopts the entities without a reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.0.1", "username": "cusadmin", "password": "pw"},
    )
    entry.add_to_hass(hass)
    coord = _make_coordinator([])  # boot with empty device list
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []

    def _add(new_entities, update_before_add=False):
        added.extend(new_entities)

    await async_setup_entry(hass, entry, _add)
    assert added == []  # nothing to add yet

    # The watcher registered a coordinator listener.
    listener = coord.async_add_listener.call_args[0][0]

    # Device data arrives (router recovered).
    coord.data.devices = [_device("AA:BB:CC:DD:EE:FF", True, "pixel-6")]
    listener()
    # Adoption runs in a background task; wait for it deterministically.
    deadline = 2.0
    while not added and deadline > 0:
        await asyncio.sleep(0.05)
        deadline -= 0.05
    await hass.async_block_till_done()

    assert len(added) == 1
    assert added[0]._identity.key == "pixel-6"

    # Further listener fires are no-ops (entities already adopted).
    coord.data.devices = [
        _device("AA:BB:CC:DD:EE:FF", True, "pixel-6"),
        _device("AA:BB:CC:DD:EE:02", True, "watch"),
    ]
    listener()
    await hass.async_block_till_done()
    assert len(added) == 1  # no duplicates
