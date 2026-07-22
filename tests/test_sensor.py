"""Tests for the sensor platform of DOBISS SX Evolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
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


async def test_last_clock_sync_sensor_not_created_without_link(
    hass: HomeAssistant, mock_controller
) -> None:
    """The last-clock-sync sensor must not exist when no Max200 link is configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.max200_last_clock_sync") is None


async def test_last_clock_sync_sensor_created_and_tracks_coordinator(
    hass: HomeAssistant, mock_controller
) -> None:
    """The last-clock-sync sensor is created when serial is configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_SOCKETCAND,
            "master_device": "/dev/ttyUSB1",
            **MOCK_CONFIG,
        },
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200SerialClient"
    ) as mock_serial_cls:
        mock_serial = mock_serial_cls.return_value
        mock_serial.device = "/dev/ttyUSB1"
        mock_serial.sync_clock = MagicMock()

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.max200_last_clock_sync")
    assert state is not None
    assert state.state != "unknown"

    coordinator = entry.runtime_data
    fixed_now = dt_util.now()
    coordinator.last_clock_sync = fixed_now
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.max200_last_clock_sync")
    parsed = dt_util.parse_datetime(state.state)
    assert parsed is not None
    assert parsed.tzinfo is not None
    # HA's TIMESTAMP device class truncates the state string to whole seconds.
    assert parsed == dt_util.as_utc(fixed_now).replace(microsecond=0)
