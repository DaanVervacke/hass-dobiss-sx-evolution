"""Tests for repair issue creation/deletion in DobissController."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import DOMAIN
from custom_components.dobiss_sx_evolution.controller import (
    RECONNECT_BACKOFF_INITIAL_S,
    RECONNECT_BACKOFF_MAX_S,
    DobissController,
    SocketcandConnection,
)

_real_sleep = asyncio.sleep

_TEST_CONNECTION = SocketcandConnection(
    host="192.168.1.10", port=29536, interface="can0"
)


def _make_controller(
    hass: HomeAssistant, entry_id: str = "test_entry_id"
) -> DobissController:
    """Build a minimal DobissController with no outputs."""
    return DobissController(
        hass,
        connection=_TEST_CONNECTION,
        lights=[],
        dimmers=[],
        shutters=[],
        entry_id=entry_id,
    )


def _make_entry(
    hass: HomeAssistant, entry_id: str = "test_entry_id"
) -> MockConfigEntry:
    """Register a minimal config entry with hass and return it."""
    from custom_components.dobiss_sx_evolution.const import CONNECTION_TYPE_SOCKETCAND

    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=entry_id,
        data={
            "connection_type": CONNECTION_TYPE_SOCKETCAND,
            "host": "192.168.1.10",
            "port": 29536,
            "interface": "can0",
        },
        title="DOBISS Test",
    )
    entry.add_to_hass(hass)
    return entry


async def test_raise_repair_issue_creates_issue(hass: HomeAssistant) -> None:
    """_raise_repair_issue creates an issue in the registry."""
    ctrl = _make_controller(hass)
    assert not ctrl._repair_issue_active

    ctrl._raise_repair_issue()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ctrl._issue_id)
    assert issue is not None
    assert issue.translation_key == "cannot_connect"
    assert ctrl._repair_issue_active is True


async def test_raise_repair_issue_idempotent(hass: HomeAssistant) -> None:
    """Calling _raise_repair_issue twice does not duplicate the issue."""
    ctrl = _make_controller(hass)
    ctrl._raise_repair_issue()
    ctrl._raise_repair_issue()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ctrl._issue_id)
    assert issue is not None
    assert ctrl._repair_issue_active is True


async def test_clear_repair_issue_removes_issue(hass: HomeAssistant) -> None:
    """_clear_repair_issue removes the issue once the connection recovers."""
    ctrl = _make_controller(hass)
    ctrl._raise_repair_issue()

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, ctrl._issue_id) is not None

    ctrl._clear_repair_issue()

    assert issue_reg.async_get_issue(DOMAIN, ctrl._issue_id) is None
    assert ctrl._repair_issue_active is False


async def test_clear_repair_issue_idempotent(hass: HomeAssistant) -> None:
    """Calling _clear_repair_issue when no issue is active is safe."""
    ctrl = _make_controller(hass)
    assert not ctrl._repair_issue_active
    # Should not raise
    ctrl._clear_repair_issue()
    assert ctrl._repair_issue_active is False


async def test_issue_id_is_entry_specific(hass: HomeAssistant) -> None:
    """Each entry gets its own issue ID so multiple entries don't collide."""
    ctrl_a = _make_controller(hass, entry_id="entry_aaa")
    ctrl_b = _make_controller(hass, entry_id="entry_bbb")
    assert ctrl_a._issue_id != ctrl_b._issue_id
    assert "entry_aaa" in ctrl_a._issue_id
    assert "entry_bbb" in ctrl_b._issue_id


# ---------------------------------------------------------------------------
# Backoff + reauth path tests
# ---------------------------------------------------------------------------
#
# Strategy for testing _read_loop without real sleeps:
#  - Patch controller.asyncio.sleep with a no-op that still yields via the
#    *real* asyncio.sleep(0) (captured before patching), so the event loop
#    stays healthy and hass.async_block_till_done() works correctly.
#  - Use an asyncio.Event as a "loop gate" to limit how many iterations run
#    before the task is cancelled, avoiding infinite loops.
# ---------------------------------------------------------------------------


def _make_noopslep(recorded: list[float] | None = None):
    """Return a fake asyncio.sleep that records delays and yields once."""

    async def _fake(delay: float) -> None:
        if recorded is not None:
            recorded.append(delay)
        # Yield to the real event loop without actually waiting.
        await _real_sleep(0)

    return _fake


async def _drain(hass: HomeAssistant, n: int = 10) -> None:
    """Let the event loop flush pending callbacks n times."""
    for _ in range(n):
        await _real_sleep(0)


