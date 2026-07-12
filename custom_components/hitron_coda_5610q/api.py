"""Async API client for the Hitron CODA-5610Q router."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp
from yarl import URL


class HitronAuthError(Exception):
    """Raised when credentials are invalid."""


class HitronConnectionError(Exception):
    """Raised when the router is unreachable."""


@dataclass(frozen=True)
class ConnectedDevice:
    hostname: str
    ip_address: str
    mac_address: str  # normalized to upper-case colon-separated
    interface: str    # "WiFi 2.4G" | "WiFi 5G" | "Ethernet"
    status: str       # "Active" | "Paused" | "Offline"


class HitronCodaAPI:
    """Thin async wrapper over the CODA-5610Q's web API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._base = URL(f"http://{host}")
        self._username = username
        self._password = password
        self._cookies: dict[str, str] = {}

    async def login(self) -> None:
        """Authenticate and store the session cookie.

        The router expects form-urlencoded with a JSON blob in the
        ``model`` field — NOT a JSON request body.
        """
        url = self._base / "1/Device/Users/Login"
        form = aiohttp.FormData()
        form.add_field(
            "model",
            f'{{"username":"{self._username}","password":"{self._password}"}}',
        )
        async with self._session.post(url, data=form) as resp:
            if resp.status != 200:
                raise HitronConnectionError(f"Login HTTP {resp.status}")
            # Router returns Content-Type: text/html even for JSON
            payload = await resp.json(content_type=None)
            if payload.get("errCode") != "000":
                raise HitronAuthError(payload.get("errMsg", "login failed"))
            # PHPSESSID is HttpOnly — read it from the session jar
            for cookie in self._session.cookie_jar:
                if cookie.key == "PHPSESSID":
                    self._cookies = {"PHPSESSID": cookie.value}
                    return
            raise HitronAuthError("No PHPSESSID cookie set")

    async def get_connected_devices(self) -> list[ConnectedDevice]:
        """Fetch the current LAN device list.

        TODO: Replace the endpoint path below with the one captured
        from the XHR intercept (see KB: xhr-intercept.md). The current
        path is a placeholder that returns "Invalid Paramters."
        """
        url = self._base / "1/Device/CM/ConnectedHost"  # placeholder
        async with self._session.get(url, cookies=self._cookies) as resp:
            if resp.status in (401, 403):
                # Session expired — re-login once
                await self.login()
                async with self._session.get(url, cookies=self._cookies) as resp2:
                    payload = await resp2.json(content_type=None)
            else:
                payload = await resp.json(content_type=None)

            if payload.get("errCode") != "000":
                raise HitronConnectionError(payload.get("errMsg", "fetch failed"))

            return [
                ConnectedDevice(
                    hostname=d.get("hostname", ""),
                    ip_address=d.get("ipAddr", ""),
                    mac_address=d.get("macAddr", "").upper(),
                    interface=d.get("interface", ""),
                    status=d.get("status", ""),
                )
                for d in payload.get("list", [])
            ]

    async def get_system_info(self) -> dict[str, Any]:
        """Return router version, serial, model, etc."""
        url = self._base / "1/Device/CM/Version"
        async with self._session.get(url, cookies=self._cookies) as resp:
            payload = await resp.json(content_type=None)
            return payload

    async def reboot(self) -> None:
        """Reboot the router.

        POST endpoint — the CODA-4680 Go client shows ``/Router/Backup``
        but the 5610Q path is unverified. Implement once the endpoint
        is confirmed via XHR intercept.
        """
        raise NotImplementedError