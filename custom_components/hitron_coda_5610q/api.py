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
   - Session: PHPSESSID expires server-side after 10 idle minutes
     (mainApp.js: SessionTimeout = 10 * 60, kept alive by the SPA's
     Users/Alive heartbeat).
   - CRITICAL (v0.3.2, verified live + via browser HAR): the router
     signals a missing/expired session with HTTP 200 + the SPA login
     page (HTML) — never with 401/403. v0.3.1 misread every HTML body
     as "firmware degraded the endpoint" and stopped re-logging in,
     wedging the integration after every restart (the API client never
     held a valid session). The HTML response is now treated as a lost
     session first: one bounded re-login (throttled to one attempt per
     minute across all endpoints) + one retry. Only HTML that survives
     a fresh login is classified as the genuine firmware degradation
     (HitronEndpointDegradedError), which re-login provably does not
     clear.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

# v0.3.2: session lifecycle constants, from the router's own SPA
# (mainApp.js: SessionTimeout = 10 * 60 seconds, kept alive by a
# Users/Alive heartbeat).
_SESSION_IDLE_EXPIRY = 10 * 60          # router kills idle sessions after 10 min
_RELOGIN_MIN_INTERVAL = 60              # min spacing between re-login attempts


class HitronAuthError(Exception):
    """Raised when credentials are invalid."""


class HitronConnectionError(Exception):
    """Raised when the router is unreachable."""


class HitronEndpointDegradedError(HitronConnectionError):
    """Raised when an endpoint serves an HTML page instead of JSON.

    v0.3.2 reclassification (verified live + browser HAR): the router
    serves the SPA login page BOTH when the session is missing/expired
    AND during the genuine firmware degradation. The difference is only
    observable after a fresh login: an expired session returns JSON for
    the new PHPSESSID, a degraded endpoint keeps serving HTML. The API
    client therefore tries one bounded re-login on HTML
    (``_request_json``) and only raises this error when HTML survives a
    fresh session — the v0.3.1-verified condition that re-login does
    not clear. The ``endpoint`` attribute carries the path of the
    affected endpoint so the coordinator can report exactly which
    endpoints are degraded.
    """

    def __init__(self, message: str, endpoint: str = "") -> None:
        super().__init__(message)
        self.endpoint = endpoint


