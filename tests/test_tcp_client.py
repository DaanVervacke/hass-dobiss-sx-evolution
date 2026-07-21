"""Tests for the Max200 TCP client."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.dobiss_sx_evolution.tcp_client import Max200TcpClient


def _mock_writer():
    """Create a mock StreamWriter."""
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


def _mock_reader(data: bytes = b""):
    """Create a mock StreamReader."""
    reader = MagicMock()
    reader.readexactly = AsyncMock(return_value=data)
    return reader


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_send_command_sends_intro_and_output(mock_open) -> None:
    """send_command writes intro + output bytes to the socket."""
    writer = _mock_writer()
    mock_open.return_value = (_mock_reader(), writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    intro = bytes(16)
    output = b"\x01\x02\x03"
    await client.send_command(intro, output)

    calls = writer.write.call_args_list
    assert calls[0][0][0] == intro
    assert calls[1][0][0] == output
    writer.drain.assert_awaited_once()
    writer.close.assert_called_once()


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_send_command_intro_only(mock_open) -> None:
    """send_command with no output sends only intro."""
    writer = _mock_writer()
    mock_open.return_value = (_mock_reader(), writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    intro = b"\xed" + bytes(15)
    await client.send_command(intro)

    assert writer.write.call_count == 1
    assert writer.write.call_args[0][0] == intro


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_send_command_connect_failure_logs_warning(mock_open, caplog) -> None:
    """Connection failure logs a warning, does not raise."""
    mock_open.side_effect = OSError("Connection refused")

    client = Max200TcpClient("10.0.0.1", 1001)
    await client.send_command(bytes(16))

    assert "Max200 TCP connect failed" in caplog.text


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_send_and_receive(mock_open) -> None:
    """send_and_receive reads back the expected response."""
    writer = _mock_writer()
    reader = _mock_reader(b"\xaa")
    mock_open.return_value = (reader, writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    response = await client.send_and_receive(bytes(16), response_size=1)

    assert response == b"\xaa"
    reader.readexactly.assert_awaited_once_with(1)
    writer.close.assert_called_once()


def test_host_property() -> None:
    """host property returns the configured host."""
    client = Max200TcpClient("10.0.0.1", 1001)
    assert client.host == "10.0.0.1"


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_sync_clock_sends_clock_packets(mock_open) -> None:
    """sync_clock sends intro + BCD time bytes."""
    writer = _mock_writer()
    mock_open.return_value = (_mock_reader(), writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    dt = datetime(2026, 7, 21, 14, 30, 45)
    await client.sync_clock(dt)

    calls = writer.write.call_args_list
    intro = calls[0][0][0]
    assert intro[0] == 0xED
    assert intro[1] == 0x4B  # 'K'
    assert intro[2] == 0x30  # '0'
    output = calls[1][0][0]
    assert len(output) == 7


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_download_config_returns_parsed_modules(mock_open) -> None:
    """download_config sends intro and parses the response."""
    response = bytearray(36)
    response[0] = ord("A")
    response[2] = ord("C")
    writer = _mock_writer()
    mock_open.return_value = (_mock_reader(bytes(response)), writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    result = await client.download_config()

    assert ("A", 0) in result
    assert ("C", 2) in result
    writer.write.assert_called_once()  # intro only, no output


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_download_output_name_returns_parsed_name(mock_open) -> None:
    """download_output_name sends intro and parses the response."""
    response = bytearray(32)
    response[0:7] = b"Kitchen"
    response[1] = ord("i")  # byte 1 != 0xFF means configured
    writer = _mock_writer()
    mock_open.return_value = (_mock_reader(bytes(response)), writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    result = await client.download_output_name(0, 0)

    assert result is not None
    assert "Kitchen" in result or "itchen" in result


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_download_output_name_unconfigured(mock_open) -> None:
    """download_output_name returns None for unconfigured output."""
    response = bytearray(32)
    response[1] = 0xFF
    writer = _mock_writer()
    mock_open.return_value = (_mock_reader(bytes(response)), writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    result = await client.download_output_name(0, 0)

    assert result is None


@patch("custom_components.dobiss_sx_evolution.tcp_client.asyncio.open_connection")
async def test_send_command_writes_empty_bytes(mock_open) -> None:
    """send_command with output=b'' must still write the empty bytes."""
    writer = _mock_writer()
    mock_open.return_value = (_mock_reader(), writer)

    client = Max200TcpClient("10.0.0.1", 1001)
    await client.send_command(bytes(16), output=b"")

    assert writer.write.call_count == 2
    assert writer.write.call_args_list[1][0][0] == b""


async def test_download_module_output_names_delegates_per_record() -> None:
    """download_module_output_names delegates to download_output_name per record."""
    names = {0: "Kitchen", 1: "Living", 2: "Bedroom"}

    async def fake_download(module_index: int, output_index: int) -> str | None:
        assert module_index == 0
        return names.get(output_index)

    client = Max200TcpClient("10.0.0.1", 1001)
    with patch.object(
        client, "download_output_name", AsyncMock(side_effect=fake_download)
    ) as mock_download:
        result = await client.download_module_output_names(0, 3)

    assert result == names
    assert mock_download.await_count == 3


async def test_download_module_output_names_omits_unconfigured() -> None:
    """A record with byte 1 == 0xFF (unconfigured) is omitted from the result."""

    async def fake_download(module_index: int, output_index: int) -> str | None:
        if output_index == 1:
            return None
        return f"Output {output_index}"

    client = Max200TcpClient("10.0.0.1", 1001)
    with patch.object(
        client, "download_output_name", AsyncMock(side_effect=fake_download)
    ):
        result = await client.download_module_output_names(0, 3)

    assert 1 not in result
    assert result == {0: "Output 0", 2: "Output 2"}


async def test_download_module_output_names_propagates_connection_error() -> None:
    """A connection error mid-batch propagates to the caller."""
    client = Max200TcpClient("10.0.0.1", 1001)
    with (
        patch.object(
            client,
            "download_output_name",
            AsyncMock(side_effect=ConnectionError("boom")),
        ),
        pytest.raises(ConnectionError, match="boom"),
    ):
        await client.download_module_output_names(0, 3)
