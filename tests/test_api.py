"""Test the API client."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.hitron_coda_5610q.api import (
    ConnectedDevice,
    HitronAuthError,
    HitronConnectionError,
    HitronCodaAPI,
    SystemInfo,
)

HOST = "192.168.0.1"
LOGIN_URL = f"http://{HOST}/1/Device/Users/Login"
HOSTS_URL = f"http://{HOST}/1/Device/Hosts/1"
VERSION_URL = f"http://{HOST}/1/Device/CM/Version"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / name
    return json.loads(fixture_path.read_text())


async def test_login_succeeds(hass):
    """Test that login stores the PHPSESSID cookie."""
    with aioresponses() as mocked:
        mocked.post(
            LOGIN_URL,
            payload={"errCode": "000", "result": "success"},
            headers={"Set-Cookie": "PHPSESSID=abc123; HttpOnly; Path=/"},
        )
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, HOST, "cusadmin", "password")
        await api.login()
        assert "PHPSESSID" in api._cookies


async def test_login_fails_invalid_credentials(hass):
    """Test that invalid credentials raise HitronAuthError."""
    with aioresponses() as mocked:
        mocked.post(
            LOGIN_URL,
            payload={"errCode": "001", "errMsg": "Invalid username or password."},
        )
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, HOST, "cusadmin", "wrong")
        with pytest.raises(HitronAuthError):
            await api.login()


async def test_get_system_info(hass):
    """Test fetching system info."""
    with aioresponses() as mocked:
        mocked.post(
            LOGIN_URL,
            payload={"errCode": "000", "result": "success"},
            headers={"Set-Cookie": "PHPSESSID=abc123; HttpOnly; Path=/"},
        )
        mocked.get(
            VERSION_URL,
            payload={
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
            },
        )
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, HOST, "cusadmin", "password")
        await api.login()
        info = await api.get_system_info()
        assert isinstance(info, SystemInfo)
        assert info.model_name == "CODA5610Q"
        assert info.serial_number == "AN6025101226"
        assert info.software_version == "7.3.5.1.2b22"
        assert info.deployment_name == "VIDEOTRON"


async def test_get_connected_devices(hass):
    """Test fetching the connected device list with real fixture data."""
    fixture = load_fixture("connect_info.json")
    with aioresponses() as mocked:
        mocked.post(
            LOGIN_URL,
            payload={"errCode": "000", "result": "success"},
            headers={"Set-Cookie": "PHPSESSID=abc123; HttpOnly; Path=/"},
        )
        mocked.get(HOSTS_URL, payload=fixture)
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, HOST, "cusadmin", "password")
        await api.login()
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


async def test_get_connected_devices_empty(hass):
    """Test fetching devices when router returns empty list."""
    with aioresponses() as mocked:
        mocked.post(
            LOGIN_URL,
            payload={"errCode": "000", "result": "success"},
            headers={"Set-Cookie": "PHPSESSID=abc123; HttpOnly; Path=/"},
        )
        mocked.get(
            HOSTS_URL,
            payload={"errCode": "000", "HostNumberOfEntries": "0", "Hosts_List": []},
        )
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, HOST, "cusadmin", "password")
        await api.login()
        devices = await api.get_connected_devices()
        assert devices == []