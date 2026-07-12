"""Async API client for the Hitron CODA-5610Q router.

Reverse-engineered from the router's Backbone.js SPA. The router uses
Fat-Free Framework (PHP) on the backend and returns JSON with
Content-Type: text/html for all API responses.

Key findings:
  - Login: POST /1/Device/Users/Login with form-urlencoded model=JSON
  - Session: PHPSESSID cookie (HttpOnly), GET-only for data endpoints
  - Device list: GET /1/Device/Hosts/1 (the /1 is a page/instance id)
  - DOCSIS: GET /1/Device/CM/DsInfo, /1/Device/CM/UsInfo
  - POST writes require a csrf token from GET /1/Device/Users/CSRF
  - Static JS files are at /webpages/js/ and /webpages/lib/ (not /js/, /lib/)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)


class HitronAuthError(Exception):
    """Raised when credentials are invalid."""


class HitronConnectionError(Exception):
    """Raised when the router is unreachable."""


@dataclass(frozen=True)
class ConnectedDevice:
    """A device connected to the router's LAN."""

    hostname: str
    ip_address: str
    mac_address: str  # normalized to upper-case colon-separated
    interface: str    # "WiFi 2.4G" | "WiFi 5G" | "Ethernet"
    address_source: str  # "DHCP-IP" | "DHCP-Reservation" | "Self-assigned"
    status: bool       # True = Active, False = Paused/Offline
    action: str        # "Resume" | "Pause"


@dataclass(frozen=True)
class DownstreamChannel:
    """A DOCSIS downstream channel."""

    port_id: str
    frequency: str       # Hz
    modulation: str      # "QAM256" etc
    signal_strength: str  # dBmV
    snr: str             # dB
    channel_id: str
    correcteds: str
    uncorrectables: str


@dataclass(frozen=True)
class UpstreamChannel:
    """A DOCSIS upstream channel."""

    port_id: str
    frequency: str       # Hz
    modulation_type: str  # "64QAM" etc
    signal_strength: str  # dBmV
    bandwidth: str       # Hz
    channel_id: str


