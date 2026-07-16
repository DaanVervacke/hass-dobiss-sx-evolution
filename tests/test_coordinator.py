"""Tests for DobissCoordinator."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONNECTION_TYPE_USB,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from custom_components.dobiss_sx_evolution.controller import ShutterConfig, UsbConnection
from custom_components.dobiss_sx_evolution.coordinator import (
    DobissCoordinator,
    parse_output_lists,
)

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

    mock_controller.is_bus_connected = False

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_coordinator_returns_states_when_bus_up(
    hass: HomeAssistant, mock_controller
) -> None:
    """_async_update_data returns the controller's states dict when bus is up."""
    entry = _make_entry(hass)
    coordinator = DobissCoordinator(hass, entry)

    mock_controller.is_bus_connected = True
    mock_controller.states = {("01", 1): 1, ("01", 2): 0}

    result = await coordinator._async_update_data()

    assert result == {("01", 1): 1, ("01", 2): 0}
    # Returned dict must be a copy, not the controller's live cache.
    assert result is not mock_controller.states


async def test_coordinator_setup_raises_not_ready_on_non_os_error(
    hass: HomeAssistant, mock_controller
) -> None:
    """_async_setup wraps non-OSError controller failures in ConfigEntryNotReady.

    python-can's can.Bus() raises can.CanInitializationError, whose MRO is
    CanInitializationError -> CanError -> Exception (not OSError), so the
    setup catch must be broad enough to still trigger a retryable setup
    failure instead of a permanent SETUP_ERROR.
    """
    entry = _make_entry(hass)
    coordinator = DobissCoordinator(hass, entry)

    mock_controller.async_setup.side_effect = Exception("CAN init failed")

    with pytest.raises(ConfigEntryNotReady):
        await coordinator._async_setup()


async def test_coordinator_setup_failure_shuts_down_controller(
    hass: HomeAssistant, mock_controller
) -> None:
    """_async_setup must call async_shutdown on controller setup failure."""
    entry = _make_entry(hass)
    coordinator = DobissCoordinator(hass, entry)

    mock_controller.async_setup.side_effect = Exception("CAN init failed")

    with pytest.raises(ConfigEntryNotReady):
        await coordinator._async_setup()

    mock_controller.async_shutdown.assert_called_once()


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


async def test_coordinator_usb_connection(
    hass: HomeAssistant, mock_controller
) -> None:
    """Coordinator must construct a UsbConnection for USB entries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_USB, CONF_DEVICE: "/dev/ttyUSB0"},
        title="DOBISS USB",
    )
    entry.add_to_hass(hass)

    DobissCoordinator(hass, entry)

    # mock_controller patches coordinator.DobissController with a MagicMock
    # whose return_value is the fake controller. The patched class itself
    # records the constructor call so we can inspect the connection kwarg.
    from custom_components.dobiss_sx_evolution.coordinator import DobissController

    call_kwargs = DobissController.call_args.kwargs  # type: ignore[attr-defined]
    connection = call_kwargs["connection"]
    assert isinstance(connection, UsbConnection)
    assert connection.device == "/dev/ttyUSB0"


# ---------------------------------------------------------------------------
# parse_output_lists
# ---------------------------------------------------------------------------


def test_parse_output_lists_light_on_non_dimmable_module():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        subentries_data=[{
            "subentry_type": SUBENTRY_TYPE_MODULE,
            "title": "Module A",
            "unique_id": "module:A",
            "data": {
                "module": "A",
                "dimmable": False,
                "outputs": {"1": {"type": "light", "name": "L1"}},
            },
        }],
    )
    lights, dimmers, shutters = parse_output_lists(entry)
    assert ("A", 1) in lights
    assert len(dimmers) == 0
    assert shutters == []


def test_parse_output_lists_dimmable_module():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        subentries_data=[{
            "subentry_type": SUBENTRY_TYPE_MODULE,
            "title": "Module B",
            "unique_id": "module:B",
            "data": {
                "module": "B",
                "dimmable": True,
                "outputs": {"2": {"type": "light", "name": "D1"}},
            },
        }],
    )
    lights, dimmers, shutters = parse_output_lists(entry)
    assert lights == []
    assert ("B", 2) in dimmers
    assert shutters == []


def test_parse_output_lists_shutter():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        subentries_data=[{
            "subentry_type": SUBENTRY_TYPE_MODULE,
            "title": "Module A",
            "unique_id": "module:A",
            "data": {
                "module": "A",
                "dimmable": False,
                "outputs": {
                    "9": {"type": "shutter", "down_output": "10", "name": "S1"},
                },
            },
        }],
    )
    lights, dimmers, shutters = parse_output_lists(entry)
    assert lights == []
    assert len(dimmers) == 0
    assert len(shutters) == 1
    assert shutters[0] == ShutterConfig(module="A", up_output=9, down_output=10)