def _raise_if_html(url: URL, text: str) -> None:
    """Raise HitronEndpointDegradedError when the body is an HTML page.

    The router serves its JSON with a Content-Type of text/html, so the
    header cannot be trusted to detect the degradation — the body must
    be inspected instead. JSON payloads always start with '{' or '[';
    the degraded firmware serves the Backbone SPA's login page, which
    starts with '<'.
    """
    if text.lstrip()[:1] == "<":
        raise HitronEndpointDegradedError(
            f"{url.path} returned an HTML page instead of JSON",
            endpoint=url.path,
        )


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
        # v0.3.0: single-flight login. The CODA issues a fresh PHPSESSID
        # per login; overlapping logins (e.g. 12 parallel request retries)
        # invalidate each other's session and cause 401 storms.
        self._login_lock = asyncio.Lock()
        # v0.3.2: login bookkeeping for the HTML→re-login path.
        # _last_login_at throttles re-login storms (one attempt per
        # minute): all endpoints share one cookie jar (self._cookies is
        # passed to every GET), so ONE fresh login fixes every
        # concurrent HTML response in the same gather wave — later
        # callers see a fresh timestamp and skip straight to retrying
        # with the new cookie. Initialized to the far past so the first
        # keepalive/HTML recovery on a fresh process actually logs in.
        self._last_login_at: float = -(_SESSION_IDLE_EXPIRY + 1)

    async def _request_json(self, url: URL) -> dict[str, Any]:
        """GET an endpoint and return parsed JSON.

        The router returns Content-Type: text/html for JSON, so we
        must use content_type=None.

        v0.3.2 session model (verified live + HAR): the router never
        sends 401/403 — an expired/missing session answers HTTP 200
        with the SPA login page. That HTML body is a lost session
        until proven otherwise: one bounded re-login (throttled across
        endpoints) + one retry. Only HTML that persists after a fresh
        login is the real firmware degradation
        (``HitronEndpointDegradedError``), which re-login cannot clear.

        Any aiohttp client error (timeout, connection refused, DNS
        failure, etc.) is translated into ``HitronConnectionError`` so
        the config flow and ``__init__.py`` can handle it uniformly
        without importing aiohttp.

        The CODA-5610Q's web server occasionally returns an empty or
        malformed body when under load (e.g. during the 12-way
        parallel gather the coordinator runs on first refresh).
        We retry up to 2 times with a short backoff, re-authenticating
        on each attempt since the session cookie may have been lost.
        """
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": str(self._base / "webpages/index.html"),
        }
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                async with self._session.get(
                    url, cookies=self._cookies, headers=headers
                ) as resp:
                    if resp.status in (401, 403):
                        # Belt-and-braces: some firmware variants do
                        # signal expiry with 401/403 — re-login and
                        # retry once inside this attempt.
                        await self.login()
                        async with self._session.get(
                            url, cookies=self._cookies, headers=headers
                        ) as resp2:
                            text = await resp2.text()
                            if not text:
                                raise HitronConnectionError(
                                    f"Empty response from {url} after re-auth"
                                )
                            _raise_if_html(url, text)
                            return await resp2.json(content_type=None)
                    text = await resp.text()
                    if not text:
                        raise HitronConnectionError(
                            f"Empty response from {url} (status {resp.status})"
                        )
                    if text.lstrip()[:1] == "<":
                        # v0.3.2: HTML body = lost session until proven
                        # otherwise (the router answers expired/missing
                        # sessions with HTTP 200 + login page, never
                        # 401/403 — verified live + HAR). One bounded
                        # re-login, then one retry with the fresh
                        # cookie. Only HTML that survives a FRESH login
                        # is classified as the genuine firmware
                        # degradation.
                        await self._ensure_fresh_login()
                        async with self._session.get(
                            url, cookies=self._cookies, headers=headers
                        ) as resp2:
                            text2 = await resp2.text()
                            if not text2:
                                raise HitronConnectionError(
                                    f"Empty response from {url} after re-login"
                                )
                            if text2.lstrip()[:1] == "<":
                                raise HitronEndpointDegradedError(
                                    f"{url.path} returned an HTML page "
                                    "instead of JSON",
                                    endpoint=url.path,
                                )
                            return await resp2.json(content_type=None)
                    return await resp.json(content_type=None)
            except HitronEndpointDegradedError:
                raise
            except (aiohttp.ClientError, HitronConnectionError, ValueError) as err:
                last_err = err
                _LOGGER.debug(
                    "HitronCodaAPI: GET %s failed (attempt %d/3): %s",
                    url, attempt + 1, err,
                )
                # Re-login before retrying in case the session died.
                # v0.3.0: under the single-flight lock so overlapping
                # retries can't invalidate each other's PHPSESSID.
                try:
                    await self.login()
                except Exception:
                    pass
            # Small backoff between retries
            await asyncio.sleep(0.5 * (attempt + 1))
        # All retries exhausted
        raise HitronConnectionError(
            f"Bad response from {url}: {last_err}"
        ) from last_err

    async def login(self) -> None:
        """Single-flight re-auth (v0.3.0). Serializes under _login_lock."""
        async with self._login_lock:
            await self._login_impl()

    async def _ensure_fresh_login(self) -> None:
        """Throttled re-login for the HTML→recovery path (v0.3.2).

        All endpoints share one aiohttp session and one cookie jar
        (``self._cookies`` is passed to every GET), so ONE fresh login
        fixes every concurrent HTML response in the same gather wave.
        The throttle prevents 12 parallel endpoints from issuing 12
        logins — the CODA invalidates overlapping sessions (fresh
        PHPSESSID per login). Under the single-flight lock the first
        caller logs in; the rest see a fresh ``_last_login_at`` and
        skip straight to retrying with the new cookie.
        """
        async with self._login_lock:
            now = time.monotonic()
            if (now - self._last_login_at) < _RELOGIN_MIN_INTERVAL:
                # Someone (another endpoint in this wave) just logged in.
                return
            await self._login_impl()

    async def keepalive(self) -> None:
        """Refresh the session without hitting a data endpoint (v0.3.2).

        The browser SPA sends GET /1/Device/Users/Alive every ~15s to
        keep its session open (SessionTimeout = 10 idle minutes in
        mainApp.js). Callers that cannot heartbeat that fast should call
        this on an interval well under the router's session expiry; it
        is a no-op when a login happened more recently than the
        router's session expiry.
        """
        if (time.monotonic() - self._last_login_at) < _SESSION_IDLE_EXPIRY - 60:
            return
        await self.login()

    async def _login_impl(self) -> None:
        """Authenticate and store the session cookie.

        The router expects form-urlencoded with a JSON blob in the
        ``model`` field — NOT a JSON request body.

        The CODA-5610Q returns ``errCode: "000"`` for BOTH success and
        authentication failure. The discriminator is the ``result`` field
        (e.g. ``"success"`` vs ``"Error_Password_Wrong"``). Checking only
        ``errCode`` causes wrong passwords to be accepted as valid logins.

        aiohttp client errors (timeout, DNS failure, connection refused,
        etc.) are translated to ``HitronConnectionError`` so the config
        flow shows "Failed to connect" instead of "Unknown error".
        """
        url = self._base / "1/Device/Users/Login"
        form = aiohttp.FormData()
        form.add_field(
            "model",
            f'{{"username":"{self._username}","password":"{self._password}"}}',
        )
        try:
            async with self._session.post(url, data=form) as resp:
                if resp.status != 200:
                    raise HitronConnectionError(f"Login HTTP {resp.status}")
                payload = await resp.json(content_type=None)
                set_cookie_headers = resp.headers.getall("Set-Cookie", [])
        except aiohttp.ClientError as err:
            raise HitronConnectionError(str(err)) from err
        except (ValueError, KeyError, TypeError) as err:
            raise HitronConnectionError(
                f"Bad login response: {err}"
            ) from err

        # errCode is unreliable on this firmware — both "success" and
        # auth failures return "000". Use the "result" field instead.
        result = payload.get("result", "")
        if payload.get("errCode") != "000" or result.startswith("Error_"):
            raise HitronAuthError(
                result or payload.get("errMsg") or "login failed"
            )

        # Read the session cookie from the captured Set-Cookie headers
        # (must be captured inside the `async with` block since `resp`
        # is closed after the context manager exits). aiohttp's session
        # cookie_jar is not reliably populated for POST requests.
        for raw in set_cookie_headers:
            name, _, rest = raw.partition("=")
            if name.strip() == "PHPSESSID":
                value = rest.split(";", 1)[0].strip()
                self._cookies = {"PHPSESSID": value}
                # v0.3.2: stamp the login time for the re-login throttle
                # and the keepalive interval.
                self._last_login_at = time.monotonic()
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
        """Fetch DHCP reservations.

        These are useful because the CODA-5610Q's live host list often
        reports hostName=\"Unknown\" even when a DHCP reservation has
        a friendly name. The integration can use reservations as a
        fallback display name / stable identity.
        """
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