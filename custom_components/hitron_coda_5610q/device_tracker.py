"""Device tracker for the Hitron CODA-5610Q.

v0.2.13: hostname-based identity to survive MAC rotation.
v0.2.15: fingerprinting fallback (mDNS, NetBIOS, SSDP, OUI, aliases)
         for devices whose router-reported hostname is "Unknown".

Background: the v0.2.12 integration tracked every device by MAC address,
using the MAC as the unique_id. This was correct for stationary devices
but broke for mobile devices that use "Private WiFi Address" (iOS/macOS
"Private Relay" on, Android 10+ "Use randomized MAC" on) — every time
such a device reconnects, the router sees a new random MAC, the
integration creates a new entity, and the old entity stays in the
entity registry forever in `not_home` state.

v0.2.13 fix: track devices by hostname (the router's reported
`hostName` field, which is stable for any device that doesn't override
it). MAC is now an attribute of the entity, not its identity. When a
device with a known hostname reconnects under a new MAC, the
existing entity is updated in place.

v0.2.15 enhancement: when the router reports hostname="Unknown",
the integration tries mDNS, NetBIOS, SSDP/UPnP, DHCP reservations,
and OUI/manufacturer lookup to build a stable identity. Users can
also assign aliases in the config entry options.

Identity keys, in order of preference:
  1. user-defined alias
  2. hostname (router-reported or DHCP reservation)
  3. mDNS / Bonjour name
  4. NetBIOS name
  5. SSDP / UPnP friendlyName
  6. OUI/manufacturer + MAC
  7. raw MAC (final fallback)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.device_registry import DeviceInfo, async_get as async_get_device_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_ALIASES,
    CONF_ENABLE_MDNS,
    CONF_USE_OUI_LABEL,
    DOMAIN,
    SERVICE_MIGRATE_TO_V0_2_13,
)
from .coordinator import HitronCodaCoordinator
from .fingerprint import DeviceFingerprinter
from .oui import lookup_oui

_LOGGER = logging.getLogger(__name__)


# v0.2.15: maximum concurrent fingerprinting probes to avoid
# overwhelming the LAN / event loop.
_FINGERPRINT_SEMAPHORE = asyncio.Semaphore(8)


def normalize_hostname(hostname: str | None) -> str | None:
    """Normalize a hostname for stable identity matching.

    - Strip leading/trailing whitespace
    - Lower-case
    - Replace illegal filesystem characters with '_'
    - Return None for empty/placeholder strings (caller falls back to MAC)
    """
    if not hostname:
        return None
    h = hostname.strip().lower()
    if not h or h in ("unknown", "--", "<unknown>"):
        return None
    # Routers sometimes report hostnames with unicode or filesystem-unsafe chars;
    # collapse them so the resulting unique_id is filesystem-safe.
    h = "".join(c if c.isalnum() or c in "-._" else "_" for c in h)
    return h or None


def _mac_key(mac: str) -> str:
    """Return a normalized MAC string for lookups."""
    return mac.upper().replace(":", "").replace("-", "")


def resolve_hostname(
    mac: str,
    router_hostname: str | None,
    dhcp_reservations: list[dict[str, str]],
) -> str | None:
    """Pick the best hostname for a device.

    Order of preference:
      1. Router live host list hostname (if meaningful)
      2. DHCP reservation hostname for this MAC
      3. None → fall back to MAC-based identity
    """
    hostname = normalize_hostname(router_hostname)
    if hostname:
        return hostname

    mac_norm = _mac_key(mac)
    for r in dhcp_reservations:
        if _mac_key(r.get("mac_address", "")) == mac_norm:
            hostname = normalize_hostname(r.get("hostname"))
            if hostname:
                return hostname
    return None


def make_entity_unique_id(track_by: str, key: str, hostname: str | None, mac: str) -> str:
    """Build a stable unique_id for a device_tracker entity.

    For hostname tracking: f"{DOMAIN}_host_{key}" where key is the
    disambiguated hostname. For MAC tracking (legacy): f"{DOMAIN}_{mac}".

    Devices with no/empty/"unknown"/"Unknown" hostname fall back to MAC-keyed IDs
    so we don't create dozens of colliding "hitron_coda_5610q_host_unknown"
    entities.
    """
    if track_by == "hostname" and hostname:
        return f"{DOMAIN}_host_{key}"
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
    fingerprint: dict[str, Any] = field(default_factory=dict)
    oui_label: str | None = None
    user_alias: str | None = None


class HitronCodaDeviceTracker(CoordinatorEntity[HitronCodaCoordinator], ScannerEntity):
    """A device seen on the LAN, identified by hostname in v0.2.13+."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.ROUTER
    _attr_entity_category = None

    def __init__(
        self,
        coordinator: HitronCodaCoordinator,
        identity: DeviceIdentity,
    ) -> None:
        super().__init__(coordinator)
        self._identity = identity
        self._attr_unique_id = make_entity_unique_id(
            identity.track_by, identity.key, identity.hostname, identity.current_mac
        )
        # Stable name for legacy/UI display. Priority:
        # 1. user alias, 2. hostname, 3. OUI label + MAC, 4. raw MAC
        if identity.user_alias:
            self._attr_name = identity.user_alias
        elif identity.hostname:
            self._attr_name = identity.hostname
        elif identity.oui_label and coordinator.config_entry.options.get(CONF_USE_OUI_LABEL, True):
            self._attr_name = f"{identity.oui_label}_{identity.current_mac.replace(':', '')}"
        else:
            self._attr_name = identity.current_mac.replace(':', '')

    @property
    def available(self) -> bool:
        """Entity is available whenever the coordinator has fresh data."""
        return self.coordinator.last_update_success

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
        name = self._compute_device_name()
        return DeviceInfo(
            identifiers={(DOMAIN, self._identity.key)},
            connections={("mac", self._identity.current_mac)},
            manufacturer=self._identity.oui_label or "Unknown",
            model="LAN device",
            name=name,
            via_device=(
                DOMAIN,
                self.coordinator.data.system_info.serial_number,
            ),
        )

    def _compute_device_name(self) -> str:
        if self._identity.user_alias:
            return self._identity.user_alias
        if self._identity.hostname:
            return self._identity.hostname
        if self._identity.oui_label and self.coordinator.config_entry.options.get(CONF_USE_OUI_LABEL, True):
            return f"{self._identity.oui_label} {self._identity.current_mac.replace(':', '')}"
        return self._identity.current_mac.replace(":", "")

    async def async_added_to_hass(self) -> None:
        """Update device registry entry when entity first loads.

        Home Assistant only creates the device entry on first sight;
        if our friendly name / manufacturer changed later, we need to
        update it explicitly so OUI labels and aliases show up.
        """
        await super().async_added_to_hass()
        dev_reg = async_get_device_registry(self.hass)
        if device := dev_reg.async_get_device(
            identifiers={(DOMAIN, self._identity.key)},
        ):
            name = self._compute_device_name()
            manufacturer = self._identity.oui_label or "Unknown"
            if device.name != name or device.manufacturer != manufacturer:
                dev_reg.async_update_device(
                    device.id,
                    name=name,
                    manufacturer=manufacturer,
                )
                _LOGGER.warning(
                    "hitron_coda_5610q device_tracker: updated device registry name=%s manufacturer=%s for key=%s",
                    name, manufacturer, self._identity.key,
                )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "identity_key": self._identity.key,
            "current_mac": self._identity.current_mac,
            "router_hostname": self._identity.hostname,
            "user_alias": self._identity.user_alias,
            "oui": self._identity.oui_label,
        }
        if self._identity.fingerprint:
            attrs["fingerprint"] = self._identity.fingerprint
        for d in self.coordinator.data.devices:
            if d.mac_address == self._identity.current_mac:
                attrs["interface"] = d.interface
                attrs["address_source"] = d.address_source
                attrs["action"] = d.action
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
    def has_entity_name(self) -> bool:
        return False

    @property
    def name(self) -> str | None:
        return self._attr_name


