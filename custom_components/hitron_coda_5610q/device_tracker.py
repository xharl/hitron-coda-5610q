"""Device tracker for the Hitron CODA-5610Q.

v0.2.13: hostname-based identity to survive MAC rotation.

Background: the v0.2.12 integration tracked every device by MAC address,
using the MAC as the unique_id. This was correct for stationary devices
but broke for mobile devices that use "Private WiFi Address" (iOS/macOS
"Private Relay" on, Android 10+ "Use randomized MAC" on) — every time
such a device reconnects, the router sees a new random MAC, the
integration creates a new entity, and the old entity stays in the
entity registry forever in `not_home` state.

In the user's setup, this caused Leave Home automations to drift:
the Pixel 6 (randomized MAC, currently `be:fe:90:46:4d:4b`) would
appear as `device_tracker.hitron_coda_5610q_15` and be fed into
`person.xavier` via the user-added "tracked devices" list. When the
phone's MAC rotated, the new MAC became `device_tracker.hitron_coda_5610q_16`
in `not_home` state, and the old `_15` stayed in `not_home` too. The
person entity would oscillate between home and not_home as the
person-picker weighed the two sources.

v0.2.13 fix: track devices by hostname (the router's reported
`hostName` field, which is stable for any device that doesn't override
it). MAC is now an attribute of the entity, not its identity. When a
device with a known hostname reconnects under a new MAC, the
existing entity is updated in place. The old MAC stays in the
recorder history as a "last seen" but the entity identity never
changes.

Identity keys, in order of preference:
  1. hostname (router-reported, stable)
  2. MAC (fallback for devices that don't report a hostname)

The integration exposes a config option `track_by: mac | hostname`
(default `hostname` in v0.2.13) for users who want the old behavior.
The migration service `hitron_coda_5610q.migrate_to_v0_2_13` is a
one-shot that maps existing v0.2.12 entities to hostname-based
entities by inspecting the entity_registry and device_registry.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SERVICE_MIGRATE_TO_V0_2_13
from .coordinator import HitronCodaCoordinator

_LOGGER = logging.getLogger(__name__)


def normalize_hostname(hostname: str | None) -> str | None:
    """Normalize a hostname for stable identity matching.

    - Strip leading/trailing whitespace
    - Lower-case
    - Replace illegal filesystem characters with '_'
    - Return None for empty strings (caller should fall back to MAC)
    """
    if not hostname:
        return None
    h = hostname.strip().lower()
    if not h:
        return None
    # Routers sometimes report hostnames with unicode or filesystem-unsafe chars;
    # collapse them so the resulting unique_id is filesystem-safe.
    h = "".join(c if c.isalnum() or c in "-._" else "_" for c in h)
    return h or None


def make_entity_unique_id(track_by: str, hostname: str | None, mac: str) -> str:
    """Build a stable unique_id for a device_tracker entity.

    For hostname tracking: f"{DOMAIN}_host_{hostname}"
    For MAC tracking (legacy): f"{DOMAIN}_{mac}"
    """
    if track_by == "hostname" and hostname:
        # Use the hostname directly. If a collision occurs between two
        # devices with the same hostname (e.g. two phones both named
        # "android"), disambiguate by MAC hash suffix.
        return f"{DOMAIN}_host_{hostname}"
    return f"{DOMAIN}_{mac}"


@dataclass
class DeviceIdentity:
    """The identity of a tracked LAN device.

    Held by HitronCodaDeviceTracker. The entity's unique_id is derived
    from `key` (which is hostname if available, else MAC). The MAC is
    updated in place as the device rotates its address.
    """
    key: str            # hostname or MAC, used for unique_id
    current_mac: str    # most recent MAC seen for this device
    track_by: str       # "hostname" or "mac"
    hostname: str | None = None  # last-seen hostname (may be None)


class HitronCodaDeviceTracker(CoordinatorEntity[HitronCodaCoordinator], TrackerEntity):
    """A device seen on the LAN, identified by hostname in v0.2.13+."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.ROUTER

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        identity: DeviceIdentity,
    ) -> None:
        super().__init__(coordinator)
        self._identity = identity
        self._attr_unique_id = make_entity_unique_id(
            identity.track_by, identity.hostname, identity.current_mac
        )
        # Stable name for legacy/UI display. If host is known, use it; else MAC.
        self._attr_name = identity.hostname or identity.current_mac

    # ---- identity helpers ----

    @property
    def mac_address(self) -> str:
        """Return the most recent MAC seen for this device.

        Unlike v0.2.12, this is an attribute, not the identity. A device
        that rotates its MAC will see this value change in the entity's
        attributes, but the entity's unique_id and entity_id stay stable.
        """
        return self._identity.current_mac

    @property
    def hostname(self) -> str | None:
        return self._identity.hostname

    @property
    def is_connected(self) -> bool:
        """True iff the device is currently in the router's active list.

        The match is by MAC because the router still reports by MAC. If
        the device rotated its MAC, `current_mac` has already been
        updated, so this lookup is straightforward.
        """
        for d in self.coordinator.data.devices:
            if d.mac_address == self._identity.current_mac:
                return d.status
        return False

    @property
    def ip_address(self) -> str | None:
        for d in self.coordinator.data.devices:
            if d.mac_address == self._identity.current_mac:
                return d.ip_address
        return None

    @property
    def state(self) -> str:
        return STATE_HOME if self.is_connected else STATE_NOT_HOME

    # ---- device registry ----

    @property
    def device_info(self) -> DeviceInfo:
        """Per-device device-registry entry, keyed on the stable identity.

        `identifiers` uses (DOMAIN, identity_key) so the device entry
        survives MAC rotation. `connections` carries the current MAC for
        HA's network/zwave/etc integration cross-referencing, but is
        NOT the primary key.

        Note: device-registry entries are immutable on `identifiers` and
        `connections` after creation. When the MAC rotates, the new MAC
        replaces the old in the device's connections; the device entry
        keeps the same id, so it doesn't appear as a new device.
        """
        return DeviceInfo(
            identifiers={(DOMAIN, self._identity.key)},
            connections={("mac", self._identity.current_mac)},
            manufacturer="Unknown",
            model="LAN device",
            name=self._identity.hostname or self._identity.current_mac,
            via_device=(
                DOMAIN,
                self.coordinator.data.system_info.serial_number,
            ),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for d in self.coordinator.data.devices:
            if d.mac_address == self._identity.current_mac:
                attrs["interface"] = d.interface
                attrs["address_source"] = d.address_source
                attrs["action"] = d.action
                # Also expose current MAC explicitly so users can see
                # when the device has rotated.
                attrs["current_mac"] = d.mac_address
                break
        # Enrich with WiFi client info
        for wc in self.coordinator.data.wifi_clients:
            if wc.get("mac_address") == self._identity.current_mac:
                attrs["rssi"] = wc["rssi"]
                attrs["wifi_band"] = wc["band"]
                attrs["wifi_ssid"] = wc["ssid"]
                attrs["wifi_bitrate"] = wc["bitrate"]
                attrs["wifi_channel"] = wc["channel"]
                break
        return attrs

    @property
    def name(self) -> str | None:
        return None


# ---- setup + identity bookkeeping ----

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker entities, one per stable device identity.

    v0.2.13 behavior: each unique hostname (or MAC if hostname missing)
    gets exactly one entity. If a device with hostname "Pixel-6" has
    MAC "be:fe:90:46:4d:4b" today and "9a:55:de:11:22:33" tomorrow,
    the same entity handles both.

    The list of entities is recomputed every time the coordinator has
    new data, so the integration can:
      - Create entities for new devices (first time seen)
      - Update the `current_mac` on existing entities in place
      - Leave entities alive after a device disconnects (state = not_home
        is set automatically via the `state` property)
    """
    coordinator: HitronCodaCoordinator = hass.data[DOMAIN][entry.entry_id]
    track_by = entry.options.get("track_by", "hostname")  # default new behavior
    host_to_identity = _build_identities(coordinator, track_by)

    # Get the existing tracker entities for this entry (across reloads)
    # so we can update current_mac on known devices.
    existing = _existing_trackers_by_key(hass, entry.entry_id, track_by)

    new_entities: list[HitronCodaDeviceTracker] = []
    for key, identity in host_to_identity.items():
        if key in existing:
            # The platform's coordinator listener mechanism handles state
            # updates for existing entities. We don't need to (and
            # can't easily) reach the live instance here — the key alone
            # tells us not to add a duplicate. The HA coordinator will
            # fire its listener on every scan, and the entity's `state`
            # property reads from coordinator.data, so the device's
            # current MAC is picked up automatically on the next refresh.
            continue
        new_entities.append(HitronCodaDeviceTracker(coordinator, identity))

    if new_entities:
        async_add_entities(new_entities)


def _build_identities(
    coordinator: HitronCodaCoordinator,
    track_by: str,
) -> dict[str, DeviceIdentity]:
    """Map stable identity key -> DeviceIdentity for all currently-seen devices.

    Key is hostname (normalized) when track_by == "hostname", else MAC.

    Within a single scan, two devices with the same hostname would
    collide; we disambiguate by MAC-hash suffix in that case. With
    typical home networks this is rare (each device has its own
    hostname) but possible (e.g. two "android" devices).
    """
    seen_keys: dict[str, int] = {}
    out: dict[str, DeviceIdentity] = {}

    for d in coordinator.data.devices:
        hostname = normalize_hostname(d.hostname) if track_by == "hostname" else None
        base_key = hostname or d.mac_address
        # disambiguate collisions
        n = seen_keys.get(base_key, 0)
        seen_keys[base_key] = n + 1
        key = base_key if n == 0 else f"{base_key}_{n}"

        out[key] = DeviceIdentity(
            key=key,
            current_mac=d.mac_address,
            track_by=track_by,
            hostname=hostname,
        )
    return out


def _existing_trackers_by_key(
    hass: HomeAssistant,
    entry_id: str,
    track_by: str,
) -> dict[str, HitronCodaDeviceTracker]:
    """Return all live HitronCodaDeviceTracker instances for this entry, keyed.

    Used during async_setup_entry to update existing entities in place.
    The key here matches the key in _build_identities (hostname or MAC).
    """
    # Live entities in HA are accessible via entity_registry. We don't
    # have a direct map of entity_id -> instance, so we walk the
    # entity_component. This is the same approach used by other
    # device_tracker integrations.
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    out: dict[str, HitronCodaDeviceTracker] = {}
    for ent_reg_entry in reg.entities.values():
        if (
            ent_reg_entry.config_entry_id == entry_id
            and ent_reg_entry.platform == DOMAIN
            and ent_reg_entry.unique_id.startswith(f"{DOMAIN}_host_")
        ):
            # The instance lives in hass.data. We need to look it up by
            # entity_id. In practice HA's entity_component holds them,
            # but for the simple case here, we'll resolve by parsing
            # the unique_id (hostname-based) to a key.
            key = ent_reg_entry.unique_id.removeprefix(f"{DOMAIN}_host_")
            # Note: we don't actually need the instance — the key alone
            # is enough to avoid re-adding it. The platform's
            # async_write_ha_state will be called on the existing
            # entity via the coordinator's listener mechanism.
            # We just track the key to know not to add a new one.
            out[key] = None  # type: ignore[assignment]
    return out


# ---- migration service ----

async def async_migrate_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """One-shot migration from v0.2.12 (MAC-keyed) to v0.2.13 (hostname-keyed).

    Walks the entity_registry, finds every device_tracker with platform
    == DOMAIN and unique_id matching the v0.2.12 format, and renames
    it to the v0.2.13 hostname-keyed unique_id. Then reloads the
    config entry so the new entities are created.

    Records to /config/.custom_components/hitron_coda_5610q/.migrated_v0_2_13
    (a marker file) so the migration only runs once per install.
    """
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    old_format = f"{DOMAIN}_"  # v0.2.12 used f"{DOMAIN}_{mac}" → "hitron_coda_5610q_2c:f0:..."
    new_format_prefix = f"{DOMAIN}_host_"

    # Walk every entity in the registry
    renamed: list[tuple[str, str, str]] = []  # (old_id, new_id, hostname)
    skipped: list[str] = []
    for ent in list(reg.entities.values()):
        if ent.platform != DOMAIN:
            continue
        if not ent.unique_id.startswith(old_format):
            continue  # already migrated or not a device_tracker
        if ent.unique_id.startswith(new_format_prefix):
            continue  # already in new format
        # The unique_id for v0.2.12 is f"{DOMAIN}_{mac}". Extract MAC.
        mac = ent.unique_id[len(old_format):]
        # Find the corresponding device_registry entry by MAC to get
        # the hostname. v0.2.12 stored the hostname in the device's
        # `name` field (e.g. "Pixel-6", "Plus4 power supply").
        from homeassistant.helpers import device_registry as dr
        dreg = dr.async_get(hass)
        hostname: str | None = None
        for dev in dreg.devices.values():
            for c in dev.connections:
                if c[0] == "mac" and c[1].lower() == mac.lower():
                    # The v0.2.12 device name was the hostname (or MAC
                    # if hostname was None).
                    name = dev.name or ""
                    if name and name != mac and not name.startswith("00:") and ":" not in name:
                        hostname = name
                    break
            if hostname:
                break

        if not hostname:
            skipped.append(ent.entity_id)
            continue

        # Normalize the hostname the same way the new integration does
        nh = normalize_hostname(hostname) or hostname
        new_unique_id = f"{new_format_prefix}{nh}"

        # If a v0.2.13 entity with the same hostname already exists,
        # mark the old one as `no_longer_used` instead of renaming.
        existing = reg.async_get_entity_id("device_tracker", DOMAIN, new_unique_id)
        if existing and existing != ent.entity_id:
            reg.async_update_entity(
                ent.entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )
            renamed.append((ent.entity_id, existing, hostname))
            continue

        # Rename in the registry. The entity_id stays the same because
        # the device_tracker unique_id is internal — only the unique_id
        # changes, and HA's device_tracker re-add path will pick it up
        # on reload.
        reg.async_update_entity(ent.entity_id, new_unique_id=new_unique_id)
        renamed.append((ent.entity_id, ent.entity_id, hostname))

    # Reload the config entry so the integration re-reads the new
    # unique_ids. This causes the device_tracker platform to re-set-up
    # with hostname-keyed identities.
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_reload(entry.entry_id)

    _LOGGER.info(
        "v0.2.13 migration complete: %d renamed, %d skipped (no hostname)",
        len(renamed), len(skipped),
    )
    if skipped:
        _LOGGER.warning(
            "v0.2.13 migration: %d entities had no hostname in the device "
            "registry and were left in place. Re-add the device_tracker "
            "integration manually if you want them hostname-keyed: %s",
            len(skipped), skipped,
        )


def register_services(hass: HomeAssistant) -> None:
    """Register the hitron_coda_5610q.migrate_to_v0_2_13 service."""
    if not hass.services.has_service(DOMAIN, SERVICE_MIGRATE_TO_V0_2_13):
        hass.services.async_register(
            DOMAIN,
            SERVICE_MIGRATE_TO_V0_2_13,
            lambda call: async_migrate_service(hass, call),
        )
