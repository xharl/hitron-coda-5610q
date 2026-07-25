# Hitron device_tracker debugging journal

## Goal
Make the custom Hitron CODA-5610Q integration reliably create router-based `device_tracker` entities that show `home`/`not_home`, survive MAC rotation, and give friendly names (via hostname, mDNS, OUI, or user aliases).

## Current state (HAOS 18.1, HA Core 2026.7.4, Pi 5)
- Integration: `hitron_coda_5610q` v0.2.15, commit `34bc63d` on `xharl/hitron-coda-5610q:main`
- Config entry: `entry_id=01KYCQPR3Y6FBZ2896K6PJQ2G5`, `unique_id=AN6025101226`
- Router behaviour: live host endpoint returns `hostName: "Unknown"` for every device; DHCP reservation table contains real names (`homeassistant`, `ubuntu-server`, `xEndeavour`)
- After latest clean wipe + restart: **0 device_tracker entities** (previously 19 existed but were unavailable)

## What has been tried

### v0.2.13 / earlier
- Tracked by MAC → entities exploded every time phone randomized MAC.
- Tried hostname tracking → all devices reported `hostName: "Unknown"`, causing colliding `host_unknown` entities.

### v0.2.14
- Patched `normalize_hostname()` to treat `"Unknown"`, `"--"`, `"<unknown>"` as no hostname.
- `make_entity_unique_id()` includes disambiguation key + MAC-derived suffix for empty hostnames.
- DHCP-reservation fallback in `_build_identities()` to get real names for static hosts.
- Fixed `_existing_trackers_by_key()` to require `domain == "device_tracker"` (later changed to `entity_id.startswith("device_tracker.")`).

### v0.2.15
1. Added mDNS/Zeroconf, NetBIOS, SSDP fallback in `fingerprint.py`.
2. Added OUI lookup in `oui.py`.
3. Added user aliases in config options in `config_flow.py`.
4. Reworked entity lifecycle to use a `hass.data[DOMAIN][f"{entry_id}_trackers"]` cache so MAC rotation updates existing entities instead of creating duplicates.
5. Added explicit `async_update_device()` in `async_added_to_hass()` to refresh device-registry name/manufacturer.
6. Set `_attr_has_entity_name = True` then `False`; tried `_attr_name` and `name` property overrides.
7. Set `_attr_source_type = SourceType.ROUTER`.
8. Set `_attr_entity_category = None` to override `BaseTrackerEntity` default of `EntityCategory.DIAGNOSTIC`.
9. Set `_attr_capability_attributes = {TRACKING_TYPE: TrackingType.CONNECTION}` for router-based trackers.

### Result after each iteration
- Entities are created (seen 18/19 at various points) but:
  - They appear as `entity_category: "diagnostic"` despite setting `_attr_entity_category = None`.
  - They show as `unavailable` in the UI.
  - No `home`/`not_home` state is written to the state DB (`states` table has 0 device_tracker rows).
  - Latest clean registry wipe caused 0 entities to be recreated.

## Suspected root causes
- `BaseTrackerEntity` in HA 2026.7.4 defaults `_attr_entity_category = EntityCategory.DIAGNOSTIC`. Setting class-level `_attr_entity_category = None` on a `TrackerEntity` subclass does not override because of how `__init_subclass__`/cached properties work, or because the registry cached it from a previous run.
- `TrackerEntity` default capability is `TrackingType.POSITION`. For router-based presence we likely need `TrackingType.CONNECTION`, but `TrackerEntity` may not be the right base class for this use case.
- HA 2026.7 may require router-based trackers to subclass `ScannerEntity` (a `BaseScannerEntity`) rather than `TrackerEntity`, because `TrackerEntity` is intended for GPS-style position trackers.

## Next steps to try
1. Investigate HA 2026.7.4 `device_tracker` architecture: should router scanners inherit `ScannerEntity` instead of `TrackerEntity`?
2. If using `ScannerEntity`, implement `async_scanner_update()` or `async_see_device()` pattern.
3. Verify whether `_attr_entity_category` can be overridden on `TrackerEntity` or if the base class forces diagnostic.
4. Check HA Core logs for silent failures during `async_setup_entry` (no traceback currently visible).
5. Consider reverting to a simpler custom entity without `TrackerEntity` base and manually writing `home`/`not_home` states, or using the legacy `device_tracker.see` service.

## Files changed
- `custom_components/hitron_coda_5610q/device_tracker.py`
- `custom_components/hitron_coda_5610q/fingerprint.py` (new)
- `custom_components/hitron_coda_5610q/oui.py` (new)
- `custom_components/hitron_coda_5610q/config_flow.py`
- `custom_components/hitron_coda_5610q/const.py`
- `custom_components/hitron_coda_5610q/manifest.json`
