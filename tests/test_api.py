"""Test the API client."""
import asyncio
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
    HitronEndpointDegradedError,
    SystemInfo,
)

HOST = "192.168.0.1"

LOGIN_PAGE_HTML = (
    "<!DOCTYPE html><html><head><title>Hitron CODA-5610Q</title></head>"
    '<body class="login-page"></body></html>'
)


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

    async def text(self):
        # Real aiohttp returns the body as a string. Our mock serializes
        # the dict so json() can round-trip it, and returns "" if the
        # data is missing/None (simulating empty body).
        if self._data is None:
            return ""
        import json as _json
        return _json.dumps(self._data)

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
        async def text(self):
            return ""

    session.post = lambda url, data=None: _BadJsonResponse()
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    with pytest.raises(HitronConnectionError, match="Bad login response"):
        await api.login()


class _HtmlPageResponse:
    """Mock aiohttp response whose body is an HTML page (not JSON).

    Mirrors the live v0.3.1 defect: HTTP 200 + the SPA login page on
    endpoints that normally serve JSON.
    """

    status = 200

    def __init__(self, body: str = LOGIN_PAGE_HTML):
        self.headers = MagicMock()
        self.headers.getall = lambda name, default=[]: default
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self._body

    async def json(self, content_type=None):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


async def test_html_triggers_bounded_relogin_then_degraded():
    """v0.3.2 regression (the 2026-09-07 wedge): HTML on a request with
    an expired/missing session must trigger ONE re-login and one retry.
    Only HTML that survives the fresh login is classified as the
    HitronEndpointDegradedError firmware degradation.

    Live evidence: the router answers a cookie-less request with
    HTTP 200 + the SPA login page (never 401/403). v0.3.1 classified
    that as degradation and never re-logged in, wedging setup on every
    restart.
    """
    session = MagicMock()
    calls = {"get": 0, "post": 0}

    def _get(url, cookies=None, headers=None):
        calls["get"] += 1
        return _HtmlPageResponse()

    def _post(url, data=None):
        calls["post"] += 1
        return MockResponse(
            {"errCode": "000", "result": "success"},
            set_cookie="PHPSESSID=abc123; path=/; HttpOnly",
        )

    session.get = _get
    session.post = _post

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    from yarl import URL

    url = URL(f"http://{HOST}/1/Device/CM/DsInfo")
    with pytest.raises(HitronEndpointDegradedError) as excinfo:
        await api._request_json(url)

    assert excinfo.value.endpoint == "/1/Device/CM/DsInfo"
    # Bounded: exactly one GET, one re-login, one retry GET. No retry
    # loop beyond that, and not classified as an auth error.
    assert calls["get"] == 2
    assert calls["post"] == 1
    assert not isinstance(excinfo.value, HitronAuthError)


async def test_html_recovers_after_relogin_with_fresh_session():
    """v0.3.2 core recovery: HTML (lost session) → re-login → retry with
    the fresh PHPSESSID → JSON. This is the exact live failure sequence:
    boot with no session, every endpoint serving the login page."""
    session = MagicMock()
    calls = {"get": 0, "post": 0}

    def _get(url, cookies=None, headers=None):
        calls["get"] += 1
        if calls["get"] == 1:
            return _HtmlPageResponse()
        return MockResponse({"errCode": "000", "data": "ok"})

    def _post(url, data=None):
        calls["post"] += 1
        return MockResponse(
            {"errCode": "000", "result": "success"},
            set_cookie="PHPSESSID=fresh; path=/; HttpOnly",
        )

    session.get = _get
    session.post = _post

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    from yarl import URL

    result = await api._request_json(URL(f"http://{HOST}/1/Device/Hosts/1"))
    assert result == {"errCode": "000", "data": "ok"}
    assert calls["get"] == 2
    assert calls["post"] == 1
    # The fresh cookie is stored for subsequent requests.
    assert api._cookies == {"PHPSESSID": "fresh"}


async def test_concurrent_html_responses_trigger_single_login():
    """All endpoints share one cookie jar, so ONE fresh login fixes every
    concurrent HTML response in the same gather wave: the throttle must
    collapse 12 parallel re-login attempts into a single POST."""
    session = MagicMock()
    calls = {"get": 0, "post": 0}
    logged_in = {"flag": False}

    def _get(url, cookies=None, headers=None):
        calls["get"] += 1
        if not logged_in["flag"]:
            return _HtmlPageResponse()
        return MockResponse({"errCode": "000", "data": "ok"})

    def _post(url, data=None):
        calls["post"] += 1
        logged_in["flag"] = True
        return MockResponse(
            {"errCode": "000", "result": "success"},
            set_cookie="PHPSESSID=fresh; path=/; HttpOnly",
        )

    session.get = _get
    session.post = _post

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    from yarl import URL

    results = await asyncio.gather(
        *(
            api._request_json(URL(f"http://{HOST}/1/Device/EP{i}"))
            for i in range(6)
        )
    )
    assert all(r == {"errCode": "000", "data": "ok"} for r in results)
    # One login total — not one per endpoint.
    assert calls["post"] == 1


