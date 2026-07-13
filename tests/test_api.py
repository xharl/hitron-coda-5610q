"""Test the API client."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.hitron_coda_5610q.api import (
    ConnectedDevice,
    HitronAuthError,
    HitronCodaAPI,
    HitronConnectionError,
    SystemInfo,
)

HOST = "192.168.0.1"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text())


class MockResponse:
    """Mock aiohttp response that supports async context manager."""

    def __init__(self, data: dict, status: int = 200, set_cookie: str | None = None):
        self.status = status
        self._data = data
        # Minimal CIMultiDict-like headers. Only getall() is exercised by the
        # API client, so that's all we implement.
        self.headers = MagicMock()
        self.headers.getall = lambda name, default=[]: (
            [set_cookie] if name.lower() == "set-cookie" and set_cookie else default
        )

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_session(
    login_payload: dict | None = None,
    hosts_data=None,
    version_data=None,
    set_cookie: str | None = "auto",
):
    """Create a mock aiohttp ClientSession.

    ``login_payload`` is the JSON body returned by the Login endpoint.
    Defaults to a real success response. Pass the real wrong-password
    response shape ``{"errCode": "000", "errMsg": "", "result": "Error_Password_Wrong"}``
    to exercise the auth-error path.

    ``set_cookie`` controls the response's Set-Cookie header. ``"auto"``
    (default) emits a PHPSESSID when ``login_payload`` indicates success
    and ``None`` otherwise. Pass an explicit string to override (e.g.
    ``None`` to test the "no cookie returned" case even on success).
    """
    session = MagicMock()

    if login_payload is None:
        login_payload = {"errCode": "000", "errMsg": "", "result": "success"}

    if set_cookie == "auto":
        is_success = login_payload.get("result") == "success"
        set_cookie = "PHPSESSID=abc123; path=/; HttpOnly" if is_success else None

    def _post(url, data=None):
        if "Login" in str(url):
            return MockResponse(login_payload, set_cookie=set_cookie)
        return MockResponse({"errCode": "001", "errMsg": "not found"})

    def _get(url, cookies=None, headers=None):
        url_str = str(url)
        if "Hosts/1" in url_str:
            return MockResponse(hosts_data or {"errCode": "001", "errMsg": "not found"})
        if "CM/Version" in url_str:
            return MockResponse(version_data or {"errCode": "001", "errMsg": "not found"})
        return MockResponse({"errCode": "001", "errMsg": "not found"})

    session.post = _post
    session.get = _get
    return session


async def test_login_succeeds():
    """Test that login stores the PHPSESSID cookie from the response header."""
    session = _make_session()
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    await api.login()
    assert "PHPSESSID" in api._cookies
    assert api._cookies["PHPSESSID"] == "abc123"


async def test_login_fails_wrong_password_real_response():
    """Regression: the CODA-5610Q returns errCode='000' for wrong passwords.

    The real router response is
    ``{"errCode": "000", "errMsg": "", "result": "Error_Password_Wrong"}``
    — errCode alone is not enough; ``result`` must also be checked.
    """
    session = _make_session(
        login_payload={
            "errCode": "000",
            "errMsg": "",
            "result": "Error_Password_Wrong",
        }
    )
    api = HitronCodaAPI(session, HOST, "cusadmin", "wrong")
    with pytest.raises(HitronAuthError, match="Error_Password_Wrong"):
        await api.login()
    # No cookie should be stored on a failed login.
    assert api._cookies == {}


async def test_login_fails_on_legacy_errcode_001():
    """Older firmware returned errCode != '000' for auth failure; still rejected."""
    session = _make_session(
        login_payload={"errCode": "001", "errMsg": "Invalid username or password."}
    )
    api = HitronCodaAPI(session, HOST, "cusadmin", "wrong")
    with pytest.raises(HitronAuthError):
        await api.login()


async def test_login_fails_when_set_cookie_missing():
    """If the response has no Set-Cookie header, login is reported as auth failure."""
    session = _make_session(
        login_payload={"errCode": "000", "result": "success"},
        set_cookie=None,  # explicit: no Set-Cookie
    )
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    with pytest.raises(HitronAuthError, match="No PHPSESSID"):
        await api.login()


async def test_login_translates_aiohttp_timeout_to_connection_error():
    """Regression: aiohttp.ConnectionTimeoutError must surface as
    HitronConnectionError, not an unhandled exception. Without this
    translation the config flow shows "Unknown error" instead of
    "Failed to connect" when the host is unreachable.
    """
    session = MagicMock()

    class _TimeoutCM:
        async def __aenter__(self):
            raise aiohttp.ConnectionTimeoutError("timeout")
        async def __aexit__(self, *args):
            return False

    session.post = lambda url, data=None: _TimeoutCM()
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    with pytest.raises(HitronConnectionError, match="timeout"):
        await api.login()


async def test_login_translates_aiohttp_connector_error_to_connection_error():
    """ClientConnectorError (DNS failure / connection refused) is also translated."""
    session = MagicMock()

    class _ConnCM:
        async def __aenter__(self):
            raise aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError("Connection refused")
            )
        async def __aexit__(self, *args):
            return False

    session.post = lambda url, data=None: _ConnCM()
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    with pytest.raises(HitronConnectionError):
        await api.login()


async def test_login_translates_malformed_json_to_connection_error():
    """If the router returns a non-JSON response, treat it as a connection error
    rather than letting the JSONDecodeError escape."""
    session = MagicMock()

    class _BadJsonResponse:
        status = 200
        def __init__(self):
            self.headers = MagicMock()
            self.headers.getall = lambda name, default=[]: default
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def json(self, content_type=None):
            raise ValueError("not json")

    session.post = lambda url, data=None: _BadJsonResponse()
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    with pytest.raises(HitronConnectionError, match="Bad login response"):
        await api.login()


async def test_get_system_info():
    """Test fetching system info."""
    version_data = {
        "errCode": "000",
        "deviceId": "38:AD:2B:93:19:20",
        "modelName": "CODA5610Q",
        "ApiVersion": "1.12.1",
        "SoftwareVersion": "7.3.5.1.2b22",
        "SerialNum": "AN6025101226",
        "HwVersion": "1A",
        "vendorName": "Hitron Technologies",
        "DeploymentName": "VIDEOTRON",
        "wifiChip": "qca",
    }
    session = _make_session(version_data=version_data)
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    api._cookies = {"PHPSESSID": "abc123"}
    info = await api.get_system_info()
    assert isinstance(info, SystemInfo)
    assert info.model_name == "CODA5610Q"
    assert info.serial_number == "AN6025101226"
    assert info.software_version == "7.3.5.1.2b22"
    assert info.deployment_name == "VIDEOTRON"


async def test_get_connected_devices():
    """Test fetching the connected device list with real fixture data."""
    fixture = load_fixture("connect_info.json")
    session = _make_session(hosts_data=fixture)
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    api._cookies = {"PHPSESSID": "abc123"}
    devices = await api.get_connected_devices()

    assert len(devices) == int(fixture["HostNumberOfEntries"])
    assert all(isinstance(d, ConnectedDevice) for d in devices)

    # Check first device from fixture
    first = devices[0]
    assert first.hostname == "HS103"
    assert first.mac_address == "60:32:B1:4A:68:E3"  # upper-cased
    assert first.ip_address == "192.168.0.16"
    assert first.interface == "WiFi 2.4G"
    assert first.address_source == "DHCP-IP"
    assert first.status is True
    assert first.action == "Resume"


async def test_get_connected_devices_empty():
    """Test fetching devices when router returns empty list."""
    session = _make_session(
        hosts_data={"errCode": "000", "HostNumberOfEntries": "0", "Hosts_List": []},
    )
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    api._cookies = {"PHPSESSID": "abc123"}
    devices = await api.get_connected_devices()
    assert devices == []