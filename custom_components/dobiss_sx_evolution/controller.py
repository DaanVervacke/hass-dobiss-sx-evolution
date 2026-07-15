"""DOBISS controller - owns the CAN bus connection and dispatches updates."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity

from .const import (
    CAN_ID_TX_STATE,
    DISCOVERY_TIMEOUT_S,
    DOMAIN,
    MAX_CAN_BRIGHTNESS_TX,
)
from .protocol import (
    DUMP_REQUEST_FRAME,
    build_state_frame,
    ha_to_can_brightness,
    parse_state_frame,
)

if TYPE_CHECKING:
    import can

_LOGGER = logging.getLogger(__name__)

RECONNECT_BACKOFF_INITIAL_S = 1.0
RECONNECT_BACKOFF_MAX_S = 60.0

# After all configured modules are seen, keep draining frames until the bus
# has been quiet for this many seconds.  DOBISS streams a tight burst after
# DUMP_REQUEST; 150 ms of silence reliably means the burst is over.
_DUMP_DRAIN_IDLE_S = 0.15

# Fast TCP reachability check - python-can's socketcand client busy-loops for
# 10s on a closed/unreachable port, spamming the log. We pre-check the socket
# so we surface OSError in ~2s and skip the noisy retry loop entirely.
_TCP_PRECHECK_TIMEOUT_S = 2.0

type OutputKey = tuple[str, int]


@dataclass(frozen=True)
class ShutterConfig:
    """A shutter mapped to a paired up/down output."""

    module: str
    up_output: int
    down_output: int


@dataclass(frozen=True)
class SocketcandConnection:
    """Socketcand (TCP) connection parameters."""

    host: str
    port: int
    interface: str

    def make_bus(self) -> can.BusABC:
        """Open and return a python-can socketcand Bus.

        Must be called from an executor thread.
        """
        import can  # noqa: PLC0415

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(_TCP_PRECHECK_TIMEOUT_S)
            probe.connect((self.host, self.port))

        return can.Bus(
            interface="socketcand",
            channel=self.interface,
            host=self.host,
            port=self.port,
        )

    @property
    def description(self) -> str:
        """Human-readable connection description for error messages."""
        return f"{self.host}:{self.port}/{self.interface}"

    @property
    def repair_placeholders(self) -> dict[str, str]:
        """Translation placeholders for the cannot_connect repair issue."""
        placeholders: dict[str, str] = {}
        if self.host:
            placeholders["host"] = self.host
        if self.interface:
            placeholders["interface"] = self.interface
        return placeholders


@dataclass(frozen=True)
class UsbConnection:
    """USB CAN adapter (serial) connection parameters."""

    device: str
    baudrate: int
    can_interface: str

    def make_bus(self) -> can.BusABC:
        """Open and return a python-can USB Bus.

        Must be called from an executor thread.
        """
        import can  # noqa: PLC0415

        kwargs: dict[str, Any] = (
            {"tty_baudrate": self.baudrate}
            if self.can_interface == "slcan"
            else {"baudrate": self.baudrate}
        )
        return can.Bus(
            interface=self.can_interface, channel=self.device, **kwargs
        )

    @property
    def description(self) -> str:
        """Human-readable connection description for error messages."""
        return f"{self.device}@{self.baudrate}baud"

    @property
    def repair_placeholders(self) -> dict[str, str]:
        """Translation placeholders for the cannot_connect repair issue."""
        placeholders: dict[str, str] = {}
        if self.device:
            placeholders["device"] = self.device
        return placeholders


type ConnectionConfig = SocketcandConnection | UsbConnection


class DobissController:
    """Owns the CAN bus, runs the read loop, and applies state writes."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection: ConnectionConfig,
        lights: list[OutputKey],
        dimmers: list[OutputKey],
        shutters: list[ShutterConfig],
        entry_id: str = "",
    ) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.connection = connection
        self.lights = lights
        self.dimmers = dimmers
        self.shutters = shutters
        self.modules: list[str] = sorted(
            {m for m, _ in (*lights, *dimmers)} | {s.module for s in shutters}
        )
        self.states: dict[OutputKey, int] = {}
        self.reconnect_count: int = 0
        self._entry_id: str = entry_id
        self._issue_id: str = f"cannot_connect_{entry_id}"
        self._bus: can.BusABC | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._listeners: list[Callable[[OutputKey, int], None]] = []
        self._repair_issue_active: bool = False
        # Set by async_refresh_and_settle while it is waiting, and fired from
        # _ingest_message on every valid inbound frame (before the state-match
        # filter).  This lets the refresh detect the response burst even when
        # the fresh values match the cache and would otherwise fire no
        # state-change listeners.
        self._frame_arrival: asyncio.Event | None = None
        # Serialises concurrent async_refresh_and_settle calls so the second
        # caller does not overwrite the first caller's event.
        self._refresh_lock: asyncio.Lock = asyncio.Lock()
        # Reader/notifier created once during setup and reused by _read_frames
        # so there is never a gap (or a blocking notifier.stop()) between
        # discovery and the running read loop.
        self._reader: can.AsyncBufferedReader | None = None
        self._notifier: can.Notifier | None = None

    @property
    def is_bus_connected(self) -> bool:
        """Return True when the CAN bus connection is open."""
        return self._bus is not None

    def dimmable(self, key: OutputKey) -> bool:
        """Return whether this output is configured as a dimmer."""
        return key in self.dimmers

    @callback
    def async_add_listener(
        self, listener: Callable[[OutputKey, int], None]
    ) -> Callable[[], None]:
        """Register a callback fired on each state update. Returns a remover."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    async def async_setup(self) -> None:
        """Open the bus, request a state dump, then start the read loop.

        The Notifier and AsyncBufferedReader are created here once (via
        _setup_notifier) and reused by both _collect_initial_state and
        _read_frames.  This eliminates the gap between discovery and the
        running read loop — no frames are lost, and notifier.stop() (which
        blocks the event loop for up to one second while it joins the reader
        thread) is never called between the two phases.
        """
        await self._open_bus()
        await self._setup_notifier()
        await self._collect_initial_state()
        self._reader_task = self.hass.async_create_background_task(
            self._read_loop(), f"dobiss_sx_evolution[{self.connection.description}]"
        )

    async def _teardown_notifier(self) -> None:
        """Stop and discard the current notifier (executor so join doesn't block)."""
        if self._notifier is not None:
            notifier = self._notifier
            self._notifier = None
            self._reader = None
            try:
                await self.hass.async_add_executor_job(notifier.stop)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Error stopping notifier", exc_info=True)

    async def _setup_notifier(self) -> None:
        """Create a fresh AsyncBufferedReader/Notifier for the current bus.

        Safe to call when self._reader/_notifier are already None (post-teardown).
        """
        import can  # noqa: PLC0415

        if self._bus is None:
            return
        reader = can.AsyncBufferedReader()
        notifier = can.Notifier(
            self._bus,
            [reader],
            timeout=0.1,
        )
        self._reader = reader
        self._notifier = notifier

    async def _open_bus(self) -> None:
        """(Re-)open the CAN bus, closing the old one if any."""
        # The notifier holds a reader-thread that keeps bus.recv() alive.
        # Stop it before replacing the bus so the thread exits cleanly.
        await self._teardown_notifier()
        if self._bus is not None:
            old = self._bus
            self._bus = None
            try:
                await self.hass.async_add_executor_job(old.shutdown)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Error closing stale bus", exc_info=True)

        self._bus = await self.hass.async_add_executor_job(
            self.connection.make_bus
        )

    async def async_shutdown(self) -> None:
        """Cancel the reader and close the bus."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        # Stop the notifier via the executor so that the reader-thread join
        # doesn't block the event loop.
        await self._teardown_notifier()
        if self._bus is not None:
            bus = self._bus
            self._bus = None
            await self.hass.async_add_executor_job(bus.shutdown)

    async def async_turn_on(
        self, key: OutputKey, brightness: int | None = None
    ) -> None:
        """Send an ON write. brightness is HA-scaled (0–255) for dimmable outputs."""
        module, output = key
        if self.dimmable(key):
            if brightness is not None:
                value = ha_to_can_brightness(brightness)
            else:
                value = MAX_CAN_BRIGHTNESS_TX
        else:
            value = 1
        await self._send_state(module, output, value)
        self._apply_local(key, value)

    async def async_turn_off(self, key: OutputKey) -> None:
        """Send an OFF write."""
        module, output = key
        await self._send_state(module, output, 0)
        self._apply_local(key, 0)

    async def async_open_shutter(self, shutter: ShutterConfig) -> None:
        """Drive the shutter up. Active while up_output > 0."""
        await self._send_state(shutter.module, shutter.up_output, 1)
        self._apply_local((shutter.module, shutter.up_output), 1)
        self._apply_local((shutter.module, shutter.down_output), 0)

    async def async_close_shutter(self, shutter: ShutterConfig) -> None:
        """Drive the shutter down. Active while down_output > 0."""
        await self._send_state(shutter.module, shutter.down_output, 1)
        self._apply_local((shutter.module, shutter.down_output), 1)
        self._apply_local((shutter.module, shutter.up_output), 0)

    async def async_stop_shutter(self, shutter: ShutterConfig) -> None:
        """Stop the shutter. Protocol stops by clearing the up_output."""
        await self._send_state(shutter.module, shutter.up_output, 0)
        self._apply_local((shutter.module, shutter.up_output), 0)
        self._apply_local((shutter.module, shutter.down_output), 0)

    async def async_request_dump(self) -> None:
        """Send a state-dump request to the bus (triggers a full status refresh)."""
        await self._send_frame(*DUMP_REQUEST_FRAME)

    async def async_refresh_and_settle(
        self,
        idle: float = _DUMP_DRAIN_IDLE_S,
        timeout: float = DISCOVERY_TIMEOUT_S,
    ) -> None:
        """Refresh the state cache and wait for the resulting burst to settle.

        Sends a dump request, waits for the first response frame to hit the
        read loop, then drains until no frame has arrived for `idle` seconds.
        `timeout` is the overall ceiling.

        We watch _frame_arrival (fired in _ingest_message before the
        state-match filter) rather than the state-change listener chain:
        DOBISS often replies with the same states we already cache, so no
        listener would fire and the wait would sit idle the whole timeout.
        Using the raw arrival hook lets the refresh return as soon as the
        response actually starts and finish as soon as the burst settles.
        """
        if self._bus is None:
            return
        async with self._refresh_lock:
            await self._refresh_and_settle_locked(idle, timeout)

    async def _refresh_and_settle_locked(
        self, idle: float, timeout: float
    ) -> None:
        """Body of async_refresh_and_settle, run while the refresh lock is held."""
        if self._bus is None:
            return
        loop = asyncio.get_running_loop()
        arrival = asyncio.Event()
        self._frame_arrival = arrival
        try:
            await self._send_frame(*DUMP_REQUEST_FRAME)
            deadline = loop.time() + timeout
            try:
                await asyncio.wait_for(arrival.wait(), timeout=timeout)
            except TimeoutError:
                # No response at all within the deadline.  Give up and
                # let the read loop handle any late frames as usual.
                _LOGGER.warning(
                    "State refresh saw no response within %.1fs; state cache may be stale",
                    timeout,
                )
                return
            # Drain the rest of the burst by waiting for `idle` seconds of
            # silence.  Each new arrival re-sets the event and restarts the
            # window.
            while True:
                arrival.clear()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                try:
                    await asyncio.wait_for(
                        arrival.wait(), timeout=min(idle, remaining)
                    )
                except TimeoutError:
                    # Idle window elapsed without new frames -> burst is over.
                    return
        finally:
            self._frame_arrival = None

    async def _send_state(self, module: str, output: int, value: int) -> None:
        frame = build_state_frame(module, output, value)
        if frame is None:
            raise RuntimeError(f"Invalid output address {module}:{output}")
        await self._send_frame(*frame)

    async def _send_frame(self, can_id: int, data: bytes) -> None:
        import can  # noqa: PLC0415

        if self._bus is None:
            raise RuntimeError("Bus not connected")
        msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=True)
        await self.hass.async_add_executor_job(self._bus.send, msg)

    @callback
    def _apply_local(self, key: OutputKey, value: int) -> None:
        """Update local cache and fan out to listeners (optimistic)."""
        self.states[key] = value
        for listener in list(self._listeners):
            listener(key, value)

    async def _collect_initial_state(self) -> None:
        """Send a dump request and drain all echoed frames from the bus.

        The DOBISS controller streams each module's outputs in a tight burst
        after DUMP_REQUEST_FRAME.  We read until every configured module has
        been seen AND the bus has been quiet for _DUMP_DRAIN_IDLE_S, so the
        background read loop takes over a clean (empty) TCP receive buffer.

        Using a shared AsyncBufferedReader/Notifier (created in async_setup
        and stored on self) means:
        - no gap between discovery and the read loop (no frames dropped), and
        - notifier.stop() is never called here, so the event loop is never
          blocked by the thread-join that stop() performs.

        The 15-second hard deadline guards against unresponsive controllers.
        """
        if self._bus is None or self._reader is None:
            return
        configured_modules: set[str] = set(self.modules)
        await self._send_frame(*DUMP_REQUEST_FRAME)

        reader = self._reader
        loop = asyncio.get_running_loop()
        seen_modules: set[str] = set()
        deadline = loop.time() + DISCOVERY_TIMEOUT_S

        while True:
            # Once all modules are seen, switch to a short idle-drain window.
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            timeout = (
                _DUMP_DRAIN_IDLE_S
                if seen_modules >= configured_modules
                else remaining
            )
            try:
                msg = await asyncio.wait_for(
                    reader.get_message(), timeout=min(timeout, remaining)
                )
            except TimeoutError:
                # Idle timeout after all modules seen → burst is over.
                # Hard deadline hit → warn below and hand off anyway.
                break
            self._ingest_message(msg)
            parsed = parse_state_frame(bytes(msg.data))
            if parsed is not None and parsed.module in configured_modules:
                seen_modules.add(parsed.module)

        if seen_modules != configured_modules:
            missing = configured_modules - seen_modules
            _LOGGER.warning(
                "Discovery saw %d/%d modules (%d total outputs in state cache); "
                "missing modules: %s",
                len(seen_modules),
                len(configured_modules),
                len(self.states),
                sorted(missing),
            )

    @callback
    def _raise_repair_issue(self) -> None:
        """Create a repair issue and start a reauth flow when the CAN bus is persistently lost."""
        if self._repair_issue_active:
            return
        self._repair_issue_active = True

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            is_persistent=False,
            learn_more_url="https://github.com/DaanVervacke/hass-dobiss-sx-evolution",
            severity=IssueSeverity.ERROR,
            translation_key="cannot_connect",
            translation_placeholders=self.connection.repair_placeholders,
        )

        # Surface a Repair card that jumps straight into the reconfigure flow.
        # HA deduplicates in-progress reauth flows, so calling this when one is
        # already active is safe.
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            entry.async_start_reauth(self.hass)

    @callback
    def _clear_repair_issue(self) -> None:
        """Delete the repair issue once the CAN bus reconnects."""
        if not self._repair_issue_active:
            return
        self._repair_issue_active = False
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

    async def _read_loop(self) -> None:
        """Background loop: ingest frames, reconnect on failure.

        On any failure (reader exception or bus drop), close the bus,
        back off, re-open the bus, and re-request the state dump so any
        wall-switch presses that happened during the gap are caught up.
        A repair issue is raised the first time backoff reaches its maximum
        (persistent failure) and cleared upon successful reconnection.
        """
        backoff = RECONNECT_BACKOFF_INITIAL_S
        while True:
            try:
                await self._read_frames()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "CAN read loop failed - reconnecting in %.1fs", backoff,
                    exc_info=True,
                )

            await asyncio.sleep(backoff)
            try:
                await self._open_bus()
                # _open_bus cleared self._reader/_notifier via _teardown_notifier.
                # Re-create them now — before sending the dump — so that the
                # echoed dump frames are captured from the first byte.
                await self._setup_notifier()
                # Most recent state may have drifted while we were deaf;
                # the dump re-seeds the cache via the normal ingest path.
                await self._send_frame(*DUMP_REQUEST_FRAME)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                new_backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_S)
                _LOGGER.warning(
                    "CAN reconnect failed - retrying in %.1fs",
                    new_backoff,
                    exc_info=True,
                )
                if new_backoff >= RECONNECT_BACKOFF_MAX_S:
                    # Backoff has hit its ceiling - surface a repair issue so the
                    # user knows the connection is persistently unavailable.
                    self._raise_repair_issue()
                backoff = new_backoff
                continue

            self.reconnect_count += 1
            _LOGGER.info("CAN bus reconnected, state-dump requested")
            # Connection recovered - remove any outstanding repair issue.
            self._clear_repair_issue()
            backoff = RECONNECT_BACKOFF_INITIAL_S

    async def _read_frames(self) -> None:
        """Inner reader - drives ingest until something throws.

        On the first call after async_setup the shared reader/notifier
        created there are used directly, so no frames are lost between
        discovery and steady-state reading.

        On subsequent calls (reconnect path) _read_loop called _setup_notifier
        before this, so self._reader is already set.
        """
        if self._bus is None:
            raise RuntimeError("Bus not connected")

        # Reuse the shared reader/notifier if still alive (normal path: both
        # the first-run case where async_setup created them, and the reconnect
        # case where _read_loop called _setup_notifier before this).
        # As a defensive fallback, create them here if somehow absent.
        if self._reader is None or self._notifier is None:
            await self._setup_notifier()
        if self._reader is None:
            raise RuntimeError("Could not create reader — bus unavailable")
        reader = self._reader

        try:
            while True:
                msg = await reader.get_message()
                self._ingest_message(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            # On any non-cancel error, tear down the notifier so the
            # reconnect path in _read_loop gets a clean slate.
            await self._teardown_notifier()
            raise

    @callback
    def _ingest_message(self, msg: can.Message) -> None:
        """Parse a CAN message and update local state if it matches an output.

        The DOBISS controller broadcasts ALL state updates - both dump
        responses and wall-switch presses - on arbitration ID 0x1010000.
        State writes we send on 0x800002 may echo back via SocketCAN
        loopback, so we explicitly drop those.
        """
        if msg.arbitration_id == CAN_ID_TX_STATE:
            return
        update = parse_state_frame(bytes(msg.data))
        if update is None or update.module not in self.modules:
            return
        # Signal the frame arrival BEFORE the state-match filter so a refresh
        # waiter can settle even when the fresh dump matches the cache.
        if self._frame_arrival is not None:
            self._frame_arrival.set()
        key = (update.module, update.output)
        if self.states.get(key) == update.state:
            return
        self._apply_local(key, update.state)
