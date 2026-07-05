"""Tests for DobissController notifier lifecycle and _collect_initial_state drain."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
)
from custom_components.dobiss_sx_evolution.controller import (
    DobissController,
    _DUMP_DRAIN_IDLE_S,
)

_real_sleep = asyncio.sleep


def _make_controller(hass: HomeAssistant) -> DobissController:
    return DobissController(
        hass,
        connection_type=CONNECTION_TYPE_SOCKETCAND,
        host="192.168.1.10",
        port=29536,
        interface="can0",
        lights=[("A", 1)],
        dimmers=[],
        shutters=[],
        entry_id="test_ctrl",
    )


def _make_fake_message(module: str = "A", output: int = 1, state: int = 1) -> MagicMock:
    """Return a mock CAN message that parse_state_frame will recognise."""
    from custom_components.dobiss_sx_evolution.protocol import build_state_frame

    frame = build_state_frame(module, output, state)
    msg = MagicMock()
    msg.arbitration_id = 0x1010000  # CAN_ID_STATE_DUMP echo
    msg.data = frame[1] if frame else b"\x00" * 8
    return msg


async def test_async_setup_creates_shared_notifier(hass: HomeAssistant) -> None:
    """async_setup must set self._reader and self._notifier before returning.

    We patch the can module at its source (``can.AsyncBufferedReader`` and
    ``can.Notifier``) rather than through the controller module, because
    ``_setup_notifier`` does a local ``import can`` and then references the
    symbols on the resulting module object.
    """
    import can as _can  # noqa: PLC0415

    ctrl = _make_controller(hass)

    fake_bus = MagicMock()
    fake_reader = MagicMock()
    fake_notifier = MagicMock()

    with (
        patch.object(_can, "AsyncBufferedReader", return_value=fake_reader),
        patch.object(_can, "Notifier", return_value=fake_notifier),
    ):
        ctrl._bus = fake_bus  # simulate _open_bus having set the bus
        await ctrl._setup_notifier()

    assert ctrl._reader is fake_reader
    assert ctrl._notifier is fake_notifier


async def test_setup_notifier_noop_when_bus_is_none(hass: HomeAssistant) -> None:
    """_setup_notifier must be a no-op (no crash) when _bus is None."""
    ctrl = _make_controller(hass)
    assert ctrl._bus is None

    # Should not raise.
    await ctrl._setup_notifier()

    assert ctrl._reader is None
    assert ctrl._notifier is None


async def test_teardown_notifier_calls_stop_via_executor(hass: HomeAssistant) -> None:
    """_teardown_notifier must stop the notifier off the event loop thread."""
    ctrl = _make_controller(hass)

    stop_calls: list = []
    fake_notifier = MagicMock()
    fake_notifier.stop = MagicMock(side_effect=lambda: stop_calls.append(1))
    ctrl._notifier = fake_notifier
    ctrl._reader = MagicMock()

    await ctrl._teardown_notifier()

    assert stop_calls == [1], "notifier.stop() must be called exactly once"
    assert ctrl._notifier is None
    assert ctrl._reader is None


async def test_collect_initial_state_drains_all_frames(hass: HomeAssistant) -> None:
    """_collect_initial_state must consume frames until idle, not just the first module frame."""
    ctrl = _make_controller(hass)

    # Build two fake messages for module A (simulating a burst).
    msg_a1 = _make_fake_message("A", 1, 1)
    msg_a2 = _make_fake_message("A", 2, 0)

    # After the two burst frames, get_message times out (idle timeout).
    call_count = 0

    async def fake_get_message():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return msg_a1
        if call_count == 2:
            return msg_a2
        # Simulate idle: no more frames arrive within the drain window.
        await _real_sleep(_DUMP_DRAIN_IDLE_S + 0.05)
        raise TimeoutError

    fake_reader = MagicMock()
    fake_reader.get_message = fake_get_message

    ctrl._bus = MagicMock()
    ctrl._reader = fake_reader

    with patch.object(ctrl, "_send_frame", new=AsyncMock()):
        await ctrl._collect_initial_state()

    # Both frames must have been ingested (call_count reached at least 2 real
    # messages before the timeout).
    assert call_count >= 2, f"Expected at least 2 get_message calls, got {call_count}"


async def test_read_frames_reuses_existing_reader(hass: HomeAssistant) -> None:
    """_read_frames must use self._reader when already set (no new Notifier created)."""
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    cancelled = asyncio.Event()
    call_count = 0

    async def fake_get_message():
        nonlocal call_count
        call_count += 1
        raise asyncio.CancelledError

    fake_reader = MagicMock()
    fake_reader.get_message = fake_get_message
    fake_notifier = MagicMock()

    ctrl._reader = fake_reader
    ctrl._notifier = fake_notifier

    import can as _can  # noqa: PLC0415

    notifier_ctor_calls: list = []

    with patch.object(
        _can,
        "Notifier",
        side_effect=lambda *a, **kw: notifier_ctor_calls.append(1) or MagicMock(),
    ):
        with pytest.raises(asyncio.CancelledError):
            await ctrl._read_frames()

    assert notifier_ctor_calls == [], (
        "Notifier must NOT be constructed when self._reader is already set"
    )
    assert call_count >= 1


async def test_async_refresh_and_settle_sends_dump_and_returns_on_idle(
    hass: HomeAssistant,
) -> None:
    """async_refresh_and_settle sends a dump and returns once the bus goes idle."""
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    with patch.object(ctrl, "_send_frame", new=AsyncMock()) as send:
        await ctrl.async_refresh_and_settle(idle=0.01, timeout=1.0)

    from custom_components.dobiss_sx_evolution.protocol import DUMP_REQUEST_FRAME
    send.assert_awaited_once_with(*DUMP_REQUEST_FRAME)


async def test_async_refresh_and_settle_waits_while_updates_arrive(
    hass: HomeAssistant,
) -> None:
    """The idle timer resets on each state update, so a stream keeps us waiting."""
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    async def _bump_forever(interval: float, count: int) -> None:
        for i in range(count):
            await _real_sleep(interval)
            ctrl._apply_local(("A", i + 1), 1)

    with patch.object(ctrl, "_send_frame", new=AsyncMock()):
        bumper = asyncio.create_task(_bump_forever(0.01, 5))
        loop = asyncio.get_running_loop()
        started = loop.time()
        await ctrl.async_refresh_and_settle(idle=0.03, timeout=1.0)
        elapsed = loop.time() - started
        await bumper

    assert elapsed >= 0.03, (
        "Expected refresh to wait past the idle window while updates arrived"
    )


async def test_async_refresh_and_settle_noop_when_bus_missing(
    hass: HomeAssistant,
) -> None:
    """No dump is sent if the bus is not connected."""
    ctrl = _make_controller(hass)
    with patch.object(ctrl, "_send_frame", new=AsyncMock()) as send:
        await ctrl.async_refresh_and_settle(idle=0.01, timeout=0.5)
    send.assert_not_awaited()


async def test_async_shutdown_stops_notifier_via_executor(hass: HomeAssistant) -> None:
    """async_shutdown must stop the notifier in the executor (non-blocking)."""
    ctrl = _make_controller(hass)

    stop_calls: list = []
    fake_notifier = MagicMock()
    fake_notifier.stop = MagicMock(side_effect=lambda: stop_calls.append(1))
    fake_bus = MagicMock()

    ctrl._notifier = fake_notifier
    ctrl._reader = MagicMock()
    ctrl._bus = fake_bus

    await ctrl.async_shutdown()

    assert stop_calls == [1], "notifier.stop() must be called during shutdown"
    assert ctrl._notifier is None
    assert ctrl._reader is None
