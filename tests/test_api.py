"""Test the API client."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hitron_coda_5610q.api import (
    ConnectedDevice,
    HitronAuthError,
    HitronCodaAPI,
    SystemInfo,
)

HOST = "192.168.0.1"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text())


class MockResponse:
    """Mock aiohttp response that supports async context manager."""

    def __init__(self, data: dict, status: int = 200):
        self.status = status
        self._data = data

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_session(login_ok=True, hosts_data=None, version_data=None):
    """Create a mock aiohttp ClientSession."""
    session = MagicMock()

    # Cookie jar
    session.cookie_jar = []
    if login_ok:
        cookie = MagicMock()
        cookie.key = "PHPSESSID"
        cookie.value = "abc123"
        session.cookie_jar.append(cookie)

    def _post(url, data=None):
        if "Login" in str(url):
            if login_ok:
                return MockResponse({"errCode": "000", "result": "success"})
            return MockResponse({"errCode": "001", "errMsg": "Invalid username or password."})
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
    """Test that login stores the PHPSESSID cookie."""
    session = _make_session(login_ok=True)
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    await api.login()
    assert "PHPSESSID" in api._cookies
    assert api._cookies["PHPSESSID"] == "abc123"


async def test_login_fails_invalid_credentials():
    """Test that invalid credentials raise HitronAuthError."""
    session = _make_session(login_ok=False)
    api = HitronCodaAPI(session, HOST, "cusadmin", "wrong")
    with pytest.raises(HitronAuthError):
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
    session = _make_session(login_ok=True, version_data=version_data)
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
    session = _make_session(login_ok=True, hosts_data=fixture)
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
        login_ok=True,
        hosts_data={"errCode": "000", "HostNumberOfEntries": "0", "Hosts_List": []},
    )
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    api._cookies = {"PHPSESSID": "abc123"}
    devices = await api.get_connected_devices()
    assert devices == []