@dataclass(frozen=True)
class SystemInfo:
    """Router system information."""

    serial_number: str
    model_name: str
    hardware_version: str
    software_version: str
    api_version: str
    vendor_name: str
    device_id: str       # MAC address
    deployment_name: str
    wifi_chip: str


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
        self._csrf_token: str | None = None

    async def _request_json(self, url: URL) -> dict[str, Any]:
        """GET an endpoint and return parsed JSON.

        The router returns Content-Type: text/html for JSON, so we
        must use content_type=None. If the response looks like a
        login page redirect, we re-login once.
        """
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": str(self._base / "webpages/index.html"),
        }
        async with self._session.get(
            url, cookies=self._cookies, headers=headers
        ) as resp:
            if resp.status in (401, 403):
                # Session expired — re-login once
                await self.login()
                async with self._session.get(
                    url, cookies=self._cookies, headers=headers
                ) as resp2:
                    return await resp2.json(content_type=None)
            return await resp.json(content_type=None)

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
            payload = await resp.json(content_type=None)
            if payload.get("errCode") != "000":
                raise HitronAuthError(payload.get("errMsg", "login failed"))
            for cookie in self._session.cookie_jar:
                if cookie.key == "PHPSESSID":
                    self._cookies = {"PHPSESSID": cookie.value}
                    return
            raise HitronAuthError("No PHPSESSID cookie set")

    async def get_connected_devices(self) -> list[ConnectedDevice]:
        """Fetch the current connected device list.

        Endpoint: GET /1/Device/Hosts/1
        The /1 is an instance/page id, not a device id.
        """
        url = self._base / "1/Device/Hosts/1"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            raise HitronConnectionError(payload.get("errMsg", "fetch failed"))

        devices: list[ConnectedDevice] = []
        for d in payload.get("Hosts_List", []):
            devices.append(
                ConnectedDevice(
                    hostname=d.get("hostName", ""),
                    ip_address=d.get("ip", ""),
                    mac_address=d.get("macAddr", "").upper(),
                    interface=d.get("connectType", ""),
                    address_source=d.get("addressSource", ""),
                    status=d.get("status", 0) == 1,
                    action=d.get("action", ""),
                )
            )
        return devices

    async def get_system_info(self) -> SystemInfo:
        """Return router version and identification."""
        url = self._base / "1/Device/CM/Version"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            raise HitronConnectionError(payload.get("errMsg", "fetch failed"))

        return SystemInfo(
            serial_number=payload.get("SerialNum", ""),
            model_name=payload.get("modelName", ""),
            hardware_version=payload.get("HwVersion", ""),
            software_version=payload.get("SoftwareVersion", ""),
            api_version=payload.get("ApiVersion", ""),
            vendor_name=payload.get("vendorName", ""),
            device_id=payload.get("deviceId", ""),
            deployment_name=payload.get("DeploymentName", ""),
            wifi_chip=payload.get("wifiChip", ""),
        )

    async def get_router_sys_info(self) -> dict[str, Any]:
        """Return WAN/LAN status, uptime, traffic stats."""
        url = self._base / "1/Device/Router/SysInfo"
        return await self._request_json(url)

    async def get_downstream_channels(self) -> list[DownstreamChannel]:
        """Fetch DOCSIS downstream channel info (SNR, power, freq)."""
        url = self._base / "1/Device/CM/DsInfo"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            raise HitronConnectionError(payload.get("errMsg", "fetch failed"))

        channels: list[DownstreamChannel] = []
        for ch in payload.get("Freq_List", []):
            channels.append(
                DownstreamChannel(
                    port_id=ch.get("portId", ""),
                    frequency=ch.get("frequency", ""),
                    modulation=ch.get("modulation", ""),
                    signal_strength=ch.get("signalStrength", ""),
                    snr=ch.get("snr", ""),
                    channel_id=ch.get("channelId", ""),
                    correcteds=ch.get("correcteds", ""),
                    uncorrectables=ch.get("uncorrectables", ""),
                )
            )
        return channels

    async def get_upstream_channels(self) -> list[UpstreamChannel]:
        """Fetch DOCSIS upstream channel info (power, freq)."""
        url = self._base / "1/Device/CM/UsInfo"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            raise HitronConnectionError(payload.get("errMsg", "fetch failed"))

        channels: list[UpstreamChannel] = []
        for ch in payload.get("Freq_List", []):
            channels.append(
                UpstreamChannel(
                    port_id=ch.get("portId", ""),
                    frequency=ch.get("frequency", ""),
                    modulation_type=ch.get("modulationType", ""),
                    signal_strength=ch.get("signalStrength", ""),
                    bandwidth=ch.get("bandwidth", ""),
                    channel_id=ch.get("channelId", ""),
                )
            )
        return channels

    async def get_wifi_clients(self) -> list[dict[str, Any]]:
        """Fetch WiFi-associated clients with RSSI per client.

        Endpoint: GET /1/Device/WiFi/Client
        Returns hostname, MAC, band, SSID, RSSI, bitrate, channel, bandwidth.
        """
        url = self._base / "1/Device/WiFi/Client"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            return []

        return [
            {
                "hostname": c.get("hostname", ""),
                "mac_address": c.get("mac", "").upper(),
                "band": c.get("band", ""),
                "ssid": c.get("ssid", ""),
                "rssi": c.get("rssi", ""),
                "bitrate": c.get("br", ""),
                "channel": c.get("ch", ""),
                "bandwidth": c.get("bw", ""),
                "phy_mode": c.get("pm", ""),
            }
            for c in payload.get("Client_List", [])
        ]

    async def get_docsis_provisioning(self) -> dict[str, Any]:
        """Fetch DOCSIS provisioning status.

        Endpoint: GET /1/Device/CM/DocsisProvision
        Returns per-step status: hwInit, findDownstream, ranging, dhcp, etc.
        """
        url = self._base / "1/Device/CM/DocsisProvision"
        return await self._request_json(url)

    async def get_cm_sys_info(self) -> dict[str, Any]:
        """Fetch cable modem system info (CM IP, lease, data rates).

        Endpoint: GET /1/Device/CM/SysInfo
        """
        url = self._base / "1/Device/CM/SysInfo"
        return await self._request_json(url)

    async def get_wifi_radios(self) -> list[dict[str, Any]]:
        """Fetch WiFi radio configuration (2.4G/5G bands).

        Endpoint: GET /1/Device/WiFi/Radios
        """
        url = self._base / "1/Device/WiFi/Radios"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            return []

        return [
            {
                "band": r.get("band", ""),
                "on_off": r.get("wlsOnOff", ""),
                "channel": r.get("wlsCurrentChannel", ""),
                "mode": r.get("wlsMode", ""),
                "supported_mode": r.get("supportedMode", ""),
                "bandwidth": r.get("n_bandwidth", ""),
                "auto_channel": r.get("autoChannel", ""),
                "wps": r.get("wlswpsOnOff", ""),
            }
            for r in payload.get("Raidos_List", [])
        ]

    async def get_firewall_status(self) -> dict[str, Any]:
        """Fetch firewall level and rules status.

        Endpoint: GET /1/Device/Firewall/Level
        """
        url = self._base / "1/Device/Firewall/Level"
        return await self._request_json(url)

    async def get_ethernet_ports(self) -> list[dict[str, Any]]:
        """Fetch Ethernet port status (link, speed, duplex).

        Endpoint: GET /1/Device/Advanced/AdvancedSwitch
        """
        url = self._base / "1/Device/Advanced/AdvancedSwitch"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            return []

        return [
            {
                "port_id": p.get("portId", ""),
                "power": p.get("power", ""),
                "speed": p.get("speed", ""),
                "duplex": p.get("duplex", ""),
                "linked": p.get("linked", ""),
                "is_wan": p.get("isWan", False),
            }
            for p in payload.get("advSwitch_List", [])
        ]

    async def get_dhcp_reservations(self) -> list[dict[str, str]]:
        """Fetch DHCP reservations."""
        url = self._base / "1/Device/DHCP/Reservation"
        payload = await self._request_json(url)

        if payload.get("errCode") != "000":
            return []

        return [
            {
                "hostname": r.get("hostName", ""),
                "mac_address": r.get("macAddr", "").upper(),
                "ip_address": r.get("ipAddr", ""),
            }
            for r in payload.get("DHCPReserv_List", [])
        ]

    async def _get_csrf_token(self) -> str:
        """Fetch a fresh CSRF token for POST requests."""
        url = self._base / "1/Device/Users/CSRF"
        payload = await self._request_json(url)
        token = payload.get("CSRF", "")
        if not token:
            raise HitronConnectionError("No CSRF token returned")
        self._csrf_token = token
        return token

    async def pause_device(self, mac_address: str) -> None:
        """Pause a device's internet access (needs CSRF token).

        Endpoint: POST /1/Device/Hosts/Pause
        Body: model=<JSON>&csrf=<token>
        """
        csrf = await self._get_csrf_token()
        url = self._base / "1/Device/Hosts/Pause"
        form = aiohttp.FormData()
        form.add_field(
            "model",
            f'{{"macAddr":"{mac_address}","action":"Pause"}}',
        )
        form.add_field("csrf", csrf)
        async with self._session.post(
            url, data=form, cookies=self._cookies
        ) as resp:
            payload = await resp.json(content_type=None)
            if payload.get("errCode") != "000":
                raise HitronConnectionError(
                    payload.get("errMsg", "pause failed")
                )

    async def resume_device(self, mac_address: str) -> None:
        """Resume a paused device's internet access.

        Endpoint: POST /1/Device/Hosts/Pause
        Body: model=<JSON>&csrf=<token>
        """
        csrf = await self._get_csrf_token()
        url = self._base / "1/Device/Hosts/Pause"
        form = aiohttp.FormData()
        form.add_field(
            "model",
            f'{{"macAddr":"{mac_address}","action":"Resume"}}',
        )
        form.add_field("csrf", csrf)
        async with self._session.post(
            url, data=form, cookies=self._cookies
        ) as resp:
            payload = await resp.json(content_type=None)
            if payload.get("errCode") != "000":
                raise HitronConnectionError(
                    payload.get("errMsg", "resume failed")
                )