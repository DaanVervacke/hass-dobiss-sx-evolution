"""DOBISS controller - owns the CAN bus connection and dispatches updates."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity

from .const import (
    CAN_ID_TX_STATE,
    CONNECTION_TYPE_SOCKETCAND,
    DEFAULT_BAUDRATE,
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


def make_bus_sync(host: str, port: int, interface: str) -> can.BusABC:
    """Open and return a python-can socketcand Bus.

    Must be called from an executor thread.
    """
    import can  # noqa: PLC0415

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(_TCP_PRECHECK_TIMEOUT_S)
        probe.connect((host, port))

    return can.Bus(
        interface="socketcand",
        channel=interface,
        host=host,
        port=port,
    )


def make_bus_usb_sync(device: str, baudrate: int, interface: str) -> can.BusABC:
    """Open and return a python-can USB Bus.

    Must be called from an executor thread. Raises on any failure.

    Args:
        device: Serial port device path (e.g., /dev/ttyACM0, COM3)
        baudrate: Baud rate for serial connection
        interface: python-can interface type (slcan, serial, etc.)
    """
    import can  # noqa: PLC0415

    kwargs = {"tty_baudrate": baudrate} if interface == "slcan" else {"baudrate": baudrate}
    return can.Bus(interface=interface, channel=device, **kwargs)


class DobissController:
    """Owns the CAN bus, runs the read loop, and applies state writes."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection_type: str,
        lights: list[OutputKey],
        dimmers: list[OutputKey],
        shutters: list[ShutterConfig],
        entry_id: str = "",
        host: str | None = None,
        port: int | None = None,
        interface: str | None = None,
        device: str | None = None,
        baudrate: int | None = None,
        can_interface: str | None = None,
    ) -> None:
        """Initialize the controller.

        Args:
            connection_type: Either CONNECTION_TYPE_SOCKETCAND or CONNECTION_TYPE_USB
            host: TCP host for socketcand
            port: TCP port for socketcand
            interface: CAN interface name for socketcand
            device: Serial device path for USB (e.g., /dev/ttyACM0)
            baudrate: Baud rate for USB connection
            can_interface: python-can interface type (slcan, serial, etc.)
        """
        self.hass = hass
        self.connection_type = connection_type
        self.host = host
        self.port = port
        self.interface = interface
        self.device = device
        self.baudrate = baudrate
        self.can_interface = can_interface
        self.lights = lights
        self.dimmers = dimmers
        self.shutters = shutters
        self.modules: list[str] = sorted(
            {m for m, _ in (*lights, *dimmers)} | {s.module for s in shutters}
        )
        self.states: dict[OutputKey, int] = {}
        self.reconnect_count: int = 0
        self._issue_id: str = f"cannot_connect_{entry_id}"
        self._bus: can.BusABC | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._listeners: list[Callable[[OutputKey, int], None]] = []
        self._repair_issue_active: bool = False

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
        """Open the bus, request a state dump, then start the read loop."""
        await self._open_bus()
        await self._collect_initial_state()
        self._reader_task = self.hass.async_create_background_task(
            self._read_loop(), f"dobiss_sx_evolution[{self.interface}]"
        )

    async def _open_bus(self) -> None:
        """(Re-)open the CAN bus, closing the old one if any."""
        if self._bus is not None:
            old = self._bus
            self._bus = None
            try:
                await self.hass.async_add_executor_job(old.shutdown)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Error closing stale bus", exc_info=True)

        if self.connection_type == CONNECTION_TYPE_SOCKETCAND:
            self._bus = await self.hass.async_add_executor_job(
                make_bus_sync,
                self.host or "",
                self.port or 0,
                self.interface or "",
            )
        else:  # CONNECTION_TYPE_USB
            self._bus = await self.hass.async_add_executor_job(
                make_bus_usb_sync,
                self.device or "",
                self.baudrate or DEFAULT_BAUDRATE,
                self.can_interface or "slcan",
            )

    async def async_shutdown(self) -> None:
        """Cancel the reader and close the bus."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
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
        """Send a dump request and collect echoes until every configured module
        has been heard from at least once, or DISCOVERY_TIMEOUT_S elapses.

        The DOBISS controller streams each module's outputs in a tight burst.
        Once the first frame from module X arrives the rest of X's burst follows
        within milliseconds, so waiting for a frame-per-output count is
        unnecessarily slow.  We exit as soon as every module is represented and
        let the background read loop absorb any trailing frames.

        The 15-second deadline remains as a hard ceiling for unresponsive modules.
        """
        import can  # noqa: PLC0415

        if self._bus is None:
            return
        configured_modules: set[str] = set(self.modules)
        await self._send_frame(*DUMP_REQUEST_FRAME)

        loop = asyncio.get_running_loop()
        reader = can.AsyncBufferedReader()
        notifier = can.Notifier(self._bus, [reader], loop=loop)
        seen_modules: set[str] = set()
        try:
            deadline = loop.time() + DISCOVERY_TIMEOUT_S
            while seen_modules != configured_modules:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(
                        reader.get_message(), timeout=remaining
                    )
                except TimeoutError:
                    break
                self._ingest_message(msg)
                # Track which configured modules we have seen at least one frame from.
                parsed = parse_state_frame(bytes(msg.data))
                if parsed is not None and parsed.module in configured_modules:
                    seen_modules.add(parsed.module)
        finally:
            notifier.stop()

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
        """Create a repair issue when the CAN bus connection is persistently lost."""
        if self._repair_issue_active:
            return
        self._repair_issue_active = True
        # Build placeholders based on connection type
        placeholders: dict[str, str] = {}
        if self.connection_type == CONNECTION_TYPE_SOCKETCAND:
            if self.host:
                placeholders["host"] = self.host
            if self.interface:
                placeholders["interface"] = self.interface
        else:
            if self.device:
                placeholders["device"] = self.device

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            is_persistent=False,
            learn_more_url="https://github.com/DaanVervacke/hass-dobiss-sx-evolution",
            severity=IssueSeverity.ERROR,
            translation_key="cannot_connect",
            translation_placeholders=placeholders,
        )

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
        """Inner reader - drives ingest until something throws."""
        import can  # noqa: PLC0415

        if self._bus is None:
            raise RuntimeError("Bus not connected")
        reader = can.AsyncBufferedReader()
        # Pass loop=running_loop so the Notifier dispatches listener
        # callbacks via the event loop instead of a worker thread.
        # Without this, AsyncBufferedReader.put_nowait() runs off-loop
        # and frame delivery to `await reader.get_message()` lags by
        # whole seconds at a time.
        notifier = can.Notifier(
            self._bus,
            [reader],
            timeout=0.1,
            loop=asyncio.get_running_loop(),
        )
        try:
            while True:
                msg = await reader.get_message()
                self._ingest_message(msg)
        finally:
            notifier.stop()

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
        key = (update.module, update.output)
        if self.states.get(key) == update.state:
            return
        self._apply_local(key, update.state)
