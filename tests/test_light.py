"""Regression tests for the light platform.

Covers the bug where DobissLight always received dimmable=False because
async_setup_entry was reading the "dimmable" key from the per-output dict
(where it is never stored) instead of from the module-level subentry data.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.core import HomeAssistant

from custom_components.dobiss_sx_evolution.const import DOMAIN, SUBENTRY_TYPE_MODULE

from .conftest import MOCK_CONFIG

# Subentry data shape as produced by async_step_add_light:
# - "dimmable" lives at the module (subentry) level, not per-output.
# - Per-output dict only has "type" and "name"; never has a "dimmable" key.


def _subentry_data(*, dimmable: bool) -> dict:
    """Return a ConfigSubentryData dict for a module with one light output."""
    return {
        "subentry_type": SUBENTRY_TYPE_MODULE,
        "title": "Module A",
        "unique_id": "module:A",
        "data": {
            "module": "A",
            "dimmable": dimmable,
            "outputs": {
                "1": {"type": "light", "name": "Living Room"},
            },
        },
    }


def _make_entry(hass: HomeAssistant, *, dimmable: bool) -> MockConfigEntry:
    """Build a config entry with one module subentry containing a single light output."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        title="DOBISS",
        version=1,
        subentries_data=[_subentry_data(dimmable=dimmable)],
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, *, dimmable: bool) -> MockConfigEntry:
    """Create and load the entry, returning it after setup."""
    entry = _make_entry(hass, dimmable=dimmable)

    # Determine which list the output lands in so dimmable() returns the right value.
    output_key = ("A", 1)
    dimmers = [output_key] if dimmable else []
    lights = [] if dimmable else [output_key]

    fake_ctrl = MagicMock(name="DobissController")
    fake_ctrl.host = MOCK_CONFIG["host"]
    fake_ctrl.port = MOCK_CONFIG["port"]
    fake_ctrl.interface = MOCK_CONFIG["interface"]
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

    state = hass.states.get("light.living_room")
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

    state = hass.states.get("light.living_room")
    assert state is not None, "Light entity was not created"

    assert state.attributes.get("color_mode") == ColorMode.ONOFF
    assert ColorMode.ONOFF in state.attributes.get("supported_color_modes", [])
    assert state.attributes.get(ATTR_BRIGHTNESS) is None
