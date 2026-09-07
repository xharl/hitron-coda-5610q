"""v0.3.0: Presence hysteresis + sticky identity store.

New in this file:
- IdentityStore: persists MAC -> identity key mappings via HA's Store so
  device identities survive restarts and router hostname flaps.
- Hysteresis: trackers remember last_seen; is_connected stays True for a
  grace window after the device disappears from the router list.

Config options (with defaults):
  presence_grace_seconds: 240   # keep "home" for N seconds after last seen
  fast_interval: 30             # seconds for host list / wifi clients
  slow_interval: 300            # seconds for DOCSIS/diagnostic endpoints
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_FAST_INTERVAL,
    CONF_PRESENCE_GRACE,
    CONF_SLOW_INTERVAL,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_PRESENCE_GRACE,
    DEFAULT_SLOW_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_FMT = f"{DOMAIN}.identity_store.{{entry_id}}"


class IdentityStore:
    """Persist MAC -> identity key so entities survive restarts and flaps."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_FMT.format(entry_id=entry_id)
        )
        self._data: dict[str, Any] = {}
        self._loaded = False

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data if isinstance(data, dict) else {}
        self._loaded = True
        _LOGGER.debug("hitron identity store loaded: %d mappings", len(self._data.get("mac_to_key", {})))

    @property
    def mac_to_key(self) -> dict[str, str]:
        return self._data.setdefault("mac_to_key", {})

    @property
    def last_seen(self) -> dict[str, float]:
        return self._data.setdefault("last_seen", {})

    def key_for_mac(self, mac: str) -> str | None:
        """Sticky identity key for a MAC, from a previous session."""
        return self._data.get("mac_to_key", {}).get(_norm(mac))

    def remember(self, mac: str, key: str) -> None:
        self._data.setdefault("mac_to_key", {})[_norm(mac)] = key

    def forget(self, mac: str) -> None:
        self._data.get("mac_to_key", {}).pop(_norm(mac), None)

    def touch(self, mac: str) -> None:
        self._data.setdefault("last_seen", {})[_norm(mac)] = time.time()

    def seen_within(self, mac: str, grace: float) -> bool:
        ts = self._data.get("last_seen", {}).get(_norm(mac))
        if ts is None:
            return False
        return (time.time() - ts) <= grace

    async def async_save(self) -> None:
        if self._loaded:
            await self._store.async_save(self._data)


def _norm(mac: str) -> str:
    return mac.upper().replace(":", "").replace("-", "")


@dataclass
class TieredPollPlan:
    """Endpoints split into fast/slow tiers."""

    fast: tuple[str, ...]
    slow: tuple[str, ...]
    slow_every: int  # run slow tier every N fast cycles

    def slow_due(self, cycle: int) -> bool:
        return cycle % self.slow_every == 0


async def build_tiered_poll_plan(options: dict[str, Any]) -> TieredPollPlan:
    """Build the fast/slow polling plan from options."""
    fast = int(options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL))
    slow = int(options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL))
    slow_every = max(1, slow // max(fast, 1))
    return TieredPollPlan(
        fast=("devices", "wifi_clients"),
        slow=(
            "system_info",
            "router_sys_info",
            "ds_channels",
            "us_channels",
            "reservations",
            "docs_prov",
            "cm_sys",
            "wifi_radios",
            "firewall",
            "eth_ports",
        ),
        slow_every=slow_every,
    )


def grace_seconds(options: dict[str, Any]) -> float:
    """Presence grace window in seconds from options."""
    try:
        return max(0, int(options.get(CONF_PRESENCE_GRACE, DEFAULT_PRESENCE_GRACE)))
    except (TypeError, ValueError):
        return float(DEFAULT_PRESENCE_GRACE)


@dataclass
class DevicePresence:
    """Tracks last_seen per MAC for hysteresis decisions."""

    grace: float
    _last_seen: dict[str, float] = field(default_factory=dict)

    def observe(self, mac: str) -> None:
        """Record that a MAC is present right now."""
        self._last_seen[_norm(mac)] = time.time()

    def still_home(self, mac: str) -> bool:
        """True if the MAC was seen within the grace window."""
        ts = self._last_seen.get(_norm(mac))
        return ts is not None and (time.time() - ts) <= self.grace

    def prune(self, keep: set[str]) -> None:
        """Drop entries for MACs no longer tracked at all."""
        stale = [m for m in self._last_seen if m not in keep]
        for m in stale:
            self._last_seen.pop(m, None)


async def async_sleep_or_cancel(delay: float, event: asyncio.Event) -> None:
    """Sleep that aborts promptly when the event is set."""
    try:
        await asyncio.wait_for(event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass


def interval_from_options(options: dict[str, Any], key: str, default: int, lo: int, hi: int) -> timedelta:
    try:
        val = int(options.get(key, default))
    except (TypeError, ValueError):
        val = default
    return timedelta(seconds=max(lo, min(val, hi)))