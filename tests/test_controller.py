"""Tests for DobissController notifier lifecycle and _collect_initial_state drain."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.dobiss_sx_evolution.const import (
    CAN_ID_RX_STATE,
    CAN_ID_STATE_DUMP,
    MAX_CAN_BRIGHTNESS_TX,
)
from custom_components.dobiss_sx_evolution.controller import (
    _DUMP_DRAIN_IDLE_S,
    DobissController,
    ShutterConfig,
    SocketcandConnection,
)
from custom_components.dobiss_sx_evolution.protocol import ha_to_can_brightness

_real_sleep = asyncio.sleep

_TEST_CONNECTION = SocketcandConnection(
    host="192.168.1.10", port=29536, interface="can0"
)


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


def _make_controller(hass: HomeAssistant) -> DobissController:
    return DobissController(
        hass,
        connection=_TEST_CONNECTION,
        lights=[("A", 1)],
        dimmers=[],
        shutters=[],
        entry_id="test_ctrl",
    )


def _make_full_controller(hass: HomeAssistant) -> DobissController:
    """Controller with a non-dimmable light, a dimmable light, and a shutter."""
    return DobissController(
        hass,
        connection=_TEST_CONNECTION,
        lights=[("A", 1)],
        dimmers={("A", 2)},
        shutters=[ShutterConfig(module="A", up_output=9, down_output=10)],
        entry_id="test_full",
    )


def _make_fake_message(module: str = "A", output: int = 1, state: int = 1) -> MagicMock:
    """Return a mock CAN message that parse_state_frame will recognise."""
    from custom_components.dobiss_sx_evolution.protocol import (  # noqa: PLC0415
        build_state_frame,
    )

    frame = build_state_frame(module, output, state)
    msg = MagicMock()
    msg.arbitration_id = CAN_ID_RX_STATE
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


async def test_open_bus_tears_down_notifier_before_replacing_bus(
    hass: HomeAssistant,
) -> None:
    """_open_bus must stop the old notifier before shutting down the old bus.

    The notifier's reader-thread keeps bus.recv() alive; tearing it down
    after the old bus is already shut down would race the read thread
    against a bus that has gone away underneath it.
    """
    ctrl = _make_controller(hass)

    old_bus = MagicMock()
    fake_notifier = MagicMock()
    ctrl._bus = old_bus
    ctrl._notifier = fake_notifier
    ctrl._reader = MagicMock()

    call_order: list[str] = []
    original_teardown = ctrl._teardown_notifier

    async def _tracked_teardown() -> None:
        call_order.append("teardown_notifier")
        await original_teardown()

    old_bus.shutdown = MagicMock(side_effect=lambda: call_order.append("bus.shutdown"))

    new_bus = MagicMock()

    with (
        patch.object(ctrl, "_teardown_notifier", side_effect=_tracked_teardown),
        patch.object(SocketcandConnection, "make_bus", return_value=new_bus),
    ):
        await ctrl._open_bus()

    assert call_order == ["teardown_notifier", "bus.shutdown"], (
        f"Expected teardown_notifier before old bus.shutdown, got {call_order}"
    )
    assert ctrl._bus is new_bus


async def test_read_loop_reconnect_sets_up_notifier_before_dump(
    hass: HomeAssistant,
) -> None:
    """_read_loop must call _setup_notifier before sending the reconnect dump.

    Regression guard: _open_bus tears down the notifier when it replaces the
    bus, so the reconnect path must re-create it (via _setup_notifier)
    before the dump request goes out. Otherwise the echoed dump burst has
    nowhere to land and reconnect discovery silently drops frames.
    """
    ctrl = _make_controller(hass)

    call_order: list[str] = []
    read_calls = 0

    async def fake_read_frames() -> None:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            raise OSError("simulated read failure")
        raise asyncio.CancelledError

    async def fake_open_bus() -> None:
        return None

    async def fake_setup_notifier() -> None:
        call_order.append("setup_notifier")

    async def fake_send_frame(can_id: int, data: bytes) -> None:
        if can_id == CAN_ID_STATE_DUMP:
            call_order.append("send_frame_dump")

    with (
        patch.object(ctrl, "_read_frames", side_effect=fake_read_frames),
        patch.object(ctrl, "_open_bus", side_effect=fake_open_bus),
        patch.object(ctrl, "_setup_notifier", side_effect=fake_setup_notifier),
        patch.object(ctrl, "_send_frame", side_effect=fake_send_frame),
        patch(
            "custom_components.dobiss_sx_evolution.controller.asyncio.sleep",
            side_effect=_make_noopslep(),
        ),
    ):
        task = hass.async_create_background_task(
            ctrl._read_loop(), "test_reconnect_order"
        )
        await _drain(hass, n=40)
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task

    assert call_order == ["setup_notifier", "send_frame_dump"], (
        f"Expected setup_notifier before the dump request, got {call_order}"
    )


async def test_collect_initial_state_drains_all_frames(hass: HomeAssistant) -> None:
    """_collect_initial_state must drain frames until idle, not just the first."""
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

    with (
        patch.object(
            _can,
            "Notifier",
            side_effect=lambda *a, **kw: notifier_ctor_calls.append(1) or MagicMock(),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
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

    from custom_components.dobiss_sx_evolution.protocol import (  # noqa: PLC0415
        DUMP_REQUEST_FRAME,
    )

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
        f"Expected refresh to wait through the whole frame stream, got {elapsed:.3f}s"
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
    from custom_components.dobiss_sx_evolution.const import (  # noqa: PLC0415
        CAN_ID_TX_STATE,
    )

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


async def test_open_bus_closes_orphaned_bus_when_cancelled_mid_connect(
    hass: HomeAssistant,
) -> None:
    """If _open_bus's await of the executor job is cancelled after make_bus()
    already returned a bus handle (the CancelledError races the executor
    result), that handle must be closed rather than leaked — it was never
    assigned to self._bus.

    This race is genuinely timing-dependent in production (a real
    ThreadPoolExecutor result racing a Task.cancel() call), which makes it
    impractical to reproduce deterministically with real threads. Instead we
    substitute a fake awaitable in place of the executor job: it reports
    itself as done with the real bus as its result (exactly like an executor
    Future that resolved just before cancellation was delivered), but raises
    CancelledError from __await__ (exactly what `await fut` does once the
    owning Task has been cancelled). This exercises the precise code path in
    `_open_bus`'s except handler without relying on thread-scheduling luck.
    """
    ctrl = _make_controller(hass)
    fake_bus = MagicMock()

    class _RacedExecutorFuture:
        """Mimics an executor Future that finished before cancellation landed."""

        def done(self) -> bool:
            return True

        def cancelled(self) -> bool:
            return False

        def exception(self) -> BaseException | None:
            return None

        def result(self) -> MagicMock:
            return fake_bus

        def __await__(self):
            raise asyncio.CancelledError
            yield  # pragma: no cover - unreachable, satisfies generator syntax

    def fake_async_add_executor_job(*args, **kwargs):
        return _RacedExecutorFuture()

    with (
        patch.object(
            ctrl.hass, "async_add_executor_job", side_effect=fake_async_add_executor_job
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await ctrl._open_bus()

    fake_bus.shutdown.assert_called_once()
    assert ctrl._bus is None


async def test_ingest_rejects_unknown_arbitration_id(hass: HomeAssistant) -> None:
    """Frames on unknown CAN IDs must be silently dropped."""
    ctrl = _make_controller(hass)
    ctrl.states[("A", 1)] = 0

    msg = _make_fake_message(module="A", output=1, state=1)
    msg.arbitration_id = 0xDEAD  # not CAN_ID_RX_STATE

    result = ctrl._ingest_message(msg)
    assert result is None
    assert ctrl.states[("A", 1)] == 0  # unchanged


async def test_read_frames_raises_on_liveness_timeout(hass: HomeAssistant) -> None:
    """_read_frames must raise RuntimeError if the reader stays silent too long."""
    ctrl = _make_controller(hass)
    ctrl._bus = MagicMock()

    async def fake_get_message():
        await asyncio.Event().wait()

    fake_reader = MagicMock()
    fake_reader.get_message = fake_get_message
    ctrl._reader = fake_reader
    ctrl._notifier = MagicMock()

    with (
        patch(
            "custom_components.dobiss_sx_evolution.controller.LIVENESS_TIMEOUT_S",
            0.1,
        ),
        pytest.raises(RuntimeError, match="No CAN frames received"),
    ):
        await ctrl._read_frames()


async def test_turn_on_non_dimmable_sends_value_1(hass: HomeAssistant) -> None:
    """Non-dimmable turn_on sends value=1 regardless of brightness arg."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    await ctrl.async_turn_on(("A", 1))
    ctrl._send_frame.assert_awaited_once()
    _, data = ctrl._send_frame.call_args[0]
    assert data[3] == 1
    assert ctrl.states[("A", 1)] == 1


