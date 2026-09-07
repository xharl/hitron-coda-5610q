"""Test the coordinator's v0.3.1 graceful DOCSIS degradation.

Background: the CODA-5610Q firmware intermittently stops serving DOCSIS
channel data — DsInfo/UsInfo answer HTTP 200 with the SPA login page
(HTML) instead of JSON while Login and the host list keep working, and
re-login does NOT clear the condition. The coordinator must absorb
HitronEndpointDegradedError on slow-tier endpoints (serve last-good
values for those fields, track the degradation) while keeping the
v0.3.0 failure semantics for every other error class.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hitron_coda_5610q.api import (
    DownstreamChannel,
    HitronConnectionError,
    HitronEndpointDegradedError,
    SystemInfo,
    UpstreamChannel,
)
from custom_components.hitron_coda_5610q.const import DOMAIN
from custom_components.hitron_coda_5610q.coordinator import HitronCodaCoordinator

DS_INFO = "/1/Device/CM/DsInfo"
US_INFO = "/1/Device/CM/UsInfo"

# Default fast/slow interval ratio (30s/300s): every 10th cycle polls
# the slow tier.
_SLOW_EVERY = 10


def _degraded(endpoint: str) -> HitronEndpointDegradedError:
    """Build the error the API client raises for a degraded endpoint."""
    return HitronEndpointDegradedError(
        f"{endpoint} returned an HTML page instead of JSON",
        endpoint=endpoint,
    )


def _system_info() -> SystemInfo:
    return SystemInfo(
        serial_number="AN6025101226",
        model_name="CODA5610Q",
        hardware_version="1A",
        software_version="7.3.5.1.2b22",
        api_version="1.12.1",
        vendor_name="Hitron Technologies",
        device_id="38:AD:2B:93:19:20",
        deployment_name="VIDEOTRON",
        wifi_chip="qca",
    )


def _ds_channels(channel_id: str = "1") -> list[DownstreamChannel]:
    return [
        DownstreamChannel(
            port_id="",
            frequency="591000000",
            modulation="QAM256",
            signal_strength="5.0",
            snr="38.6",
            channel_id=channel_id,
            correcteds="0",
            uncorrectables="0",
        )
    ]


def _us_channels(channel_id: str = "5") -> list[UpstreamChannel]:
    return [
        UpstreamChannel(
            port_id="",
            frequency="30000000",
            modulation_type="ATDMA",
            signal_strength="44.0",
            bandwidth="6400000",
            channel_id=channel_id,
        )
    ]


def _make_api() -> MagicMock:
    """Build an API mock where every endpoint succeeds."""
    api = MagicMock()
    api.get_connected_devices = AsyncMock(return_value=[])
    api.get_wifi_clients = AsyncMock(return_value=[])
    api.get_system_info = AsyncMock(return_value=_system_info())
    api.get_router_sys_info = AsyncMock(return_value={"systemWanUptime": 100})
    api.get_downstream_channels = AsyncMock(return_value=_ds_channels())
    api.get_upstream_channels = AsyncMock(return_value=_us_channels())
    api.get_dhcp_reservations = AsyncMock(return_value=[])
    api.get_docsis_provisioning = AsyncMock(return_value={"networkAccess": "Permitted"})
    api.get_cm_sys_info = AsyncMock(return_value={"DsDataRate": 1180000000})
    api.get_wifi_radios = AsyncMock(return_value=[])
    api.get_firewall_status = AsyncMock(return_value={"securityLevel": "Low"})
    api.get_ethernet_ports = AsyncMock(return_value=[])
    return api


@pytest.fixture
async def make_coordinator(hass):
    """Build a real coordinator over a mocked API; clean up timers after."""
    created: list[HitronCodaCoordinator] = []

    async def _make(api: MagicMock) -> HitronCodaCoordinator:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "192.168.0.1", "username": "cusadmin", "password": "pw"},
        )
        entry.add_to_hass(hass)
        coordinator = HitronCodaCoordinator(hass, entry, api, timedelta(seconds=30))
        created.append(coordinator)
        return coordinator

    yield _make
    for coordinator in created:
        await coordinator.async_shutdown()


async def _prime_last_good(make_coordinator) -> tuple[HitronCodaCoordinator, Any]:
    """Run a first full update and arm the next slow cycle."""
    coordinator = await make_coordinator(_make_api())
    data = await coordinator._async_update_data()
    assert coordinator.degraded_endpoints == []
    # Jump to the cycle right before the next slow cycle.
    coordinator._cycle = _SLOW_EVERY - 1
    return coordinator, data


async def test_degraded_slow_tier_serves_last_good(make_coordinator):
    """DsInfo/UsInfo serving HTML must not fail the update: the channel
    values stay at their last-good state and the degradation is tracked.
    """
    coordinator, first_data = await _prime_last_good(make_coordinator)
    api = coordinator.api
    api.get_downstream_channels.side_effect = _degraded(DS_INFO)
    api.get_upstream_channels.side_effect = _degraded(US_INFO)

    data = await coordinator._async_update_data()

    # Update succeeded and the channel fields hold the previous values.
    assert data.downstream_channels == first_data.downstream_channels
    assert data.upstream_channels == first_data.upstream_channels
    # Unaffected slow-tier endpoint refreshed (equal values, fresh fetch).
    assert data.system_info == first_data.system_info
    # Degradation is tracked with the endpoint paths.
    assert coordinator.docsis_degraded is True
    assert coordinator.docsis_degraded_endpoints == [DS_INFO, US_INFO]
    assert coordinator.degraded_since is not None
    assert coordinator.is_degraded("downstream_channels")
    assert coordinator.is_degraded("upstream_channels")
    assert not coordinator.is_degraded("router_sys_info")
    # last_good timestamps survive from the successful fetch.
    assert coordinator.field_last_good("downstream_channels") is not None
    assert coordinator.field_last_good("router_sys_info") is not None


async def test_degradation_persists_until_healthy_slow_cycle(make_coordinator):
    """The flag must survive fast-only cycles (no DOCSIS probe happens)
    and clear on the next fully healthy slow cycle.
    """
    coordinator, _ = await _prime_last_good(make_coordinator)
    api = coordinator.api
    api.get_downstream_channels.side_effect = _degraded(DS_INFO)
    api.get_upstream_channels.side_effect = _degraded(US_INFO)
    await coordinator._async_update_data()
    since = coordinator.degraded_since
    assert since is not None

    # Fast cycle in between: still degraded, same window start.
    await coordinator._async_update_data()
    assert coordinator.docsis_degraded is True
    assert coordinator.degraded_since == since

    # Recover: next slow cycle reports fresh data and clears the flag.
    api.get_downstream_channels.side_effect = None
    api.get_upstream_channels.side_effect = None
    api.get_downstream_channels.return_value = _ds_channels(channel_id="9")
    coordinator._cycle = 2 * _SLOW_EVERY - 1  # arm the next slow cycle
    data = await coordinator._async_update_data()

    assert data.downstream_channels == _ds_channels(channel_id="9")
    assert coordinator.docsis_degraded is False
    assert coordinator.docsis_degraded_endpoints == []
    assert coordinator.degraded_since is None
    assert not coordinator.is_degraded("downstream_channels")


async def test_degradation_on_first_refresh_succeeds_with_empty_channels(make_coordinator):
    """No previous values exist during a degraded first refresh — the
    update still succeeds with empty channel lists so the integration
    (and docsis_data_ok) come up instead of failing setup.
    """
    api = _make_api()
    api.get_downstream_channels.side_effect = _degraded(DS_INFO)
    api.get_upstream_channels.side_effect = _degraded(US_INFO)
    coordinator = await make_coordinator(api)

    data = await coordinator._async_update_data()

    assert data.downstream_channels == []
    assert data.upstream_channels == []
    assert data.system_info == _system_info()  # unaffected endpoints fetch fine
    assert coordinator.docsis_degraded is True
    assert coordinator.field_last_good("downstream_channels") is None


async def test_mixed_failure_without_last_good_fails_update(make_coordinator):
    """One endpoint degraded + another connection-error on the first
    fetch: the non-degraded failure keeps the existing semantics —
    no last-good data exists, so the update fails.
    """
    api = _make_api()
    api.get_downstream_channels.side_effect = _degraded(DS_INFO)
    api.get_system_info.side_effect = HitronConnectionError("empty body")
    coordinator = await make_coordinator(api)

    with pytest.raises(UpdateFailed, match="empty body"):
        await coordinator._async_update_data()


async def test_mixed_failure_uses_last_good_window_for_hard_errors(make_coordinator):
    """With last-good data available, a mixed cycle serves the previous
    snapshot through the existing v0.3.0 window (degraded part tracked,
    hard error part counts against _LAST_GOOD_CYCLES).
    """
    coordinator, first_data = await _prime_last_good(make_coordinator)
    api = coordinator.api
    api.get_downstream_channels.side_effect = _degraded(DS_INFO)
    api.get_router_sys_info.side_effect = HitronConnectionError("router hiccup")

    # The window serves last-good for _LAST_GOOD_CYCLES (3) consecutive
    # hard failures. Keep forcing slow cycles so the hard error recurs
    # every time (a healthy fast cycle would reset the age counter —
    # that is the pre-existing v0.3.0 behavior).
    for age in (1, 2, 3):
        coordinator._cycle = _SLOW_EVERY - 1
        data = await coordinator._async_update_data()
        assert data is first_data  # the whole previous snapshot
        assert coordinator._last_good_age_cycles == age
    # Degradation was still recorded for the DOCSIS endpoint.
    assert coordinator.docsis_degraded_endpoints == [DS_INFO]

    coordinator._cycle = _SLOW_EVERY - 1
    with pytest.raises(UpdateFailed, match="router hiccup"):
        await coordinator._async_update_data()


async def test_degraded_fast_tier_on_first_refresh_succeeds(make_coordinator):
    """Live regression (2026-09-07): the firmware degraded WiFi/Client (fast
    tier) at the same time as the DOCSIS endpoints, with no last-good data.
    Setup must survive with empty device lists and record the degradation
    instead of failing every entity unavailable."""
    api = _make_api()
    coordinator = await make_coordinator(api)
    api.get_connected_devices.side_effect = _degraded("/1/Device/Hosts/1")
    api.get_wifi_clients.side_effect = _degraded("/1/Device/WiFi/Client")
    api.get_downstream_channels.side_effect = _degraded(DS_INFO)
    api.get_upstream_channels.side_effect = _degraded(US_INFO)

    data = await coordinator._async_update_data()

    assert data.devices == []
    assert data.wifi_clients == []
    assert data.downstream_channels == []
    assert coordinator.docsis_degraded is True
    assert coordinator.degraded_since is not None
    # docsis_degraded_endpoints filters to the /1/Device/CM/ family — a
    # degraded host list is a different problem (different remediation)
    assert set(coordinator.docsis_degraded_endpoints) == {DS_INFO, US_INFO}
    # the fast-tier degradation is still recorded on the full map
    assert coordinator.is_degraded("devices")
    assert coordinator.is_degraded("wifi_clients")


# ---- v0.3.2: explicit polling loop ----


async def test_poll_loop_refreshes_and_survives_errors(make_coordinator):
    """The explicit loop must refresh on the fast cadence and keep
    running through failed cycles — the live failure mode was a dead
    timer chain leaving every entity frozen."""
    coordinator = await make_coordinator(_make_api())
    coordinator.start_polling()
    try:
        assert coordinator._poll_task is not None
        assert not coordinator._poll_task.done()
        assert coordinator.update_interval is None
        # Idempotent start.
        first_task = coordinator._poll_task
        coordinator.start_polling()
        assert coordinator._poll_task is first_task

        # A failing cycle must not kill the loop.
        coordinator.api.get_connected_devices.side_effect = HitronConnectionError(
            "router hiccup"
        )
        await asyncio.sleep(0.05)
        assert not coordinator._poll_task.done()

        # ...and the next cycle succeeds again.
        coordinator.api.get_connected_devices.side_effect = None
        await asyncio.sleep(0.1)
        assert not coordinator._poll_task.done()
    finally:
        coordinator.stop_polling()
        await coordinator.async_shutdown()
    assert coordinator._poll_task.done()


async def test_poll_loop_stops_via_event(make_coordinator):
    """stop_polling() ends the loop promptly."""
    coordinator = await make_coordinator(_make_api())
    coordinator.start_polling()
    await asyncio.sleep(0.05)
    assert not coordinator._poll_task.done()
    coordinator.stop_polling()
    await asyncio.wait_for(coordinator._poll_task, timeout=5)
    assert coordinator._poll_task.done()
