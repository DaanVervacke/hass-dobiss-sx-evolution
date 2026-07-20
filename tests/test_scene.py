"""Tests for the scene platform (DOBISS moods)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
    SUBENTRY_TYPE_MOOD,
)

from .conftest import MOCK_CONFIG, MOCK_CONNECTION


def _mood_subentry_data(mood_number: int = 0, name: str = "Test Mood") -> dict:
    return {
        "subentry_type": SUBENTRY_TYPE_MOOD,
        "title": name,
        "unique_id": f"mood:{mood_number}",
        "data": {"mood_number": mood_number},
    }


def _make_entry(
    hass: HomeAssistant,
    subentries: list[dict] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        title="DOBISS",
        version=1,
        subentries_data=subentries or [_mood_subentry_data()],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(
    hass: HomeAssistant,
    subentries: list[dict] | None = None,
) -> MockConfigEntry:
    entry = _make_entry(hass, subentries)

    fake_ctrl = MagicMock(name="DobissController")
    fake_ctrl.connection = MOCK_CONNECTION
    fake_ctrl.modules = []
    fake_ctrl.lights = []
    fake_ctrl.dimmers = []
    fake_ctrl.shutters = []
    fake_ctrl.switches = []
    fake_ctrl.states = {}
    fake_ctrl.reconnect_count = 0
    fake_ctrl.is_bus_connected = True

    fake_ctrl.async_setup = AsyncMock(return_value=None)
    fake_ctrl.async_shutdown = AsyncMock(return_value=None)
    fake_ctrl.async_request_dump = AsyncMock(return_value=None)
    fake_ctrl.async_activate_mood = AsyncMock(return_value=None)
    fake_ctrl.async_refresh_and_settle = AsyncMock(return_value=None)

    unsubscribe = MagicMock(name="unsubscribe")
    fake_ctrl.async_add_listener = MagicMock(return_value=unsubscribe)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.DobissController",
        return_value=fake_ctrl,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


async def test_scene_entity_created(hass: HomeAssistant) -> None:
    """A mood subentry produces a scene entity."""
    await _setup(hass)

    state = hass.states.get("scene.max200_test_mood")
    assert state is not None, "Scene entity was not created"


async def test_scene_activate(hass: HomeAssistant) -> None:
    """Activating the scene sends the mood CAN frame via the controller."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    await hass.services.async_call(
        "scene",
        "turn_on",
        {"entity_id": "scene.max200_test_mood"},
        blocking=True,
    )
    coordinator.controller.async_activate_mood.assert_awaited_once_with(0)


async def test_scene_activate_different_mood_number(hass: HomeAssistant) -> None:
    """Each scene sends the correct mood number from its subentry data."""
    entry = await _setup(
        hass,
        subentries=[_mood_subentry_data(mood_number=42, name="Night Mode")],
    )
    coordinator = entry.runtime_data

    await hass.services.async_call(
        "scene",
        "turn_on",
        {"entity_id": "scene.max200_night_mode"},
        blocking=True,
    )
    coordinator.controller.async_activate_mood.assert_awaited_once_with(42)


async def test_scene_bus_error_raises_ha_error(hass: HomeAssistant) -> None:
    """A CAN send failure during activation surfaces as HomeAssistantError."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    coordinator.controller.async_activate_mood = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "scene",
            "turn_on",
            {"entity_id": "scene.max200_test_mood"},
            blocking=True,
        )


async def test_scene_unavailable_when_bus_down(hass: HomeAssistant) -> None:
    """Scene entity reports unavailable when the CAN bus is disconnected."""
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    state = hass.states.get("scene.max200_test_mood")
    assert state is not None
    assert state.state != "unavailable"

    coordinator.controller.is_bus_connected = False
    coordinator.async_set_updated_data(dict(coordinator.controller.states))
    await hass.async_block_till_done()

    state = hass.states.get("scene.max200_test_mood")
    assert state is not None
    assert state.state == "unavailable"


async def test_scene_hub_device_not_duplicated(hass: HomeAssistant) -> None:
    """Multiple mood scenes share the hub device without creating duplicates."""
    entry = await _setup(
        hass,
        subentries=[
            _mood_subentry_data(mood_number=0, name="Mood A"),
            _mood_subentry_data(mood_number=1, name="Mood B"),
        ],
    )

    device_reg = dr.async_get(hass)
    hub_identifier = (DOMAIN, entry.entry_id)
    devices = [
        d for d in device_reg.devices.values() if hub_identifier in d.identifiers
    ]
    assert len(devices) == 1, f"Expected 1 hub device, got {len(devices)}"


async def test_scene_unique_id(hass: HomeAssistant) -> None:
    """unique_id follows {subentry_id}-mood."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = await _setup(hass)

    entity_reg = er.async_get(hass)
    ent = entity_reg.async_get("scene.max200_test_mood")
    assert ent is not None

    mood_subentry_id = next(
        sid
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_TYPE_MOOD
    )
    assert ent.unique_id == f"{mood_subentry_id}-mood"