async def test_keepalive_skips_when_session_fresh():
    """keepalive() is a no-op while the session is younger than the
    router's idle expiry (10 min minus margin)."""
    session = MagicMock()
    posts = {"n": 0}

    def _post(url, data=None):
        posts["n"] += 1
        return MockResponse(
            {"errCode": "000", "result": "success"},
            set_cookie="PHPSESSID=abc123; path=/; HttpOnly",
        )

    session.post = _post
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    await api.login()
    assert posts["n"] == 1
    await api.keepalive()
    assert posts["n"] == 1  # skipped: fresh session


async def test_keepalive_relogins_after_expiry_window():
    """keepalive() re-logs in when the last login is older than the
    router's session idle expiry (10 min) minus the margin."""
    session = MagicMock()
    posts = {"n": 0}

    def _post(url, data=None):
        posts["n"] += 1
        return MockResponse(
            {"errCode": "000", "result": "success"},
            set_cookie="PHPSESSID=abc123; path=/; HttpOnly",
        )

    session.post = _post
    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    await api.login()
    # Simulate a session that aged past the expiry margin.
    api._last_login_at -= (10 * 60)  # 10 minutes ago
    await api.keepalive()
    assert posts["n"] == 2


async def test_get_downstream_channels_surfaces_degradation():
    """HTML that survives a fresh login (the genuine v0.3.1 firmware
    degradation) propagates with the endpoint path attached."""
    session = MagicMock()
    session.get = lambda url, cookies=None, headers=None: _HtmlPageResponse()

    def _post(url, data=None):
        return MockResponse(
            {"errCode": "000", "result": "success"},
            set_cookie="PHPSESSID=abc123; path=/; HttpOnly",
        )

    session.post = _post

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    with pytest.raises(HitronEndpointDegradedError, match="DsInfo"):
        await api.get_downstream_channels()
    with pytest.raises(HitronEndpointDegradedError, match="UsInfo"):
        await api.get_upstream_channels()


async def test_html_after_reauth_still_raises_degraded():
    """A 401 → re-login → HTML sequence must still end in
    HitronEndpointDegradedError (not a retry loop): the post-re-auth
    response is guarded too.
    """
    session = MagicMock()
    calls = {"get": 0, "post": 0}

    class _Unauthorized:
        status = 401

        def __init__(self):
            self.headers = MagicMock()
            self.headers.getall = lambda name, default=[]: default

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def text(self):
            return ""

        async def json(self, content_type=None):
            return {}

    def _get(url, cookies=None, headers=None):
        calls["get"] += 1
        if calls["get"] == 1:
            return _Unauthorized()
        return _HtmlPageResponse()

    def _post(url, data=None):
        calls["post"] += 1
        return MockResponse(
            {"errCode": "000", "result": "success"},
            set_cookie="PHPSESSID=abc123; path=/; HttpOnly",
        )

    session.get = _get
    session.post = _post

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    from yarl import URL

    with pytest.raises(HitronEndpointDegradedError, match="UsInfo"):
        await api._request_json(URL(f"http://{HOST}/1/Device/CM/UsInfo"))

    # One GET, one re-login, one GET after re-auth — then fail fast.
    assert calls["get"] == 2
    assert calls["post"] == 1


async def test_get_recovers_from_empty_response():
    """Regression: CODA-5610Q occasionally returns empty bodies under load.
    The GET should retry up to 3 times, re-authenticating between attempts,
    and recover if a subsequent attempt gets a real response.
    """
    session = MagicMock()
    session.cookie_jar = []

    call_count = {"post": 0, "get": 0}

    class _GoodLogin:
        status = 200
        def __init__(self):
            self.headers = MagicMock()
            self.headers.getall = lambda name, default=[]: (
                ["PHPSESSID=abc123; path=/; HttpOnly"]
                if name.lower() == "set-cookie" else []
            )
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def json(self, content_type=None):
            return {"errCode": "000", "result": "success"}
        async def text(self):
            return '{"errCode":"000","result":"success"}'

    class _EmptyThenGood:
        """First call returns empty body, second returns good data."""
        def __init__(self):
            self.headers = MagicMock()
            self.headers.getall = lambda name, default=[]: []
            self.status = 200
        async def __aenter__(self):
            call_count["get"] += 1
            return self
        async def __aexit__(self, *args):
            return False
        async def json(self, content_type=None):
            if call_count["get"] == 1:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return {"errCode": "000", "data": "ok"}
        async def text(self):
            if call_count["get"] == 1:
                return ""
            return '{"errCode":"000","data":"ok"}'

    def _post(url, data=None):
        call_count["post"] += 1
        return _GoodLogin()

    def _get(url, cookies=None, headers=None):
        return _EmptyThenGood()

    session.post = _post
    session.get = _get

    api = HitronCodaAPI(session, HOST, "cusadmin", "password")
    # Use the retry path via _request_json
    from yarl import URL
    result = await api._request_json(URL("http://192.168.0.1/1/Device/Test"))
    # Should have logged in once initially, then once per retry
    assert result == {"errCode": "000", "data": "ok"}
    # Verify we recovered: at least 2 GETs were made
    assert call_count["get"] >= 2


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