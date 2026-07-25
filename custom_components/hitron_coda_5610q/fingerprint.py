"""LAN device fingerprinting helpers.

Provides async functions to discover better names / stable identities for
devices whose router-reported hostname is "Unknown" or missing.

Methods used:
  - OUI / manufacturer lookup from MAC
  - mDNS / Zeroconf reverse lookup by IP
  - NetBIOS name query
  - SSDP / UPnP discovery cache lookup
  - User-defined aliases from config entry options
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_ALIASES, CONF_ENABLE_MDNS, DOMAIN
from .oui import lookup_oui

_LOGGER = logging.getLogger(__name__)


def _normalize_mac(mac: str) -> str:
    """Return an upper-case, colon-free MAC string."""
    return mac.upper().replace(":", "").replace("-", "").replace(" ", "")


class DeviceFingerprinter:
    """Collect as much identifying info as possible for a LAN device."""

    def __init__(
        self,
        hass: HomeAssistant,
        options: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.options = options or {}
        self._aliases: dict[str, str] = self.options.get(CONF_DEVICE_ALIASES, {}) or {}

    def alias_for(self, mac: str, current_key: str | None = None) -> str | None:
        """Return a user-defined alias if one exists for the MAC or key."""
        mac_norm = _normalize_mac(mac)
        if mac_norm in self._aliases:
            return self._aliases[mac_norm]
        if current_key and current_key in self._aliases:
            return self._aliases[current_key]
        # Also try the last-known identity key without OUI/manufacturer suffix
        if current_key and "_" in current_key:
            base = current_key.split("_")[0]
            if base in self._aliases:
                return self._aliases[base]
        return None

    def oui_label(self, mac: str) -> str | None:
        """Return a manufacturer label from the MAC OUI, or None."""
        return lookup_oui(mac)

    async def mdns_name(self, ip_address: str) -> str | None:
        """Try a reverse mDNS lookup for the IP address.

        We use a short timeout because not every device advertises mDNS.
        Returns the hostname without the `.local.` suffix.
        """
        if not ip_address or not self.options.get(CONF_ENABLE_MDNS, True):
            return None

        host = ip_address
        try:
            # gethostbyaddr can resolve mDNS via libnss-mdns if installed.
            # Timeout is enforced because it can block for seconds.
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, socket.gethostbyaddr, host),
                timeout=2.0,
            )
            names = result[1] if result else []
            primary = result[0] if result else None
            for name in [primary] + list(names):
                if not name:
                    continue
                name = name.rstrip(".").lower()
                # Filter out generic/reverse mDNS names like 192.168.0.109.in-addr.arpa
                if "in-addr.arpa" in name or name == ip_address.lower():
                    continue
                if name.endswith(".local"):
                    name = name[:-6].rstrip(".")
                if name:
                    return name
        except Exception:
            return None
        return None

    async def netbios_name(self, ip_address: str) -> str | None:
        """Try a NetBIOS name query (port 137 UDP).

        Many Windows and some Android devices answer this. Implementation
        is intentionally minimal: send a status request and parse the first
        name. Disabled if the `netbios-python` package is unavailable.
        """
        if not ip_address:
            return None
        try:
            import netbios  # type: ignore[import-not-found]
        except Exception:
            return None
        try:
            ns = netbios.NetBIOS()
            names = ns.queryIPForName(ip_address, timeout=1)
            if names:
                return str(names[0]).strip().lower()
        except Exception:
            pass
        return None

    def _ssdp_cache_name(self, ip_address: str) -> str | None:
        """Look in HA's SSDP discovery cache for a friendly name by IP."""
        if not ip_address:
            return None
        try:
            ssdp = self.hass.data.get("ssdp")  # type: ignore[attr-defined]
            if not ssdp:
                return None
            for ssdp_st, entries in getattr(ssdp, "_discovered_devices", {}).items():
                for entry in entries:
                    if getattr(entry, "ip_address", None) == ip_address:
                        # Prefer UPnP friendlyName, then modelName, then deviceType
                        upnp = getattr(entry, "upnp", {}) or {}
                        for key in ("friendlyName", "modelName", "deviceType"):
                            val = upnp.get(key)
                            if val:
                                return str(val).strip().lower().replace(" ", "_")
        except Exception:
            return None
        return None

    async def resolve_name(
        self,
        mac: str,
        ip_address: str,
        router_hostname: str | None,
        current_key: str | None = None,
    ) -> tuple[str | None, dict[str, Any]]:
        """Return the best friendly name + a fingerprint dict.

        Priority order:
          1. User-defined alias (from config entry options)
          2. Router hostname if meaningful
          3. mDNS name
          4. NetBIOS name
          5. SSDP / UPnP friendlyName
          6. None — caller should fall back to MAC + optional OUI label
        """
        fingerprint: dict[str, Any] = {
            "mac": mac,
            "ip": ip_address,
            "router_hostname": router_hostname,
            "oui": self.oui_label(mac),
            "mdns": None,
            "netbios": None,
            "ssdp": None,
            "alias": None,
        }

        # 1. User alias always wins
        alias = self.alias_for(mac, current_key)
        if alias:
            fingerprint["alias"] = alias
            return alias, fingerprint

        # 2. Router hostname
        if router_hostname and router_hostname.strip().lower() not in (
            "",
            "unknown",
            "--",
            "<unknown>",
        ):
            return router_hostname.strip().lower(), fingerprint

        # 3. mDNS
        mdns = await self.mdns_name(ip_address)
        if mdns:
            fingerprint["mdns"] = mdns
            return mdns, fingerprint

        # 4. NetBIOS
        netbios = await self.netbios_name(ip_address)
        if netbios:
            fingerprint["netbios"] = netbios
            return netbios, fingerprint

        # 5. SSDP cache
        ssdp = self._ssdp_cache_name(ip_address)
        if ssdp:
            fingerprint["ssdp"] = ssdp
            return ssdp, fingerprint

        return None, fingerprint
