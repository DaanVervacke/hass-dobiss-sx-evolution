"""TCP client for the Max200 controller (port 1001).

Handles the 16-byte intro + variable-length output framing shared by all
Max200 TCP commands. Individual commands build their own intro/output bytes
via protocol.py, then call send_command() or send_and_receive().
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .const import MAX200_TCP_PORT
from .protocol import (
    CONFIG_RESPONSE_SIZE,
    OUTPUT_NAME_RESPONSE_SIZE,
    build_clock_set_packets,
    build_config_download_intro,
    build_output_name_intro,
    parse_config_response,
    parse_output_name,
)

_LOGGER = logging.getLogger(__name__)

# MaxTool's LAN config download uses a 30s Receive() timeout for the 36-byte
# ConfigVars response. 5s has been fine in practice for the small payloads
# this client sends (config download, output names, clock set), but if slow
# Max200 controllers start timing out, raise this or add a per-call override.
CONNECT_TIMEOUT_S = 5.0


class Max200TcpClient:
    """Ephemeral TCP client for Max200 configuration commands."""

    def __init__(self, host: str, port: int = MAX200_TCP_PORT) -> None:
        self._host = host
        self._port = port

    @property
    def host(self) -> str:
        return self._host

    async def send_command(self, intro: bytes, output: bytes | None = None) -> None:
        """Fire-and-forget: send intro + optional output, then close."""
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT_S,
            )
        except (TimeoutError, OSError) as err:
            _LOGGER.warning(
                "Max200 TCP connect failed (%s:%s): %s",
                self._host,
                self._port,
                err,
            )
            return

        try:
            writer.write(intro)
            if output is not None:
                writer.write(output)
            await writer.drain()
        except OSError as err:
            _LOGGER.warning("Max200 TCP send failed: %s", err)
        finally:
            writer.close()
            await writer.wait_closed()

    async def send_and_receive(
        self,
        intro: bytes,
        output: bytes | None = None,
        response_size: int = 1,
    ) -> bytes:
        """Send intro + optional output, read response_size bytes back."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port),
            timeout=CONNECT_TIMEOUT_S,
        )

        try:
            writer.write(intro)
            if output is not None:
                writer.write(output)
            await writer.drain()

            return await asyncio.wait_for(
                reader.readexactly(response_size),
                timeout=CONNECT_TIMEOUT_S,
            )
        finally:
            writer.close()
            await writer.wait_closed()

    async def sync_clock(self, dt: datetime) -> None:
        """K0 clock set over TCP."""
        intro, output = build_clock_set_packets(dt)
        await self.send_command(intro, output)

    async def download_config(self) -> list[tuple[str, int]]:
        """a0 config download over TCP."""
        intro = build_config_download_intro()
        data = await self.send_and_receive(intro, response_size=CONFIG_RESPONSE_SIZE)
        return parse_config_response(data)

    async def download_output_name(
        self, module_index: int, output_index: int
    ) -> str | None:
        """u1 output name download over TCP."""
        intro = build_output_name_intro(module_index, output_index)
        data = await self.send_and_receive(
            intro, response_size=OUTPUT_NAME_RESPONSE_SIZE
        )
        return parse_output_name(data)
