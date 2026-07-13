"""Test the API client."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hitron_coda_5610q.api import (
    ConnectedDevice,
    HitronAuthError,
    HitronConnectionError,
    HitronCodaAPI,
    SystemInfo,
)

HOST = "192.168.0.1"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text())


def _mock_response(data: dict, status: int = 200, cookies: dict | None = None):
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=data)
    return resp


def _mock_session(cookie_jar_cookies: dict | None = None):
    """Create a mock aiohttp ClientSession."""
    session = MagicMock()
    session.cookie_jar = []

    if cookie_jar_cookies:
        for key, val in cookie_jar_cookies.items():
            cookie = MagicMock()
            cookie.key = key
            cookie.value = val
            session.cookie_jar.append(cookie)

    async def _post(url, data=None):
        if "Login" in str(url):
            return _mock_response(
                {"errCode": "000", "result": "success"},
                cookies={"PHPSESSID": "abc123"},
            )
        return _mock_response({"errCode": "001", "errMsg": "not found"})

    async def _get(url, cookies=None, headers=None):
        if "Hosts/1" in str(url):
            return _mock_response(load_fixture("connect_info.json"))
        if "CM/Version" in str(url):
            return _mock_response(
                {
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
            )
        return _mock_response({"errCode": "001", "errMsg": "not found"})

    # Make post/get context managers
    class _Ctx:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *args):
            pass

    async def _post_cm(url, data=None):
        return _Ctx(await _post(url, data))

    async def _get_cm(url, cookies=None, headers=None):
        return _Ctx(await _get(url, cookies, headers))

    session.post = _post_cm
    session.get = _get_cm

    return session


async def test_login_succeeds():
    """Test that login stores the PHPSESSID cookie."""
    session = _mock_session()
    # After login, the cookie jar should have PHPSESSID
    cookie = MagicMock()
    cookie.key = "PHPSESSID"
    cookie.value = "abc123"
    session.cookie_jar.append(cookie)

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    await api.login()
    assert "PHPSESSID" in api._cookies
    assert api._cookies["PHPSESSID"] == "abc123"


async def test_login_fails_invalid_credentials():
    """Test that invalid credentials raise HitronAuthError."""
    session = MagicMock()
    resp = _mock_response({"errCode": "001", "errMsg": "Invalid username or password."})

    class _Ctx:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *args):
            pass

    session.post = AsyncMock(return_value=_Ctx())
    session.cookie_jar = []

    api = HitronCodaAPI(session, HOST, "cusadmin", "wrong")
    with pytest.raises(HitronAuthError):
        await api.login()


async def test_get_system_info():
    """Test fetching system info."""
    session = _mock_session()
    cookie = MagicMock()
    cookie.key = "PHPSESSID"
    cookie.value = "abc123"
    session.cookie_jar.append(cookie)

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
    session = _mock_session()
    cookie = MagicMock()
    cookie.key = "PHPSESSID"
    cookie.value = "abc123"
    session.cookie_jar.append(cookie)

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
    session = MagicMock()
    resp = _mock_response(
        {"errCode": "000", "HostNumberOfEntries": "0", "Hosts_List": []}
    )

    class _Ctx:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *args):
            pass

    async def _get(url, cookies=None, headers=None):
        return _Ctx()

    session.get = _get
    session.cookie_jar = []

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    api._cookies = {"PHPSESSID": "abc123"}
    devices = await api.get_connected_devices()
    assert devices == []