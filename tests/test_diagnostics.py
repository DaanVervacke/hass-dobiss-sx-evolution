"""Tests for the dobiss_sx_evolution diagnostics module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import DOMAIN
from custom_components.dobiss_sx_evolution.controller import UsbConnection
from custom_components.dobiss_sx_evolution.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_init import _make_entry_data


async def test_diagnostics_socketcand_redacts_host(
    hass: HomeAssistant, mock_controller
) -> None:
    """Socketcand connections redact the host but keep other connection fields."""
    mock_controller.switches = [("A", 5)]

    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    conn = mock_controller.connection
    connection = diagnostics["connection"]
    assert connection["type"] == "socketcand"
    assert connection["host"] == REDACTED
    assert connection["port"] == conn.port
    assert connection["can_interface"] == conn.interface
    assert diagnostics["controller"]["switches"] == [["A", 5]]


async def test_diagnostics_usb_redacts_device(
    hass: HomeAssistant, mock_controller
) -> None:
    """USB connections redact the device path but keep other connection fields."""
    mock_controller.connection = UsbConnection(
        device="/dev/serial/by-id/usb-Some_Vendor_CAN-if00",
        baudrate=250000,
        can_interface="slcan0",
    )

    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    conn = mock_controller.connection
    connection = diagnostics["connection"]
    assert connection["type"] == "usb"
    assert connection["device"] == REDACTED
    assert connection["baudrate"] == conn.baudrate
    assert connection["can_interface"] == conn.can_interface


async def test_diagnostics_redacts_master_device(
    hass: HomeAssistant, mock_controller
) -> None:
    """master_device is redacted in diagnostics output."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(master_device="/dev/ttyUSB1"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["connection"]["master_device"] == REDACTED


async def test_diagnostics_redacts_max200_host(
    hass: HomeAssistant, mock_controller
) -> None:
    """max200_host is redacted in diagnostics output."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(max200_host="10.0.0.2"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient",
    ) as mock_tcp_cls:
        mock_tcp_cls.return_value.sync_clock = AsyncMock()

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["connection"]["max200_host"] == REDACTED