async def test_turn_on_dimmable_with_brightness(hass: HomeAssistant) -> None:
    """Dimmable turn_on with explicit brightness sends converted CAN value."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    await ctrl.async_turn_on(("A", 2), brightness=128)
    _, data = ctrl._send_frame.call_args[0]
    expected = ha_to_can_brightness(128)
    assert data[3] == expected
    assert ctrl.states[("A", 2)] == expected


async def test_turn_on_dimmable_no_brightness_sends_max(hass: HomeAssistant) -> None:
    """Dimmable turn_on without brightness sends MAX_CAN_BRIGHTNESS_TX."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    await ctrl.async_turn_on(("A", 2))
    _, data = ctrl._send_frame.call_args[0]
    assert data[3] == MAX_CAN_BRIGHTNESS_TX
    assert ctrl.states[("A", 2)] == MAX_CAN_BRIGHTNESS_TX


async def test_turn_off_sends_zero_and_clears_state(hass: HomeAssistant) -> None:
    """turn_off sends value=0 and updates local state."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    ctrl.states[("A", 1)] = 1
    await ctrl.async_turn_off(("A", 1))
    _, data = ctrl._send_frame.call_args[0]
    assert data[3] == 0
    assert ctrl.states[("A", 1)] == 0


async def test_open_shutter_sets_up_clears_down(hass: HomeAssistant) -> None:
    """open_shutter sends up=1 and locally sets up=1, down=0."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    shutter = ShutterConfig(module="A", up_output=9, down_output=10)
    await ctrl.async_open_shutter(shutter)
    assert ctrl.states[("A", 9)] == 1
    assert ctrl.states[("A", 10)] == 0


async def test_close_shutter_sets_down_clears_up(hass: HomeAssistant) -> None:
    """close_shutter sends down=1 and locally sets down=1, up=0."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    shutter = ShutterConfig(module="A", up_output=9, down_output=10)
    await ctrl.async_close_shutter(shutter)
    assert ctrl.states[("A", 10)] == 1
    assert ctrl.states[("A", 9)] == 0


async def test_stop_shutter_clears_both(hass: HomeAssistant) -> None:
    """stop_shutter sends up=0 and locally clears both up and down."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    ctrl.states[("A", 9)] = 1
    ctrl.states[("A", 10)] = 0
    shutter = ShutterConfig(module="A", up_output=9, down_output=10)
    await ctrl.async_stop_shutter(shutter)
    assert ctrl.states[("A", 9)] == 0
    assert ctrl.states[("A", 10)] == 0


async def test_turn_on_fires_listener(hass: HomeAssistant) -> None:
    """Command methods must fire registered listeners via _apply_local."""
    ctrl = _make_full_controller(hass)
    ctrl._send_frame = AsyncMock()
    received = []
    ctrl.async_add_listener(lambda key, val: received.append((key, val)))
    await ctrl.async_turn_on(("A", 1))
    assert received == [(("A", 1), 1)]