async def test_backoff_caps_at_max(hass: HomeAssistant) -> None:
    """Backoff doubles on each reconnect failure and caps at RECONNECT_BACKOFF_MAX_S."""
    entry_id = "backoff_test_entry"
    _make_entry(hass, entry_id)
    ctrl = _make_controller(hass, entry_id)

    sleep_delays: list[float] = []
    fail_after = 8  # more than enough to saturate (need 6)
    open_calls = 0

    async def fake_read_frames() -> None:
        raise OSError("simulated read failure")

    async def fake_open_bus() -> None:
        nonlocal open_calls
        open_calls += 1
        if open_calls <= fail_after:
            raise OSError("simulated reconnect failure")

    with (
        patch.object(ctrl, "_read_frames", side_effect=fake_read_frames),
        patch.object(ctrl, "_open_bus", side_effect=fake_open_bus),
        patch(
            "custom_components.dobiss_sx_evolution.controller.asyncio.sleep",
            side_effect=_make_noopslep(sleep_delays),
        ),
    ):
        task = hass.async_create_background_task(ctrl._read_loop(), "test_backoff")
        # Drain enough real event-loop rounds for fail_after+1 iterations to complete.
        await _drain(hass, n=(fail_after + 2) * 5)
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task

    assert sleep_delays, "Expected sleep to be called at least once"
    assert all(d <= RECONNECT_BACKOFF_MAX_S for d in sleep_delays), (
        f"Some sleep delay exceeded max: {sleep_delays}"
    )
    # After enough failures the delays should reach the ceiling.
    assert RECONNECT_BACKOFF_MAX_S in sleep_delays, (
        f"Expected {RECONNECT_BACKOFF_MAX_S} in delays: {sleep_delays}"
    )
    # First sleep must use the initial backoff.
    assert sleep_delays[0] == RECONNECT_BACKOFF_INITIAL_S


async def test_reauth_started_when_backoff_saturates(hass: HomeAssistant) -> None:
    """async_start_reauth is called once backoff hits RECONNECT_BACKOFF_MAX_S."""
    entry_id = "reauth_test_entry"
    entry = _make_entry(hass, entry_id)
    ctrl = _make_controller(hass, entry_id)

    reauth_calls: list[int] = []
    original_start_reauth = entry.async_start_reauth

    def _track_reauth(h: HomeAssistant) -> None:
        reauth_calls.append(1)

    entry.async_start_reauth = _track_reauth  # type: ignore[method-assign]

    fail_count = 7  # 6 needed to saturate; 7 for margin
    open_calls = 0

    async def fake_read_frames() -> None:
        raise OSError("simulated read failure")

    async def fake_open_bus() -> None:
        nonlocal open_calls
        open_calls += 1
        if open_calls <= fail_count:
            raise OSError("simulated reconnect failure")

    try:
        with (
            patch.object(ctrl, "_read_frames", side_effect=fake_read_frames),
            patch.object(ctrl, "_open_bus", side_effect=fake_open_bus),
            patch(
                "custom_components.dobiss_sx_evolution.controller.asyncio.sleep",
                side_effect=_make_noopslep(),
            ),
        ):
            task = hass.async_create_background_task(ctrl._read_loop(), "test_reauth")
            await _drain(hass, n=(fail_count + 2) * 5)
            task.cancel()
            with pytest.raises((asyncio.CancelledError, Exception)):
                await task
    finally:
        entry.async_start_reauth = original_start_reauth  # type: ignore[method-assign]

    # Repair issue must exist.
    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, ctrl._issue_id) is not None
    assert ctrl._repair_issue_active is True

    # async_start_reauth must have been called exactly once (idempotent guard).
    assert reauth_calls == [1], (
        f"Expected reauth called once, got {len(reauth_calls)} calls"
    )


async def test_repair_clears_on_reconnect(hass: HomeAssistant) -> None:
    """Repair issue is removed and reconnect_count increments after recovery."""
    entry_id = "recover_test_entry"
    _make_entry(hass, entry_id)
    ctrl = _make_controller(hass, entry_id)

    # Saturate backoff first (6 failures), then let one reconnect succeed.
    fail_count = 6
    open_calls = 0
    # After the first successful open, make _read_frames raise so the loop
    # keeps running without us needing to drive it forever.
    read_calls = 0

    async def fake_read_frames() -> None:
        nonlocal read_calls
        read_calls += 1
        raise OSError("simulated read failure")

    async def fake_open_bus() -> None:
        nonlocal open_calls
        open_calls += 1
        if open_calls <= fail_count:
            raise OSError("simulated reconnect failure")
        # Subsequent calls succeed silently.

    with (
        patch.object(ctrl, "_read_frames", side_effect=fake_read_frames),
        patch.object(ctrl, "_open_bus", side_effect=fake_open_bus),
        patch(
            "custom_components.dobiss_sx_evolution.controller.asyncio.sleep",
            side_effect=_make_noopslep(),
        ),
        # _send_frame is called (DUMP_REQUEST_FRAME) after a successful open_bus.
        patch.object(ctrl, "_send_frame", new=AsyncMock()),
    ):
        task = hass.async_create_background_task(ctrl._read_loop(), "test_recover")
        # Run enough rounds: fail_count saturation + 1 successful reconnect + margin.
        await _drain(hass, n=(fail_count + 4) * 5)
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task

    # After recovery the repair issue must be cleared.
    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, ctrl._issue_id) is None
    assert ctrl._repair_issue_active is False

    # reconnect_count must have been incremented at least once.
    assert ctrl.reconnect_count >= 1
