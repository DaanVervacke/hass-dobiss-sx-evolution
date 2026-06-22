"""Tests for DobissCoordinator."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import DOMAIN
from custom_components.dobiss_sx_evolution.coordinator import DobissCoordinator

from .conftest import MOCK_CONFIG


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Build a minimal config entry with no module subentries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        title="DOBISS SX Evolution",
    )
    entry.add_to_hass(hass)
    return entry


async def test_coordinator_update_failed_when_bus_down(
    hass: HomeAssistant, mock_controller
) -> None:
    """_async_update_data raises UpdateFailed when the bus is None."""
    entry = _make_entry(hass)
    coordinator = DobissCoordinator(hass, entry)

    mock_controller._bus = None

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_coordinator_returns_states_when_bus_up(
    hass: HomeAssistant, mock_controller
) -> None:
    """_async_update_data returns the controller's states dict when bus is up."""
    entry = _make_entry(hass)
    coordinator = DobissCoordinator(hass, entry)

    mock_controller._bus = object()
    mock_controller.states = {("01", 1): 1, ("01", 2): 0}

    result = await coordinator._async_update_data()

    assert result == {("01", 1): 1, ("01", 2): 0}
    # Returned dict must be a copy, not the controller's live cache.
    assert result is not mock_controller.states


async def test_coordinator_listener_invokes_update(
    hass: HomeAssistant, mock_controller
) -> None:
    """_on_controller_update pushes the controller's states into the coordinator."""
    entry = _make_entry(hass)
    coordinator = DobissCoordinator(hass, entry)

    mock_controller.states = {("01", 1): 1}

    coordinator._on_controller_update(("01", 1), 1)
    await hass.async_block_till_done()

    assert coordinator.data == {("01", 1): 1}
    assert coordinator.last_update_success is True
