"""Regression tests for the light platform.

Covers the bug where DobissLight always received dimmable=False because
async_setup_entry was reading the "dimmable" key from the per-output dict
(where it is never stored) instead of from the module-level subentry data.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.core import HomeAssistant
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
    assert friendly == "Module A Living Room", (
        f"Expected friendly name 'Module A Living Room', got: {friendly!r}"
    )
    # Old and un-scoped entity_ids must not be registered.
    assert hass.states.get("light.living_room") is None
    assert hass.states.get("light.sx_evo_living_room") is None


async def test_light_friendly_name_uses_subentry_title(
    hass: HomeAssistant,
) -> None:
    """The entity friendly name is prefixed with the subentry title.

    Regression guard for the fast-path reload: renaming a module subentry
    (title change) must flow through into the entity friendly name so users
    see their chosen module name next to the output name.
    """
    await _setup(hass, dimmable=False, title="Living Room Panel")

    state = hass.states.get("light.sx_evo_module_a_living_room")
    assert state is not None
    assert state.attributes.get("friendly_name") == "Living Room Panel Living Room", (
        f"Expected friendly name to lead with the subentry title, got: "
        f"{state.attributes.get('friendly_name')!r}"
    )
