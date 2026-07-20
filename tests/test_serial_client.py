"""Tests for the Max200 serial client."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import serial

from custom_components.dobiss_sx_evolution.protocol import to_bcd
from custom_components.dobiss_sx_evolution.serial_client import Max200SerialClient


def _mock_port(echo_byte: int | None = None, read_data: bytes = b""):
    """Create a mock serial.Serial with configurable echo and read data."""
    port = MagicMock()
    reads = []
    if echo_byte is not None:
        reads.append(bytes([echo_byte]))
    if read_data:
        reads.append(read_data)
    port.read = MagicMock(side_effect=reads if reads else [b""])
    port.write = MagicMock()
    port.close = MagicMock()
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


@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_sync_clock_handshake_mismatch_raises(mock_serial_cls):
    port = _mock_port(echo_byte=0xFF)
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError, match="handshake mismatch"):
        client.sync_clock(datetime(2026, 1, 1))

    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_handshake_timeout_raises(mock_serial_cls):
    port = _mock_port()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError, match="no response"):
        client.sync_clock(datetime(2026, 1, 1))

    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_serial_exception_wrapped_in_connection_error(mock_serial_cls):
    port = MagicMock()
    port.write = MagicMock(side_effect=serial.SerialException("device gone"))
    port.close = MagicMock()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError, match="device gone"):
        client.sync_clock(datetime(2026, 1, 1))

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

    addr = 128 + 2 * 384 + 5 * 32
    base_call = port.write.call_args_list[1]
    assert base_call[0][0] == bytes([0xA0])
    addr_call = port.write.call_args_list[2]
    assert addr_call[0][0][0] == addr >> 8
    assert addr_call[0][0][1] == addr & 0xFF
    port.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.serial_client.time.sleep")
@patch("custom_components.dobiss_sx_evolution.serial_client.serial.Serial")
def test_port_closed_on_exception(mock_serial_cls, _mock_sleep):
    """Port is closed even when download_config raises during handshake."""
    port = _mock_port()
    mock_serial_cls.return_value = port

    client = Max200SerialClient("/dev/ttyUSB1")
    with pytest.raises(ConnectionError):
        client.download_config()

    port.close.assert_called_once()