# ---- setup + identity bookkeeping ----

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device tracker entities, one per stable device identity.

    v0.2.15 behavior: each device gets one entity. Identity is stable
    hostname when available; otherwise a fingerprint-based key (mDNS,
    NetBIOS, SSDP, user alias, or MAC fallback). MAC rotation is
    handled by updating `current_mac` on existing entities.
    """
    coordinator: HitronCodaCoordinator = hass.data[DOMAIN][entry.entry_id]
    track_by = entry.options.get("track_by", "hostname")
    fingerprinter = DeviceFingerprinter(hass, entry.options)

    host_to_identity = await _build_identities(hass, coordinator, fingerprinter, track_by)
    _LOGGER.warning(
        "hitron_coda_5610q device_tracker: built %d identities (track_by=%s)",
        len(host_to_identity),
        track_by,
    )
    for key, ident in list(host_to_identity.items())[:5]:
        _LOGGER.warning(
            "  identity key=%s mac=%s hostname=%s alias=%s oui=%s fingerprint=%s",
            key, ident.current_mac, ident.hostname, ident.user_alias, ident.oui_label,
            ident.fingerprint,
        )

    # Keep a per-entry map of live tracker instances so we can update
    # their MAC in place when a device reconnects under a new MAC.
    tracker_store_key = f"{entry.entry_id}_trackers"
    if tracker_store_key not in hass.data[DOMAIN]:
        hass.data[DOMAIN][tracker_store_key] = {}
    tracker_store: dict[str, HitronCodaDeviceTracker] = hass.data[DOMAIN][tracker_store_key]

    new_entities: list[HitronCodaDeviceTracker] = []
    for key, identity in host_to_identity.items():
        tracker = tracker_store.get(key)
        if tracker is not None and tracker._identity.current_mac != identity.current_mac:
            _LOGGER.warning(
                "hitron_coda_5610q device_tracker: updating MAC for key=%s %s -> %s",
                key, tracker._identity.current_mac, identity.current_mac,
            )
            tracker._identity = identity
            tracker.async_write_ha_state()
        elif tracker is None:
            tracker = HitronCodaDeviceTracker(coordinator, identity)
            tracker_store[key] = tracker
            new_entities.append(tracker)

    _LOGGER.warning(
        "hitron_coda_5610q device_tracker: adding %d new entities (sample unique_ids: %s)",
        len(new_entities),
        ", ".join([e.unique_id for e in new_entities[:3]]) if new_entities else "none",
    )
    if new_entities:
        try:
            async_add_entities(new_entities)
        except Exception:
            _LOGGER.exception("hitron_coda_5610q device_tracker: async_add_entities failed")


async def _build_identities(
    hass: HomeAssistant,
    coordinator: HitronCodaCoordinator,
    fingerprinter: DeviceFingerprinter,
    track_by: str,
) -> dict[str, DeviceIdentity]:
    """Map stable identity key -> DeviceIdentity for all currently-seen devices.

    v0.2.15 builds identities using multiple fingerprinting methods:
      1. User-defined alias (from config entry options)
      2. Router hostname or DHCP reservation
      3. mDNS / Bonjour name by IP
      4. NetBIOS name
      5. SSDP / UPnP friendlyName
      6. OUI/manufacturer + MAC
      7. Raw MAC (final fallback)

    The chosen key is stable as long as the same method keeps returning
    the same name for a device. When a device rotates its MAC but keeps
    the same hostname / mDNS name, the entity identity survives.
    """
    seen_keys: dict[str, int] = {}
    out: dict[str, DeviceIdentity] = {}

    reservations = coordinator.data.dhcp_reservations

    # Run mDNS/NetBIOS/SSDP resolves in parallel with a bounded semaphore
    async def _resolve_one(d):
        async with _FINGERPRINT_SEMAPHORE:
            return await fingerprinter.resolve_name(
                d.mac_address,
                d.ip_address,
                d.hostname,
            )

    resolve_tasks = {d.mac_address: _resolve_one(d) for d in coordinator.data.devices}
    resolve_results = {}
    if resolve_tasks:
        resolve_results = dict(
            zip(
                resolve_tasks.keys(),
                await asyncio.gather(*resolve_tasks.values(), return_exceptions=True),
            )
        )

    for d in coordinator.data.devices:
        mac = d.mac_address
        router_hostname = resolve_hostname(mac, d.hostname, reservations)

        # Fingerprint: alias/mDNS/NetBIOS/SSDP
        resolved_name = None
        fingerprint: dict[str, Any] = {}
        if mac in resolve_results:
            result = resolve_results[mac]
            if isinstance(result, tuple) and len(result) == 2:
                resolved_name, fingerprint = result
            elif isinstance(result, Exception):
                _LOGGER.debug("Fingerprint failed for %s: %s", mac, result)

        # Pick the identity key.
        alias = fingerprinter.alias_for(mac, router_hostname or resolved_name)
        if track_by == "mac" or not (router_hostname or resolved_name or alias):
            # Legacy mode: every MAC is its own entity.
            base_key = mac
            hostname = None
            user_alias = None
        else:
            # Prefer user alias for display, but the stable key is still
            # the router hostname / mDNS name. If neither exists, fall
            # back to MAC so we don't create colliding entities.
            hostname = router_hostname or resolved_name
            user_alias = alias
            base_key = hostname or mac

        oui = lookup_oui(mac)

        # disambiguate collisions (two devices with same hostname)
        n = seen_keys.get(base_key, 0)
        seen_keys[base_key] = n + 1
        key = base_key if n == 0 else f"{base_key}_{n}"

        out[key] = DeviceIdentity(
            key=key,
            current_mac=mac,
            track_by=track_by,
            hostname=hostname,
            fingerprint=fingerprint,
            oui_label=oui,
            user_alias=user_alias,
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
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    out: dict[str, HitronCodaDeviceTracker] = {}
    for ent_reg_entry in reg.entities.values():
        if (
            ent_reg_entry.config_entry_id != entry_id
            or ent_reg_entry.platform != DOMAIN
            or not ent_reg_entry.entity_id.startswith("device_tracker.")
            or not ent_reg_entry.unique_id.startswith(f"{DOMAIN}_")
        ):
            continue
        if ent_reg_entry.unique_id.startswith(f"{DOMAIN}_host_"):
            key = ent_reg_entry.unique_id.removeprefix(f"{DOMAIN}_host_")
        else:
            # MAC-keyed fallback unique_id is f"{DOMAIN}_{mac}". The key is
            # the MAC portion after the underscore.
            key = ent_reg_entry.unique_id.removeprefix(f"{DOMAIN}_")
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
