"""Tests for the dobiss_sx_evolution diagnostics module."""
from __future__ import annotations

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import CONNECTION_TYPE_USB, DOMAIN
from custom_components.dobiss_sx_evolution.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_init import _make_entry_data


async def test_diagnostics_socketcand_redacts_host(
    hass: HomeAssistant, mock_controller
) -> None:
    """Socketcand connections redact the host but keep other connection fields."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    connection = diagnostics["connection"]
    assert connection["type"] == "socketcand"
    assert connection["host"] == REDACTED
    assert connection["port"] == mock_controller.port
    assert connection["can_interface"] == mock_controller.interface


async def test_diagnostics_usb_redacts_device(
    hass: HomeAssistant, mock_controller
) -> None:
    """USB connections redact the device path but keep other connection fields."""
    mock_controller.connection_type = CONNECTION_TYPE_USB
    mock_controller.host = None
    mock_controller.port = None
    mock_controller.interface = None
    mock_controller.device = "/dev/serial/by-id/usb-Some_Vendor_CAN-if00"
    mock_controller.baudrate = 250000
    mock_controller.can_interface = "slcan0"

    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    connection = diagnostics["connection"]
    assert connection["type"] == "usb"
    assert connection["device"] == REDACTED
    assert connection["baudrate"] == mock_controller.baudrate
    assert connection["can_interface"] == mock_controller.can_interface
