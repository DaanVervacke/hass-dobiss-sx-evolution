"""Tests for the Max200 TCP client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
