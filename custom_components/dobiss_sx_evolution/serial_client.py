"""Serial client for the Max200 master module (RS-232 via SX-kabel).

Implements the MaxTool serial protocol: 2-char command handshake +
WriteControlByte EEPROM addressing. All methods are blocking and must
be called via hass.async_add_executor_job().
"""

from __future__ import annotations

import time
from datetime import datetime

import serial

from .const import (
    MASTER_SERIAL_BAUDRATE,
    SERIAL_DELAY_AFTER_ADDR_S,
    SERIAL_DELAY_AFTER_BASE_S,
    SERIAL_HANDSHAKE_RETRIES,
    SERIAL_RETRY_DELAY_S,
    SERIAL_SETTLE_BEFORE_OPEN_S,
)
from .protocol import (
    CONFIG_RESPONSE_SIZE,
    EEPROM_BASE_BYTE,
    EEPROM_READ_DIRECTION,
    EEPROM_READ_RECORD_SIZE,
    OUTPUT_NAME_RESPONSE_SIZE,
    output_name_eeprom_addr,
    parse_config_response,
    parse_output_name,
    to_bcd,
)


class Max200SerialClient:
    """Ephemeral serial client for Max200 configuration commands."""

    def __init__(self, device: str, baudrate: int = MASTER_SERIAL_BAUDRATE) -> None:
        self._device = device
        self._baudrate = baudrate

    @property
    def device(self) -> str:
        return self._device

    def _open(self) -> serial.Serial:
        try:
            return serial.Serial(self._device, self._baudrate, timeout=2.0)
        except serial.SerialException as err:
            raise ConnectionError(f"Port {self._device}: {err}") from err

    def _handshake(self, port: serial.Serial, command: str) -> None:
        """Drain the ready byte, send 2-char command, verify first char echo.

        The Max200 sends a ready byte when the port opens. We must read
        (and discard) it before writing the command. After writing, the
        Max200 echoes the first character as a lock confirmation.
        Retries on mismatch since the Max200 can be slow to respond.
        """
        last_err: Exception | None = None
        for attempt in range(SERIAL_HANDSHAKE_RETRIES):
            try:
                port.reset_input_buffer()
                port.read(1)
                port.write(command.encode("ascii"))
                echo = port.read(1)
                if not echo:
                    last_err = ConnectionError(
                        f"Port {self._device}: no response within timeout"
                    )
                elif echo[0] != ord(command[0]):
                    last_err = ConnectionError(
                        f"Port {self._device}: handshake mismatch for "
                        f"{command!r}, expected {command[0]!r} got {echo[0]:#x}"
                    )
                else:
                    return
            except serial.SerialException as err:
                last_err = ConnectionError(f"Port {self._device}: {err}")
            if attempt < SERIAL_HANDSHAKE_RETRIES - 1:
                time.sleep(SERIAL_RETRY_DELAY_S)
        raise last_err  # type: ignore[misc]

    def _write_control_byte(
        self,
        port: serial.Serial,
        base: int,
        addr_hi: int,
        addr_lo: int,
        record_size: int,
        direction: int,
    ) -> None:
        """Send 5-byte EEPROM addressing window."""
        port.write(bytes([base]))
        time.sleep(SERIAL_DELAY_AFTER_BASE_S)
        port.write(bytes([addr_hi, addr_lo, record_size, direction]))
        time.sleep(SERIAL_DELAY_AFTER_ADDR_S)

    def sync_clock(self, dt: datetime) -> None:
        """K0 clock set. Blocking."""
        port = self._open()
        try:
            self._handshake(port, "K0")
            port.write(
                bytes(
                    [
                        to_bcd(dt.second),
                        to_bcd(dt.minute),
                        to_bcd(dt.hour),
                        to_bcd(dt.isoweekday()),
                        to_bcd(dt.day),
                        to_bcd(dt.month),
                        to_bcd(dt.year % 100),
                    ]
                )
            )
        finally:
            port.close()

    def download_config(self) -> list[tuple[str, int]]:
        """a0 config download. Blocking."""
        time.sleep(SERIAL_SETTLE_BEFORE_OPEN_S)
        port = self._open()
        try:
            self._handshake(port, "a0")
            self._write_control_byte(
                port,
                EEPROM_BASE_BYTE,
                0,
                0,
                EEPROM_READ_RECORD_SIZE,
                EEPROM_READ_DIRECTION,
            )
            data = port.read(CONFIG_RESPONSE_SIZE)
            return parse_config_response(data)
        finally:
            port.close()

    def download_module_output_names(
        self, module_index: int, count: int
    ) -> dict[int, str]:
        """u1 batch output name download. Single connection for all outputs.

        Returns {output_index: name} for outputs that have a non-empty name.
        """
        time.sleep(SERIAL_SETTLE_BEFORE_OPEN_S)
        port = self._open()
        try:
            self._handshake(port, "u1")
            names: dict[int, str] = {}
            for output_index in range(count):
                addr = output_name_eeprom_addr(module_index, output_index)
                self._write_control_byte(
                    port,
                    EEPROM_BASE_BYTE,
                    addr >> 8,
                    addr & 0xFF,
                    EEPROM_READ_RECORD_SIZE,
                    EEPROM_READ_DIRECTION,
                )
                data = port.read(OUTPUT_NAME_RESPONSE_SIZE)
                name = parse_output_name(data)
                if name is not None:
                    names[output_index] = name
            return names
        finally:
            port.close()
