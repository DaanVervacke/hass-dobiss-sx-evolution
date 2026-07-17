"""Tests for DobissCoordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dobiss_sx_evolution.const import (
    CLOCK_SYNC_INTERVAL_HOURS,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONF_MAX200_HOST,
    CONNECTION_TYPE_USB,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from custom_components.dobiss_sx_evolution.controller import (
    ShutterConfig,
    UsbConnection,
)
from custom_components.dobiss_sx_evolution.coordinator import (
    DobissCoordinator,
    parse_output_lists,
)

from .conftest import MOCK_CONFIG
from .test_init import _make_entry_data


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
    async_fire_time_changed(hass, utcnow() + timedelta(milliseconds=100))
    await hass.async_block_till_done()

    assert coordinator.data == {("01", 1): 1}
    assert coordinator.last_update_success is True


async def test_coordinator_usb_connection(hass: HomeAssistant, mock_controller) -> None:
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
    from custom_components.dobiss_sx_evolution.coordinator import (  # noqa: PLC0415
        DobissController,
    )

    call_kwargs = DobissController.call_args.kwargs  # type: ignore[attr-defined]
    connection = call_kwargs["connection"]
    assert isinstance(connection, UsbConnection)
    assert connection.device == "/dev/ttyUSB0"


async def test_coordinator_coalesces_burst_notifications(
    hass: HomeAssistant, mock_controller
) -> None:
    """Multiple rapid state changes should result in one coordinator update."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    update_count = 0
    original = coordinator.async_set_updated_data

    def counting_update(data):
        nonlocal update_count
        update_count += 1
        original(data)

    coordinator.async_set_updated_data = counting_update

    # Simulate a burst of 5 rapid state changes
    listener = mock_controller.async_add_listener.call_args[0][0]
    for i in range(5):
        listener(("A", i + 1), 1)

    # Let the debounce timer fire
    async_fire_time_changed(hass, utcnow() + timedelta(milliseconds=100))
    await hass.async_block_till_done()

    # Should have coalesced into 1 update, not 5
    assert update_count == 1


# ---------------------------------------------------------------------------
# parse_output_lists
# ---------------------------------------------------------------------------


def test_parse_output_lists_light_on_non_dimmable_module():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {
                    "module": "A",
                    "dimmable": False,
                    "outputs": {"1": {"type": "light", "name": "L1"}},
                },
            }
        ],
    )
    lights, dimmers, shutters, switches = parse_output_lists(entry)
    assert ("A", 1) in lights
    assert len(dimmers) == 0
    assert shutters == []
    assert switches == []


def test_parse_output_lists_dimmable_module():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module B",
                "unique_id": "module:B",
                "data": {
                    "module": "B",
                    "dimmable": True,
                    "outputs": {"2": {"type": "light", "name": "D1"}},
                },
            }
        ],
    )
    lights, dimmers, shutters, switches = parse_output_lists(entry)
    assert lights == []
    assert ("B", 2) in dimmers
    assert shutters == []
    assert switches == []


def test_parse_output_lists_shutter():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        subentries_data=[
            {
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
            }
        ],
    )
    lights, dimmers, shutters, switches = parse_output_lists(entry)
    assert lights == []
    assert len(dimmers) == 0
    assert len(shutters) == 1
    assert shutters[0] == ShutterConfig(module="A", up_output=9, down_output=10)
    assert switches == []


def test_parse_output_lists_switch():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {
                    "module": "A",
                    "dimmable": False,
                    "outputs": {"3": {"type": "switch", "name": "Buzzer"}},
                },
            }
        ],
    )
    lights, dimmers, shutters, switches = parse_output_lists(entry)
    assert lights == []
    assert len(dimmers) == 0
    assert shutters == []
    assert switches == [("A", 3)]


# ---------------------------------------------------------------------------
# TCP client / clock sync
# ---------------------------------------------------------------------------


async def test_coordinator_creates_tcp_client_when_max200_host_set(
    hass: HomeAssistant, mock_controller
) -> None:
    """Coordinator creates a tcp_client when max200_host is in entry data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**MOCK_CONFIG, CONF_MAX200_HOST: "10.0.0.5"},
        title="DOBISS",
    )
    entry.add_to_hass(hass)
    coordinator = DobissCoordinator(hass, entry)
    assert coordinator.tcp_client is not None
    assert coordinator.tcp_client.host == "10.0.0.5"


async def test_coordinator_no_tcp_client_when_max200_host_absent(
    hass: HomeAssistant, mock_controller
) -> None:
    """Coordinator has no tcp_client when max200_host is not configured."""
    entry = _make_entry(hass)
    coordinator = DobissCoordinator(hass, entry)
    assert coordinator.tcp_client is None


async def test_coordinator_clock_sync_on_setup(
    hass: HomeAssistant, mock_controller
) -> None:
    """Clock sync fires once during _async_setup when max200_host is set."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(max200_host="10.0.0.5"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient"
    ) as mock_tcp_cls:
        mock_tcp = mock_tcp_cls.return_value
        mock_tcp.host = "10.0.0.5"
        mock_tcp.send_command = AsyncMock()

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        mock_tcp.send_command.assert_awaited_once()


async def test_coordinator_clock_sync_periodic(
    hass: HomeAssistant, mock_controller
) -> None:
    """Clock sync fires periodically via async_track_time_interval."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(max200_host="10.0.0.5"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient"
    ) as mock_tcp_cls:
        mock_tcp = mock_tcp_cls.return_value
        mock_tcp.host = "10.0.0.5"
        mock_tcp.send_command = AsyncMock()

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert mock_tcp.send_command.await_count == 1

        async_fire_time_changed(
            hass, utcnow() + timedelta(hours=CLOCK_SYNC_INTERVAL_HOURS + 1)
        )
        await hass.async_block_till_done()

        assert mock_tcp.send_command.await_count == 2


async def test_coordinator_clock_sync_failure_logged(
    hass: HomeAssistant, mock_controller
) -> None:
    """Clock sync failure is logged, does not crash setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(max200_host="10.0.0.5"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient"
    ) as mock_tcp_cls:
        mock_tcp = mock_tcp_cls.return_value
        mock_tcp.host = "10.0.0.5"
        mock_tcp.send_command = AsyncMock(side_effect=OSError("unreachable"))

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
