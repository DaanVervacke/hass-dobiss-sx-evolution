"""Tests for the switch platform.

Switches are generic on/off relays: the CAN protocol is identical to a
non-dimmable light (state 1/0), so these tests mirror test_light.py's
non-dimmable coverage without any brightness/optimistic-tracking concerns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)

from .conftest import MOCK_CONFIG, MOCK_CONNECTION


def _subentry_data(*, title: str = "Module A") -> dict:
    """Return a ConfigSubentryData dict for a module with one switch output."""
    return {
        "subentry_type": SUBENTRY_TYPE_MODULE,
        "title": title,
        "unique_id": "module:A",
        "data": {
            "module": "A",
            "dimmable": False,
            "outputs": {
                "3": {"type": "switch", "name": "Door Buzzer"},
            },
        },
    }


def _make_entry(hass: HomeAssistant, *, title: str = "Module A") -> MockConfigEntry:
    """Build a config entry with one module subentry containing a single switch."""
    entry_data = {
        "connection_type": CONNECTION_TYPE_SOCKETCAND,
        **MOCK_CONFIG,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        title="DOBISS",
        version=1,
        subentries_data=[_subentry_data(title=title)],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(
    hass: HomeAssistant, *, state: int = 0, title: str = "Module A"
) -> MockConfigEntry:
    """Create and load the entry, returning it after setup."""
    entry = _make_entry(hass, title=title)

    output_key = ("A", 3)

    fake_ctrl = MagicMock(name="DobissController")
    fake_ctrl.connection = MOCK_CONNECTION
    fake_ctrl.modules = ["A"]
    fake_ctrl.lights = []
    fake_ctrl.dimmers = []
    fake_ctrl.shutters = []
    fake_ctrl.switches = [output_key]
    fake_ctrl.states = {output_key: state}
    fake_ctrl.reconnect_count = 0
    fake_ctrl.is_bus_connected = True
    fake_ctrl._bus = object()  # truthy — avoids UpdateFailed

    fake_ctrl.async_setup = AsyncMock(return_value=None)
    fake_ctrl.async_shutdown = AsyncMock(return_value=None)
    fake_ctrl.async_request_dump = AsyncMock(return_value=None)
    fake_ctrl.dimmable = MagicMock(return_value=False)
    fake_ctrl.async_turn_on = AsyncMock(return_value=None)
    fake_ctrl.async_turn_off = AsyncMock(return_value=None)

    unsubscribe = MagicMock(name="unsubscribe")
    fake_ctrl.async_add_listener = MagicMock(return_value=unsubscribe)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.DobissController",
        return_value=fake_ctrl,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


async def test_switch_setup_and_state(hass: HomeAssistant) -> None:
    """A switch output creates an entity whose is_on reflects controller state."""
    await _setup(hass, state=0)

    state = hass.states.get("switch.sx_evo_module_a_output_3_door_buzzer")
    assert state is not None, "Switch entity was not created"
    assert state.state == "off"


async def test_switch_state_on_when_controller_state_positive(
    hass: HomeAssistant,
) -> None:
    """is_on must be True when the controller reports a non-zero state."""
    await _setup(hass, state=1)

    state = hass.states.get("switch.sx_evo_module_a_output_3_door_buzzer")
    assert state is not None
    assert state.state == "on"


async def test_switch_turn_on_off(hass: HomeAssistant) -> None:
    """async_turn_on/async_turn_off must delegate to the controller."""
    entry = await _setup(hass, state=0)
    coordinator = entry.runtime_data

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.sx_evo_module_a_output_3_door_buzzer"},
        blocking=True,
    )
    coordinator.controller.async_turn_on.assert_awaited_once_with(("A", 3))

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.sx_evo_module_a_output_3_door_buzzer"},
        blocking=True,
    )
    coordinator.controller.async_turn_off.assert_awaited_once_with(("A", 3))


async def test_switch_bus_error_raises_ha_error(hass: HomeAssistant) -> None:
    """A CAN send failure during turn_on/turn_off must surface as HomeAssistantError."""
    entry = await _setup(hass, state=0)
    coordinator = entry.runtime_data
    coordinator.controller.async_turn_on = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.sx_evo_module_a_output_3_door_buzzer"},
            blocking=True,
        )

    coordinator.controller.async_turn_off = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": "switch.sx_evo_module_a_output_3_door_buzzer"},
            blocking=True,
        )


async def test_switch_availability(hass: HomeAssistant) -> None:
    """Entity must report unavailable when the CAN bus is disconnected."""
    entry = await _setup(hass, state=0)
    coordinator = entry.runtime_data

    state = hass.states.get("switch.sx_evo_module_a_output_3_door_buzzer")
    assert state is not None
    assert state.state != "unavailable"

    coordinator.controller.is_bus_connected = False
    coordinator.async_set_updated_data(dict(coordinator.controller.states))
    await hass.async_block_till_done()

    state = hass.states.get("switch.sx_evo_module_a_output_3_door_buzzer")
    assert state is not None
    assert state.state == "unavailable"


async def test_switch_unique_id_and_entity_id(hass: HomeAssistant) -> None:
    """unique_id follows {subentry_id}-switch_{output}, entity_id gets sx_evo prefix."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = await _setup(hass, state=0)

    entity_id = "switch.sx_evo_module_a_output_3_door_buzzer"
    state = hass.states.get(entity_id)
    assert state is not None

    entity_reg = er.async_get(hass)
    ent = entity_reg.async_get(entity_id)
    assert ent is not None

    subentry_id = next(iter(entry.subentries))
    assert ent.unique_id == f"{subentry_id}-switch_3"
