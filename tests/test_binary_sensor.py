"""Tests for the binary_sensor platform of DOBISS SX Evolution."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
)

from .conftest import MOCK_CONFIG


async def test_bus_connected_binary_sensor(
    hass: HomeAssistant, mock_controller
) -> None:
    """Binary sensor must reflect controller.is_bus_connected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.max200_can_bus_connected")
    assert state is not None
    assert state.state == "on"

    mock_controller.is_bus_connected = False
    coordinator = entry.runtime_data
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    state = hass.states.get("binary_sensor.max200_can_bus_connected")
    assert state is not None
    assert state.state == "off"
