"""Tests for the Max200 serial client."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import serial

from custom_components.dobiss_sx_evolution.const import (
    SERIAL_HANDSHAKE_RETRIES,
    SERIAL_RETRY_DELAY_S,
)
from custom_components.dobiss_sx_evolution.protocol import to_bcd
from custom_components.dobiss_sx_evolution.serial_client import Max200SerialClient


def _mock_port(
    echo_byte: int | None = None,
    read_data: bytes = b"",
    retries: int = 1,
):
    """Create a mock serial.Serial with configurable echo and read data.

    Each handshake attempt reads twice: once for the ready byte (discarded),
    once for the echo. The ready byte is always b"M".
    """
    port = MagicMock()
    reads: list[bytes] = []
    for _ in range(retries):
        reads.append(b"M")
        if echo_byte is not None:
            reads.append(bytes([echo_byte]))
        else:
            reads.append(b"")
    if read_data:
        reads.append(read_data)
    port.read = MagicMock(side_effect=reads)
    port.write = MagicMock()
    port.close = MagicMock()
    port.reset_input_buffer = MagicMock()
    return port


def test_device_property():
    client = Max200SerialClient("/dev/ttyUSB1")
    assert client.device == "/dev/ttyUSB1"


@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_sync_clock_sends_bcd_bytes(mock_serial_cls):
    dt = datetime(2026, 7, 17, 14, 30, 45)
    port = _mock_port(echo_byte=ord("K"))
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    client.sync_clock(dt)

    port.write.assert_any_call(b"K0")
    bcd_call = port.write.call_args_list[-1]
    data = bcd_call[0][0]
    assert data[0] == to_bcd(45)
    assert data[1] == to_bcd(30)
    assert data[2] == to_bcd(14)
    assert data[3] == to_bcd(dt.isoweekday())
    assert data[4] == to_bcd(17)
    assert data[5] == to_bcd(7)
    assert data[6] == to_bcd(26)
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_sync_clock_handshake_mismatch_raises(mock_serial_cls, _mock_sleep):
    port = _mock_port(echo_byte=0xFF, retries=3)
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError, match="handshake mismatch"):
        client.sync_clock(datetime(2026, 1, 1))

    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_handshake_timeout_raises(mock_serial_cls, _mock_sleep):
    port = _mock_port(retries=3)
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError, match="no response"):
        client.sync_clock(datetime(2026, 1, 1))

    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_serial_exception_wrapped_in_connection_error(mock_serial_cls, _mock_sleep):
    port = MagicMock()
    port.write = MagicMock(side_effect=serial.SerialException("device gone"))
    port.close = MagicMock()
    port.reset_input_buffer = MagicMock()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError, match="device gone"):
        client.sync_clock(datetime(2026, 1, 1))

    port.close.assert_called_once()
    # Sleeps happen between attempts only, never after the last exhausted one.
    assert _mock_sleep.call_count == SERIAL_HANDSHAKE_RETRIES - 1
    for call in _mock_sleep.call_args_list:
        assert call.args == (SERIAL_RETRY_DELAY_S,)


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_handshake_serial_exception_then_succeeds(mock_serial_cls, _mock_sleep):
    """First handshake attempt raises SerialException; second attempt succeeds."""
    port = MagicMock()
    port.read = MagicMock(side_effect=[b"M", b"M", bytes([ord("K")])])
    port.write = MagicMock(
        side_effect=[serial.SerialException("transient"), None, None]
    )
    port.reset_input_buffer = MagicMock()
    port.close = MagicMock()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    client.sync_clock(datetime(2026, 1, 1))

    retry_sleeps = [
        call
        for call in _mock_sleep.call_args_list
        if call.args == (SERIAL_RETRY_DELAY_S,)
    ]
    assert len(retry_sleeps) == 1
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_handshake_retries_then_succeeds(mock_serial_cls, _mock_sleep):
    """Handshake succeeds on the third attempt after two mismatches."""
    port = MagicMock()
    port.read = MagicMock(
        side_effect=[
            b"M",
            bytes([0xFF]),
            b"M",
            bytes([0x00]),
            b"M",
            bytes([ord("K")]),
        ]
    )
    port.reset_input_buffer = MagicMock()
    port.close = MagicMock()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    client.sync_clock(datetime(2026, 1, 1))

    assert port.read.call_count == 6
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_open_failure_raises_connection_error(mock_serial_cls):
    mock_serial_cls.side_effect = serial.SerialException("no such port")

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError, match="no such port"):
        client.sync_clock(datetime(2026, 1, 1))


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_download_config(mock_serial_cls, _mock_sleep):
    config_data = bytearray(36)
    config_data[0] = ord("A")
    config_data[2] = ord("C")

    port = _mock_port(echo_byte=ord("a"), read_data=bytes(config_data))
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    result = client.download_config()

    assert result == [("A", 0), ("C", 2)]
    port.write.assert_any_call(b"a0")
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_download_output_name(mock_serial_cls, _mock_sleep):
    name_data = bytearray(32)
    name_bytes = b"Kitchen ceiling"
    name_data[: len(name_bytes)] = name_bytes

    port = _mock_port(echo_byte=ord("u"), read_data=bytes(name_data))
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    result = client.download_output_name(0, 0)

    assert result == "Kitchen ceiling"
    port.write.assert_any_call(b"u1")
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_download_output_name_eeprom_address(mock_serial_cls, _mock_sleep):
    """Verify the EEPROM address bytes sent in WriteControlByte."""
    name_data = bytearray(32)
    name_data[:4] = b"Test"
    port = _mock_port(echo_byte=ord("u"), read_data=bytes(name_data))
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    client.download_output_name(2, 5)

    addr = 0x8000 + 2 * 384 + 5 * 32
    base_call = port.write.call_args_list[1]
    assert base_call[0][0] == bytes([0xA0])
    addr_call = port.write.call_args_list[2]
    assert addr_call[0][0][0] == addr >> 8
    assert addr_call[0][0][1] == addr & 0xFF
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_download_module_output_names(mock_serial_cls, _mock_sleep):
    """Batch download reads all outputs in one connection."""
    name0 = bytearray(32)
    name0[:4] = b"Lamp"
    name1 = bytearray(32)
    name2 = bytearray(32)
    name2[:6] = b"Switch"

    port = MagicMock()
    port.read = MagicMock(
        side_effect=[
            b"M",
            bytes([ord("u")]),
            bytes(name0),
            bytes(name1),
            bytes(name2),
        ]
    )
    port.reset_input_buffer = MagicMock()
    port.close = MagicMock()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    result = client.download_module_output_names(0, 3)

    assert result == {0: "Lamp", 2: "Switch"}
    port.write.assert_any_call(b"u1")
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_batch_download_port_closed_on_read_failure(mock_serial_cls, _mock_sleep):
    """Port is closed even when a read fails mid-batch."""
    name0 = bytearray(32)
    name0[:4] = b"Lamp"

    port = MagicMock()
    port.read = MagicMock(
        side_effect=[
            b"M",
            bytes([ord("u")]),
            bytes(name0),
            serial.SerialException("read failed"),
        ]
    )
    port.reset_input_buffer = MagicMock()
    port.close = MagicMock()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(serial.SerialException, match="read failed"):
        client.download_module_output_names(0, 12)

    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_port_closed_on_exception(mock_serial_cls, _mock_sleep):
    """Port is closed even when download_config raises during handshake."""
    port = _mock_port(retries=3)
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError):
        client.download_config()

    port.close.assert_called_once()
