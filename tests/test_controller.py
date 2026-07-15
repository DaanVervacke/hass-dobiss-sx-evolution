"""Tests for DobissController notifier lifecycle and _collect_initial_state drain."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.dobiss_sx_evolution.const import CONNECTION_TYPE_SOCKETCAND
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

    async def _one_frame() -> None:
        await _real_sleep(0.02)
        ctrl._ingest_message(_make_fake_message("A", 1, 1))

    with patch.object(ctrl, "_send_frame", new=AsyncMock()) as send:
        firing = asyncio.create_task(_one_frame())
        await ctrl.async_refresh_and_settle(idle=0.05, timeout=1.0)
        await firing

    from custom_components.dobiss_sx_evolution.protocol import DUMP_REQUEST_FRAME
    send.assert_awaited_once_with(*DUMP_REQUEST_FRAME)


async def test_async_refresh_and_settle_settles_even_when_states_match_cache(
    hass: HomeAssistant,
) -> None:
    """Refresh must return promptly even if every fresh frame matches the cache.

    Regression: an earlier version watched the state-change listener chain,
    which is only fired by _apply_local. When DOBISS replied with the same
    states we already had, no listener fired and the refresh waited the
    full timeout, leaving newly-added entities hanging for ~15 seconds.
    """
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()
    ctrl.states[("A", 1)] = 1  # cache already matches the incoming frame

    async def _same_state_frame() -> None:
        await _real_sleep(0.02)
        ctrl._ingest_message(_make_fake_message("A", 1, 1))

    with patch.object(ctrl, "_send_frame", new=AsyncMock()):
        firing = asyncio.create_task(_same_state_frame())
        loop = asyncio.get_running_loop()
        started = loop.time()
        await ctrl.async_refresh_and_settle(idle=0.05, timeout=2.0)
        elapsed = loop.time() - started
        await firing

    assert elapsed < 0.5, (
        f"Refresh took {elapsed:.3f}s even though a frame arrived quickly. "
        f"The arrival hook must fire even when the state matches the cache."
    )


async def test_async_refresh_and_settle_waits_while_updates_arrive(
    hass: HomeAssistant,
) -> None:
    """The idle timer resets on each frame arrival, so a stream keeps us waiting."""
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    async def _stream_frames(interval: float, count: int) -> None:
        for i in range(count):
            await _real_sleep(interval)
            ctrl._ingest_message(_make_fake_message("A", (i % 12) + 1, i % 2))

    with patch.object(ctrl, "_send_frame", new=AsyncMock()):
        bumper = asyncio.create_task(_stream_frames(0.01, 5))
        loop = asyncio.get_running_loop()
        started = loop.time()
        await ctrl.async_refresh_and_settle(idle=0.03, timeout=1.0)
        elapsed = loop.time() - started
        await bumper

    assert elapsed >= 0.05, (
        "Expected refresh to wait through the whole frame stream, "
        f"got {elapsed:.3f}s"
    )


async def test_async_refresh_and_settle_waits_for_first_frame(
    hass: HomeAssistant,
) -> None:
    """Refresh must wait for the first response, not exit on an initial idle window.

    Regression: an early version returned as soon as `idle` seconds passed
    with no state updates, even if that was before DOBISS had had a chance
    to start responding at all.  On a real bus with a 200-300ms round-trip
    that meant the cache stayed empty and newly-added lights rendered off.
    """
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    frame_delay = 0.2  # longer than idle so the naive implementation would exit

    async def _delayed_frame() -> None:
        await _real_sleep(frame_delay)
        ctrl._ingest_message(_make_fake_message("A", 1, 1))

    with patch.object(ctrl, "_send_frame", new=AsyncMock()):
        firing = asyncio.create_task(_delayed_frame())
        loop = asyncio.get_running_loop()
        started = loop.time()
        await ctrl.async_refresh_and_settle(idle=0.05, timeout=1.0)
        elapsed = loop.time() - started
        await firing

    assert elapsed >= frame_delay, (
        f"Refresh returned in {elapsed:.3f}s, before the first frame at "
        f"{frame_delay}s. It must wait for at least one response frame."
    )


async def test_async_refresh_and_settle_gives_up_when_bus_silent(
    hass: HomeAssistant,
) -> None:
    """If no response ever arrives, refresh must give up on the timeout."""
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    with patch.object(ctrl, "_send_frame", new=AsyncMock()):
        loop = asyncio.get_running_loop()
        started = loop.time()
        await ctrl.async_refresh_and_settle(idle=0.05, timeout=0.15)
        elapsed = loop.time() - started

    assert 0.15 <= elapsed <= 0.35, (
        f"Expected refresh to return around the 0.15s timeout, got {elapsed:.3f}s"
    )


async def test_frame_arrival_hook_ignores_tx_echoes(
    hass: HomeAssistant,
) -> None:
    """A tx echo frame (CAN_ID_TX_STATE) must not fire the arrival hook.

    Guards against spuriously settling a refresh on the loopback of our own
    write, which would let the refresh return before the DOBISS response
    starts arriving.
    """
    from custom_components.dobiss_sx_evolution.const import CAN_ID_TX_STATE

    ctrl = _make_controller(hass)
    ctrl._frame_arrival = asyncio.Event()

    echo = _make_fake_message("A", 1, 1)
    echo.arbitration_id = CAN_ID_TX_STATE
    ctrl._ingest_message(echo)

    assert not ctrl._frame_arrival.is_set(), (
        "Tx echoes must be filtered before the arrival hook fires"
    )


async def test_frame_arrival_hook_ignores_unconfigured_modules(
    hass: HomeAssistant,
) -> None:
    """Frames for modules we did not configure must not fire the arrival hook."""
    ctrl = _make_controller(hass)  # configures module "A" only
    ctrl._frame_arrival = asyncio.Event()

    stranger = _make_fake_message("B", 1, 1)
    ctrl._ingest_message(stranger)

    assert not ctrl._frame_arrival.is_set(), (
        "Frames for unconfigured modules must not fire the arrival hook"
    )


async def test_async_refresh_and_settle_serialises_concurrent_calls(
    hass: HomeAssistant,
) -> None:
    """Two overlapping refresh calls must not orphan each other's arrival event.

    Without the refresh lock, the second call would overwrite _frame_arrival
    and the first call's waiter would hang until its own timeout.  With the
    lock, the second call waits until the first completes.
    """
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    async def _feed_frames() -> None:
        # A steady drip keeps each refresh's idle timer resetting for a bit,
        # then goes silent so both eventually settle.
        for i in range(3):
            await _real_sleep(0.02)
            ctrl._ingest_message(_make_fake_message("A", i + 1, 1))

    with patch.object(ctrl, "_send_frame", new=AsyncMock()) as send:
        feeder = asyncio.create_task(_feed_frames())
        t1 = asyncio.create_task(ctrl.async_refresh_and_settle(idle=0.05, timeout=1.0))
        t2 = asyncio.create_task(ctrl.async_refresh_and_settle(idle=0.05, timeout=1.0))
        await asyncio.gather(t1, t2)
        await feeder

    assert send.await_count == 2, (
        f"Both refresh calls must send a dump; got {send.await_count}"
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


async def test_async_shutdown_closes_bus_even_when_notifier_raises(
    hass: HomeAssistant,
) -> None:
    """bus.shutdown() must run even if notifier.stop() raises."""
    ctrl = _make_controller(hass)

    fake_notifier = MagicMock()
    fake_notifier.stop = MagicMock(side_effect=RuntimeError("boom"))
    fake_bus = MagicMock()

    ctrl._notifier = fake_notifier
    ctrl._reader = MagicMock()
    ctrl._bus = fake_bus

    await ctrl.async_shutdown()

    fake_bus.shutdown.assert_called_once()
    assert ctrl._notifier is None
    assert ctrl._reader is None
    assert ctrl._bus is None
