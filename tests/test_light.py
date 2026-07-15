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

from .conftest import MOCK_CONFIG

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
    """Build a config entry with one module subentry containing a single light output.

    """
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
    fake_ctrl.connection_type = CONNECTION_TYPE_SOCKETCAND
    fake_ctrl.host = MOCK_CONFIG["host"]
    fake_ctrl.port = MOCK_CONFIG["port"]
    fake_ctrl.interface = MOCK_CONFIG["interface"]
    fake_ctrl.device = None
    fake_ctrl.baudrate = None
    fake_ctrl.can_interface = None
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

    state = hass.states.get("light.sx_evo_module_a_living_room")
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

    state = hass.states.get("light.sx_evo_module_a_living_room")
    assert state is not None, "Light entity was not created"

    assert state.attributes.get("color_mode") == ColorMode.ONOFF
    assert ColorMode.ONOFF in state.attributes.get("supported_color_modes", [])
    assert state.attributes.get(ATTR_BRIGHTNESS) is None


async def test_light_entity_id_has_sx_evo_prefix(
    hass: HomeAssistant,
) -> None:
    """Entity IDs for lights must be prefixed with sx_evo_.

    The friendly name must remain unprefixed (e.g. "Living Room", not
    "sx_evo_Living Room").
    """
    await _setup(hass, dimmable=False)

    state = hass.states.get("light.sx_evo_module_a_living_room")
    assert state is not None, "light.sx_evo_module_a_living_room was not found"
    friendly = state.attributes.get("friendly_name", "")
    assert "sx_evo_" not in friendly, (
        f"Friendly name must not carry the sx_evo_ prefix, got: {friendly!r}"
    )
    assert friendly == "Living Room", (
        f"Expected friendly name 'Living Room' (as typed at setup), got: {friendly!r}"
    )
    # Old and un-scoped entity_ids must not be registered.
    assert hass.states.get("light.living_room") is None
    assert hass.states.get("light.sx_evo_living_room") is None


async def test_light_links_to_module_device_without_area(
    hass: HomeAssistant,
) -> None:
    """Entity links to the module device, and no area is inherited by default.

    DOBISS modules span rooms, so the module device's area is left unset and
    the entity has no inherited area.  This lets users assign each light to
    its own room individually via the entity settings dialog.
    """
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry = await _setup(hass, dimmable=False)

    entity_reg = er.async_get(hass)
    ent = entity_reg.async_get("light.sx_evo_module_a_living_room")
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
    from homeassistant.helpers import entity_platform as ep

    await _setup(hass, dimmable=True)

    platforms = ep.async_get_platforms(hass, DOMAIN)
    light_platform = next(p for p in platforms if p.domain == "light")
    entity = light_platform.entities["light.sx_evo_module_a_living_room"]

    await entity.async_turn_on()

    brightness = entity.brightness
    assert brightness is not None
    assert 0 <= brightness <= 255, f"Brightness overflowed 0-255: {brightness!r}"


async def test_light_friendly_name_is_output_name_only(
    hass: HomeAssistant,
) -> None:
    """The entity friendly name must be exactly the output name from setup.

    The subentry title (module rename) is used for the device name but does
    NOT get concatenated into the entity friendly name.
    """
    await _setup(hass, dimmable=False, title="Living Room Panel")

    state = hass.states.get("light.sx_evo_module_a_living_room")
    assert state is not None
    assert state.attributes.get("friendly_name") == "Living Room", (
        f"Expected friendly name to be the output name only, got: "
        f"{state.attributes.get('friendly_name')!r}"
    )


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
            {"entity_id": "light.sx_evo_module_a_living_room"},
            blocking=True,
        )
