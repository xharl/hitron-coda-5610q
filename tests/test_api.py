"""Test the API client."""
import pytest
from aioresponses import aioresponses
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.hitron_coda_5610q.api import HitronCodaAPI


async def test_login_succeeds(hass):
    """Test that login stores the PHPSESSID cookie."""
    with aioresponses() as mocked:
        mocked.post(
            "http://192.168.0.1/1/Device/Users/Login",
            payload={"errCode": "000", "result": "success"},
            cookies={"PHPSESSID": "abc123"},
        )
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, "192.168.0.1", "cusadmin", "password")
        await api.login()
        assert api._cookies == {"PHPSESSID": "abc123"}


async def test_login_fails_invalid_credentials(hass):
    """Test that invalid credentials raise HitronAuthError."""
    from custom_components.hitron_coda_5610q.api import HitronAuthError

    with aioresponses() as mocked:
        mocked.post(
            "http://192.168.0.1/1/Device/Users/Login",
            payload={"errCode": "001", "errMsg": "Invalid username or password."},
        )
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, "192.168.0.1", "cusadmin", "wrong")
        with pytest.raises(HitronAuthError):
            await api.login()


async def test_get_system_info(hass):
    """Test fetching system info."""
    with aioresponses() as mocked:
        mocked.post(
            "http://192.168.0.1/1/Device/Users/Login",
            payload={"errCode": "000", "result": "success"},
            cookies={"PHPSESSID": "abc123"},
        )
        mocked.get(
            "http://192.168.0.1/1/Device/CM/Version",
            payload={
                "errCode": "000",
                "deviceId": "38:AD:2B:93:19:20",
                "modelName": "CODA5610Q",
                "ApiVersion": "1.12.1",
                "SoftwareVersion": "7.3.5.1.2b22",
                "SerialNum": "AN6025101226",
                "HwVersion": "1A",
                "vendorName": "Hitron Technologies",
            },
        )
        session = async_get_clientsession(hass)
        api = HitronCodaAPI(session, "192.168.0.1", "cusadmin", "password")
        await api.login()
        info = await api.get_system_info()
        assert info["modelName"] == "CODA5610Q"
        assert info["SerialNum"] == "AN6025101226"