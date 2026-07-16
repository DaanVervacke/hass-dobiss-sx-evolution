"""Tests for the cover platform of DOBISS SX Evolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from custom_components.dobiss_sx_evolution.controller import ShutterConfig

from .conftest import MOCK_CONFIG, MOCK_CONNECTION


def _subentry_data() -> dict:
    """Return a ConfigSubentryData dict for a module with one shutter output."""
    return {
        "subentry_type": SUBENTRY_TYPE_MODULE,
        "title": "Module A",
        "unique_id": "module:A",
        "data": {
            "module": "A",
            "dimmable": False,
            "outputs": {
                # Output 9 is the "up" relay; output 10 is "down".
                "9": {
                    "type": "shutter",
                    "name": "Living Room Blind",
                    "down_output": "10",
                },
            },
        },
    }


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Create and load a config entry containing one shutter subentry."""
    entry_data = {
        "connection_type": CONNECTION_TYPE_SOCKETCAND,
        **MOCK_CONFIG,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        title="DOBISS",
        version=1,
        subentries_data=[_subentry_data()],
    )
    entry.add_to_hass(hass)

    fake_ctrl = MagicMock(name="DobissController")
    fake_ctrl.connection = MOCK_CONNECTION
    fake_ctrl.modules = ["A"]
    fake_ctrl.lights = []
    fake_ctrl.dimmers = []
    fake_ctrl.shutters = [MagicMock(module="A", up_output=9, down_output=10)]
    fake_ctrl.states = {}
    fake_ctrl.reconnect_count = 0
    fake_ctrl._bus = object()  # truthy — avoids UpdateFailed
    fake_ctrl.is_bus_connected = True

    fake_ctrl.async_setup = AsyncMock(return_value=None)
    fake_ctrl.async_shutdown = AsyncMock(return_value=None)
    fake_ctrl.async_request_dump = AsyncMock(return_value=None)
    fake_ctrl.async_open_shutter = AsyncMock(return_value=None)
    fake_ctrl.async_close_shutter = AsyncMock(return_value=None)
    fake_ctrl.async_stop_shutter = AsyncMock(return_value=None)

    unsubscribe = MagicMock(name="unsubscribe")
    fake_ctrl.async_add_listener = MagicMock(return_value=unsubscribe)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.DobissController",
        return_value=fake_ctrl,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


async def test_cover_entity_id_has_sx_evo_prefix(hass: HomeAssistant) -> None:
    """Entity IDs must follow the sx_evo_module_X_output_N_name pattern."""
    await _setup(hass)

    state = hass.states.get("cover.sx_evo_module_a_output_9_living_room_blind")
    assert state is not None, (
        "cover.sx_evo_module_a_output_9_living_room_blind was not found"
    )
    friendly = state.attributes.get("friendly_name", "")
    assert "sx_evo" not in friendly.lower(), (
        f"Friendly name must not carry the sx_evo prefix, got: {friendly!r}"
    )


async def test_open_cover_can_error_raises_ha_error(hass: HomeAssistant) -> None:
    """A CAN send failure must surface as HomeAssistantError, not a raw exception.

    python-can's BusABC.send() raises can.CanOperationError, whose MRO is
    CanOperationError -> CanError -> Exception (NOT RuntimeError). The entity
    must catch this broadly so users see a clean HomeAssistantError instead of
    the raw CAN exception.
    """
    entry = await _setup(hass)

    coordinator = entry.runtime_data
    coordinator.controller.async_open_shutter = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover",
            "open_cover",
            {"entity_id": "cover.sx_evo_module_a_output_9_living_room_blind"},
            blocking=True,
        )


async def test_close_cover_can_error_raises_ha_error(hass: HomeAssistant) -> None:
    """A CAN send failure on close must surface as HomeAssistantError."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator.controller.async_close_shutter = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover",
            "close_cover",
            {"entity_id": "cover.sx_evo_module_a_output_9_living_room_blind"},
            blocking=True,
        )


async def test_stop_cover_can_error_raises_ha_error(hass: HomeAssistant) -> None:
    """A CAN send failure on stop must surface as HomeAssistantError."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator.controller.async_stop_shutter = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover",
            "stop_cover",
            {"entity_id": "cover.sx_evo_module_a_output_9_living_room_blind"},
            blocking=True,
        )


async def test_open_cover_calls_controller(hass: HomeAssistant) -> None:
    """Opening the cover must call async_open_shutter with the right shutter."""
    entry = await _setup(hass)

    await hass.services.async_call(
        "cover",
        "open_cover",
        {"entity_id": "cover.sx_evo_module_a_output_9_living_room_blind"},
        blocking=True,
    )

    coordinator = entry.runtime_data
    coordinator.controller.async_open_shutter.assert_awaited_once_with(
        ShutterConfig(module="A", up_output=9, down_output=10)
    )


async def test_close_cover_calls_controller(hass: HomeAssistant) -> None:
    """Closing the cover must call async_close_shutter with the right shutter."""
    entry = await _setup(hass)

    await hass.services.async_call(
        "cover",
        "close_cover",
        {"entity_id": "cover.sx_evo_module_a_output_9_living_room_blind"},
        blocking=True,
    )

    coordinator = entry.runtime_data
    coordinator.controller.async_close_shutter.assert_awaited_once_with(
        ShutterConfig(module="A", up_output=9, down_output=10)
    )


async def test_stop_cover_calls_controller(hass: HomeAssistant) -> None:
    """Stopping the cover must call async_stop_shutter with the right shutter."""
    entry = await _setup(hass)

    await hass.services.async_call(
        "cover",
        "stop_cover",
        {"entity_id": "cover.sx_evo_module_a_output_9_living_room_blind"},
        blocking=True,
    )

    coordinator = entry.runtime_data
    coordinator.controller.async_stop_shutter.assert_awaited_once_with(
        ShutterConfig(module="A", up_output=9, down_output=10)
    )


async def test_cover_state_attributes(hass: HomeAssistant) -> None:
    """Shutters are open-loop: assumed_state, unknown position, shade class."""
    await _setup(hass)

    state = hass.states.get("cover.sx_evo_module_a_output_9_living_room_blind")
    assert state is not None

    assert state.attributes.get("assumed_state") is True
    assert state.state == "unknown"
    assert state.attributes.get("device_class") == "shade"

    supported = state.attributes.get("supported_features", 0)
    assert supported & CoverEntityFeature.OPEN
    assert supported & CoverEntityFeature.CLOSE
    assert supported & CoverEntityFeature.STOP


async def test_cover_unavailable_when_bus_disconnected(hass: HomeAssistant) -> None:
    """The cover entity must go unavailable when the CAN bus disconnects."""
    entry = await _setup(hass)

    state = hass.states.get("cover.sx_evo_module_a_output_9_living_room_blind")
    assert state is not None
    assert state.state != "unavailable"

    coordinator = entry.runtime_data
    coordinator.controller.is_bus_connected = False
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    state = hass.states.get("cover.sx_evo_module_a_output_9_living_room_blind")
    assert state is not None
    assert state.state == "unavailable"
