"""Test the config flow end-to-end.

Regression for v0.2.2 → v0.2.3: the config flow used
``info["SerialNum"]`` (dict access) but ``get_system_info()`` returns
a ``SystemInfo`` dataclass, so every successful login crashed with
``TypeError: 'SystemInfo' object is not subscriptable`` and HA showed
"Unknown error". This test exercises the real flow handler with
realistic router response payloads and asserts the config entry is
created with the right unique id (the serial number).
"""
from unittest.mock import MagicMock

from custom_components.hitron_coda_5610q.config_flow import (
    HitronCodaConfigFlow,
)
from custom_components.hitron_coda_5610q.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
)


HOST = "192.168.0.1"


def _make_session(login_payload, version_payload):
    """Build a mock aiohttp.ClientSession that returns the given payloads."""
    session = MagicMock()

    class _Resp:
        def __init__(self, payload, set_cookie=None):
            self.status = 200
            self._payload = payload
            self.headers = MagicMock()
            self.headers.getall = lambda name, default=[]: (
                [set_cookie] if set_cookie and name.lower() == "set-cookie" else default
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self, content_type=None):
            return self._payload

    def _post(url, data=None):
        if "Login" in str(url):
            sc = "PHPSESSID=abc123; path=/; HttpOnly" if login_payload.get("result") == "success" else None
            return _Resp(login_payload, set_cookie=sc)
        return _Resp({"errCode": "001"})

    def _get(url, cookies=None, headers=None):
        return _Resp(version_payload)

    session.post = _post
    session.get = _get
    return session


def _patch_session(hass, session):
    from custom_components.hitron_coda_5610q import config_flow as cf_mod
    cf_mod.async_get_clientsession = lambda hass: session


async def test_config_flow_succeeds(hass):
    """Happy path: valid creds produce a config entry with serial-number unique id."""
    login_payload = {"errCode": "000", "errMsg": "", "result": "success"}
    version_payload = {
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
    _patch_session(hass, _make_session(login_payload, version_payload))

    flow = HitronCodaConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}

    result = await flow.async_step_user(
        {CONF_HOST: HOST, CONF_USERNAME: "cusadmin", CONF_PASSWORD: "password"}
    )

    assert result["type"] == "create_entry", result
    assert result["title"] == f"Hitron CODA-5610Q ({HOST})"
    assert result["data"][CONF_HOST] == HOST
    assert result["data"][CONF_PASSWORD] == "password"
    assert flow.unique_id == "AN6025101226"


async def test_config_flow_wrong_password_shows_invalid_auth(hass):
    """Wrong password returns the form with invalid_auth error, no crash."""
    login_payload = {"errCode": "000", "errMsg": "", "result": "Error_Password_Wrong"}
    version_payload = {"errCode": "001", "errMsg": "no auth"}
    _patch_session(hass, _make_session(login_payload, version_payload))

    flow = HitronCodaConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}

    result = await flow.async_step_user(
        {CONF_HOST: HOST, CONF_USERNAME: "cusadmin", CONF_PASSWORD: "wrong"}
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
    # Unique id should not have been set on failure
    assert getattr(flow, "unique_id", None) is None


async def test_config_flow_unreachable_host_shows_cannot_connect(hass):
    """Unreachable host: aiohttp ConnectionTimeoutError must surface as
    HitronConnectionError, then the config flow renders cannot_connect."""
    session = MagicMock()

    class _TimeoutCM:
        async def __aenter__(self):
            import aiohttp
            raise aiohttp.ConnectionTimeoutError("timeout")
        async def __aexit__(self, *args):
            return False

    session.post = lambda url, data=None: _TimeoutCM()
    _patch_session(hass, session)

    flow = HitronCodaConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}

    result = await flow.async_step_user(
        {CONF_HOST: "192.168.99.99", CONF_USERNAME: "cusadmin", CONF_PASSWORD: "x"}
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}
