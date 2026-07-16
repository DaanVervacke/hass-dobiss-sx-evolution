"""Regression tests for the light platform.

Covers the bug where DobissLight always received dimmable=False because
async_setup_entry was reading the "dimmable" key from the per-output dict
(where it is never stored) instead of from the module-level subentry data.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from custom_components.dobiss_sx_evolution.protocol import (
    can_to_ha_brightness,
    ha_to_can_brightness,
)

from .conftest import MOCK_CONFIG, MOCK_CONNECTION

# Subentry data shape as produced by async_step_add_light:
# - "dimmable" lives at the module (subentry) level, not per-output.
# - Per-output dict only has "type" and "name"; never has a "dimmable" key.


def _subentry_data(*, dimmable: bool, title: str = "Module A") -> dict:
    """Return a ConfigSubentryData dict for a module with one light output."""
    return {
        "subentry_type": SUBENTRY_TYPE_MODULE,
        "title": title,
        "unique_id": "module:A",
        "data": {
            "module": "A",
            "dimmable": dimmable,
            "outputs": {
                "1": {"type": "light", "name": "Living Room"},
            },
        },
    }


def _make_entry(
    hass: HomeAssistant, *, dimmable: bool, title: str = "Module A"
) -> MockConfigEntry:
    """Build a config entry with one module subentry containing a single light."""
    entry_data = {
        "connection_type": CONNECTION_TYPE_SOCKETCAND,
        **MOCK_CONFIG,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        title="DOBISS",
        version=1,
        subentries_data=[_subentry_data(dimmable=dimmable, title=title)],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(
    hass: HomeAssistant, *, dimmable: bool, title: str = "Module A"
) -> MockConfigEntry:
    """Create and load the entry, returning it after setup."""
    entry = _make_entry(hass, dimmable=dimmable, title=title)

    # Determine which list the output lands in so dimmable() returns the right value.
    output_key = ("A", 1)
    dimmers = [output_key] if dimmable else []
    lights = [] if dimmable else [output_key]

    fake_ctrl = MagicMock(name="DobissController")
    fake_ctrl.connection = MOCK_CONNECTION
    fake_ctrl.modules = ["A"]
    fake_ctrl.lights = lights
    fake_ctrl.dimmers = dimmers
    fake_ctrl.shutters = []
    # State of 100 for dimmable so brightness is non-zero; 1 for on/off.
    fake_ctrl.states = {output_key: 100 if dimmable else 1}
    fake_ctrl.reconnect_count = 0
    fake_ctrl._bus = object()  # truthy — avoids UpdateFailed

    fake_ctrl.async_setup = AsyncMock(return_value=None)
    fake_ctrl.async_shutdown = AsyncMock(return_value=None)
    fake_ctrl.async_request_dump = AsyncMock(return_value=None)
    fake_ctrl.dimmable = MagicMock(side_effect=lambda key: key in fake_ctrl.dimmers)
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


async def test_dimmable_light_exposes_brightness_color_mode(
    hass: HomeAssistant,
) -> None:
    """A light on a dimmable module must advertise ColorMode.BRIGHTNESS.

    Regression: before the fix, async_setup_entry read cfg.get("dimmable", False)
    from the per-output dict (always False) instead of subentry.data["dimmable"],
    so DobissLight was always constructed with dimmable=False.
    """
    await _setup(hass, dimmable=True)

    state = hass.states.get("light.sx_evo_module_a_output_1_living_room")
    assert state is not None, "Light entity was not created"

    assert state.attributes.get("color_mode") == ColorMode.BRIGHTNESS
    assert ColorMode.BRIGHTNESS in state.attributes.get("supported_color_modes", [])
    # Brightness must be present (not None) for a dimmable light with state > 0.
    assert state.attributes.get(ATTR_BRIGHTNESS) is not None


async def test_non_dimmable_light_exposes_onoff_color_mode(
    hass: HomeAssistant,
) -> None:
    """A light on a non-dimmable module must advertise ColorMode.ONOFF."""
    await _setup(hass, dimmable=False)

    state = hass.states.get("light.sx_evo_module_a_output_1_living_room")
    assert state is not None, "Light entity was not created"

    assert state.attributes.get("color_mode") == ColorMode.ONOFF
    assert ColorMode.ONOFF in state.attributes.get("supported_color_modes", [])
    assert state.attributes.get(ATTR_BRIGHTNESS) is None


async def test_light_entity_id_has_sx_evo_prefix(
    hass: HomeAssistant,
) -> None:
    """Entity IDs must follow the sx_evo_module_X_output_N_name pattern."""
    await _setup(hass, dimmable=False)

    state = hass.states.get("light.sx_evo_module_a_output_1_living_room")
    assert state is not None, "light.sx_evo_module_a_output_1_living_room was not found"
    friendly = state.attributes.get("friendly_name", "")
    assert "sx_evo" not in friendly.lower(), (
        f"Friendly name must not carry the sx_evo prefix, got: {friendly!r}"
    )


async def test_light_links_to_module_device_without_area(
    hass: HomeAssistant,
) -> None:
    """Entity links to the module device, and no area is inherited by default.

    DOBISS modules span rooms, so the module device's area is left unset and
    the entity has no inherited area.  This lets users assign each light to
    its own room individually via the entity settings dialog.
    """
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = await _setup(hass, dimmable=False)

    entity_reg = er.async_get(hass)
    ent = entity_reg.async_get("light.sx_evo_module_a_output_1_living_room")
    assert ent is not None
    assert ent.device_id is not None, "Entity must link to the module device"
    assert ent.area_id is None, "Entity must have no default area"

    device_reg = dr.async_get(hass)
    device = device_reg.async_get(ent.device_id)
    assert device is not None
    assert (DOMAIN, f"{entry.entry_id}_module_A") in device.identifiers
    assert device.area_id is None, "Module device area must stay unset"


async def test_turn_on_without_brightness_does_not_overflow(
    hass: HomeAssistant,
) -> None:
    """Turning on a dimmer without an explicit brightness must stay in 0-255.

    Regression: async_turn_on used to leave the CAN state cache holding
    MAX_CAN_BRIGHTNESS_TX (144) after a bare turn_on with no optimistic HA
    brightness recorded. The brightness property then fell back to
    can_to_ha_brightness(144), which computes 144 * 255 // 90 = 408 -
    exceeding HA's 0-255 range.
    """
    from homeassistant.helpers import entity_platform as ep  # noqa: PLC0415

    await _setup(hass, dimmable=True)

    platforms = ep.async_get_platforms(hass, DOMAIN)
    light_platform = next(p for p in platforms if p.domain == "light")
    entity = light_platform.entities["light.sx_evo_module_a_output_1_living_room"]

    await entity.async_turn_on()

    brightness = entity.brightness
    assert brightness is not None
    assert 0 <= brightness <= 255, f"Brightness overflowed 0-255: {brightness!r}"


async def test_light_friendly_name_includes_device_and_output(
    hass: HomeAssistant,
) -> None:
    """The entity friendly name is the device name plus the output name.

    HA core computes friendly_name as "<device name> <entity name>" for all
    entities associated with a device.  The subentry title becomes the device
    name; the output name from setup is the entity name.
    """
    await _setup(hass, dimmable=False, title="Living Room Panel")

    state = hass.states.get("light.sx_evo_module_a_output_1_living_room")
    assert state is not None
    assert state.attributes.get("friendly_name") == "Living Room Panel Living Room"


async def test_turn_on_can_error_raises_ha_error(hass: HomeAssistant) -> None:
    """A CAN send failure must surface as HomeAssistantError, not a raw exception.

    python-can's BusABC.send() raises can.CanOperationError, whose MRO is
    CanOperationError -> CanError -> Exception (NOT RuntimeError). The entity
    must catch this broadly so users see a clean HomeAssistantError instead of
    the raw CAN exception, and so optimistic state is rolled back.
    """
    entry = await _setup(hass, dimmable=False)

    coordinator = entry.runtime_data
    coordinator.controller.async_turn_on = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.sx_evo_module_a_output_1_living_room"},
            blocking=True,
        )


async def test_light_unavailable_when_bus_disconnected(
    hass: HomeAssistant,
) -> None:
    """Entity must report unavailable when the CAN bus is disconnected."""
    entry = await _setup(hass, dimmable=False)
    coordinator = entry.runtime_data

    state = hass.states.get("light.sx_evo_module_a_output_1_living_room")
    assert state is not None
    assert state.state != "unavailable"

    coordinator.controller.is_bus_connected = False
    coordinator.async_set_updated_data(dict(coordinator.controller.states))
    await hass.async_block_till_done()

    state = hass.states.get("light.sx_evo_module_a_output_1_living_room")
    assert state is not None
    assert state.state == "unavailable"


async def test_turn_off_can_error_raises_ha_error(hass: HomeAssistant) -> None:
    """A CAN send failure on turn_off must surface as HomeAssistantError."""
    entry = await _setup(hass, dimmable=False)
    coordinator = entry.runtime_data
    coordinator.controller.async_turn_off = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "light",
            "turn_off",
            {"entity_id": "light.sx_evo_module_a_output_1_living_room"},
            blocking=True,
        )


def _get_light_entity(hass: HomeAssistant, entity_id: str):
    """Look up the live entity object from the platform."""
    from homeassistant.helpers import entity_platform as ep  # noqa: PLC0415

    platforms = ep.async_get_platforms(hass, DOMAIN)
    light_platform = next(p for p in platforms if p.domain == "light")
    return light_platform.entities[entity_id]


async def test_turn_on_with_brightness_sets_optimistic_value(
    hass: HomeAssistant,
) -> None:
    """turn_on with explicit brightness must retain the HA value, not quantise."""
    entry = await _setup(hass, dimmable=True)
    entity_id = "light.sx_evo_module_a_output_1_living_room"

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )

    entity = _get_light_entity(hass, entity_id)
    assert entity.brightness == 128

    coordinator = entry.runtime_data
    coordinator.controller.async_turn_on.assert_awaited_once_with(
        ("A", 1), brightness=128
    )


async def test_optimistic_brightness_retained_on_matching_echo(
    hass: HomeAssistant,
) -> None:
    """When the CAN echo matches what we sent, keep the optimistic HA value."""
    entry = await _setup(hass, dimmable=True)
    entity_id = "light.sx_evo_module_a_output_1_living_room"

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )

    coordinator = entry.runtime_data
    echo_can_value = ha_to_can_brightness(128)
    coordinator.controller.states = {("A", 1): echo_can_value}
    coordinator.async_set_updated_data(dict(coordinator.controller.states))
    await hass.async_block_till_done()

    entity = _get_light_entity(hass, entity_id)
    assert entity.brightness == 128


async def test_optimistic_brightness_cleared_on_external_change(
    hass: HomeAssistant,
) -> None:
    """A wall-switch dim must clear optimistic state and fall back to CAN value."""
    entry = await _setup(hass, dimmable=True)
    entity_id = "light.sx_evo_module_a_output_1_living_room"

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )

    coordinator = entry.runtime_data
    wall_switch_can_value = 45
    coordinator.controller.states = {("A", 1): wall_switch_can_value}
    coordinator.async_set_updated_data(dict(coordinator.controller.states))
    await hass.async_block_till_done()

    entity = _get_light_entity(hass, entity_id)
    assert entity.brightness == can_to_ha_brightness(wall_switch_can_value)
    assert entity._optimistic_can_value is None


async def test_turn_on_brightness_error_rolls_back_optimistic_state(
    hass: HomeAssistant,
) -> None:
    """A failed turn_on must roll back optimistic brightness to None."""
    entry = await _setup(hass, dimmable=True)
    entity_id = "light.sx_evo_module_a_output_1_living_room"

    coordinator = entry.runtime_data
    coordinator.controller.async_turn_on = AsyncMock(
        side_effect=Exception("CAN send failed")
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": entity_id, ATTR_BRIGHTNESS: 128},
            blocking=True,
        )

    entity = _get_light_entity(hass, entity_id)
    assert entity._attr_brightness is None
    assert entity._optimistic_can_value is None
