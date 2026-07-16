"""Tests for the sensor platform of DOBISS SX Evolution."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
)

from .conftest import MOCK_CONFIG


async def test_reconnect_count_sensor(hass: HomeAssistant, mock_controller) -> None:
    """Sensor must reflect controller.reconnect_count."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.max200_can_bus_reconnections")
    assert state is not None
    assert state.state == "0"

    mock_controller.reconnect_count = 3
    coordinator = entry.runtime_data
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.max200_can_bus_reconnections")
    assert state.state == "3"
