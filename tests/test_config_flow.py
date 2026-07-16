"""Tests for the DOBISS SX Evolution config flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.config_flow import (
    DobissConfigFlow,
    ModuleSubentryFlowHandler,
    _occupied_outputs_in_module,
    _validate_module,
)
from custom_components.dobiss_sx_evolution.const import (
    CONF_DEVICE,
    CONNECTION_TYPE_SOCKETCAND,
    CONNECTION_TYPE_USB,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)

from .conftest import MOCK_CONFIG

MOCK_USB_DEVICE = "/dev/serial/by-id/usb-mock-can-adapter"


@pytest.fixture
def mock_usb_ports():
    """Mock USB serial port enumeration and HA USB helpers."""
    fake_port = MagicMock()
    fake_port.device = "/dev/ttyUSB0"
    fake_port.serial_number = "ABC123"
    fake_port.manufacturer = "MockCorp"
    fake_port.description = "Mock CAN Adapter"
    fake_port.vid = 0x1234
    fake_port.pid = 0x5678

    with (
        patch(
            "custom_components.dobiss_sx_evolution.config_flow.serial.tools.list_ports.comports",
            return_value=[fake_port],
        ),
        patch(
            "homeassistant.components.usb.get_serial_by_id",
            return_value=MOCK_USB_DEVICE,
        ),
        patch(
            "homeassistant.components.usb.human_readable_device_name",
            return_value="MockCorp Mock CAN Adapter",
        ),
    ):
        yield


async def _setup_loaded_entry(
    hass: HomeAssistant,
    mock_controller,
    subentries_data: list[dict] | None = None,
) -> MockConfigEntry:
    """Create and set up a config entry so subentry flows can be tested."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
        version=1,
        subentries_data=subentries_data or [],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_user_flow_success(
    hass: HomeAssistant, mock_probe, mock_controller
) -> None:
    """Happy-path user flow creates a config entry."""
    # Step 1: Show connection type selector
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Step 2: User selects socketcand and submits
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "socketcand"

    # Step 3: User enters socketcand details
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], user_input=MOCK_CONFIG
    )
    await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"]["connection_type"] == CONNECTION_TYPE_SOCKETCAND
    assert result3["data"]["host"] == MOCK_CONFIG["host"]
    assert result3["data"]["port"] == MOCK_CONFIG["port"]
    assert result3["data"]["interface"] == MOCK_CONFIG["interface"]
    assert mock_probe.called


async def test_user_flow_invalid_connection_type(hass: HomeAssistant) -> None:
    """Submitting an unrecognized connection type shows an error.

    The SelectSelector schema normally rejects unlisted values before the
    step handler runs (custom_value defaults to False), so the
    invalid_connection_type branch is only reachable by invoking the step
    method directly, bypassing schema-level validation.
    """
    flow = DobissConfigFlow()
    flow.hass = hass

    result = await flow.async_step_user({"connection_type": "nonexistent"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["connection_type"] == "invalid_connection_type"


async def test_reconfigure_invalid_connection_type(hass: HomeAssistant) -> None:
    """Submitting an unrecognized connection type on reconfigure shows an error.

    Same schema-bypass rationale as test_user_flow_invalid_connection_type.
    """
    flow = DobissConfigFlow()
    flow.hass = hass

    result = await flow.async_step_reconfigure({"connection_type": "nonexistent"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"]["connection_type"] == "invalid_connection_type"


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """User flow re-renders the form with cannot_connect when probe raises."""
    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        side_effect=OSError("boom"),
    ):
        # Step 1: Connection type selector
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        assert result["type"] == FlowResultType.FORM

        # Step 2: Select socketcand
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND},
        )
        assert result2["type"] == FlowResultType.FORM

        # Step 3: Try to configure with invalid data
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input=MOCK_CONFIG
        )

    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"] == {"base": "cannot_connect"}


async def test_user_flow_rejects_invalid_port(hass: HomeAssistant, mock_probe) -> None:
    """Port outside the 1-65535 range is rejected by schema validation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND}
    )
    assert result2["type"] == FlowResultType.FORM

    for bad_port in (0, 99999):
        with pytest.raises(InvalidData):
            await hass.config_entries.flow.async_configure(
                result2["flow_id"], user_input={**MOCK_CONFIG, "port": bad_port}
            )

    assert not mock_probe.called


async def test_user_flow_connection_failure(hass: HomeAssistant) -> None:
    """User flow re-renders the form with cannot_connect on a non-OSError failure.

    Complements test_user_flow_cannot_connect, which covers the OSError branch;
    this exercises the catch-all Exception branch in async_step_socketcand.
    """
    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        side_effect=ValueError("unexpected probe failure"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input=MOCK_CONFIG
        )

    assert result3["type"] == FlowResultType.FORM
    assert result3["step_id"] == "socketcand"
    assert result3["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(hass: HomeAssistant, mock_probe) -> None:
    """Second entry with the same host/port/interface aborts."""
    # The unique_id format has changed to include connection_type
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_SOCKETCAND,
            **MOCK_CONFIG,
        },
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    existing.add_to_hass(hass)

    # Step 1: Connection type selector
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM

    # Step 2: Select socketcand
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND}
    )
    assert result2["type"] == FlowResultType.FORM

    # Step 3: Try to configure with same settings
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], user_input=MOCK_CONFIG
    )

    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "already_configured"


async def test_reauth_flow_success(hass: HomeAssistant, mock_probe) -> None:
    """Reauth flow with a working probe updates and reloads the entry."""
    # Entry now has connection_type
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_SOCKETCAND,
            **MOCK_CONFIG,
        },
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    # Reauth now goes to reauth_socketcand step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_socketcand"

    new_config = {**MOCK_CONFIG, "host": "192.168.1.99"}
    with patch(
        "custom_components.dobiss_sx_evolution.async_setup_entry",
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=new_config
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data["host"] == "192.168.1.99"


async def test_reauth_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Reauth flow re-renders form on probe failure."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_SOCKETCAND,
            **MOCK_CONFIG,
        },
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        side_effect=OSError("boom"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_socketcand"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=MOCK_CONFIG
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow_prefills_current_values(hass: HomeAssistant) -> None:
    """Reauth form is pre-filled with the entry's current connection details."""
    entry_data = {
        "connection_type": CONNECTION_TYPE_SOCKETCAND,
        "host": "10.0.0.5",
        "port": 1234,
        "interface": "vcan0",
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:10.0.0.5:1234/vcan0",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_socketcand"

    assert result["data_schema"] is not None
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["host"] == "10.0.0.5"
    assert defaults["port"] == 1234
    assert defaults["interface"] == "vcan0"


async def test_reconfigure_socketcand_success(hass: HomeAssistant, mock_probe) -> None:
    """Reconfigure flow updates connection params and reloads."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reconfigure_socketcand"

    new_config = {**MOCK_CONFIG, "host": "10.0.0.99"}
    with patch(
        "custom_components.dobiss_sx_evolution.async_setup_entry",
        return_value=True,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input=new_config
        )
        await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "10.0.0.99"


async def test_reconfigure_cannot_connect(
    hass: HomeAssistant,
) -> None:
    """Reconfigure flow shows error on probe failure."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        side_effect=OSError("timeout"),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input=MOCK_CONFIG
        )

    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_usb_success(
    hass: HomeAssistant, mock_probe, mock_usb_ports
) -> None:
    """Reconfigure flow can switch to a USB connection and reloads."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"connection_type": CONNECTION_TYPE_USB},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "reconfigure_usb"

    with patch(
        "custom_components.dobiss_sx_evolution.async_setup_entry",
        return_value=True,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], user_input={CONF_DEVICE: MOCK_USB_DEVICE}
        )
        await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"
    assert entry.data["connection_type"] == CONNECTION_TYPE_USB
    assert entry.data[CONF_DEVICE] == MOCK_USB_DEVICE


# ---------------------------------------------------------------------------
# _validate_module / _occupied_outputs_in_module (pure helper functions)
# ---------------------------------------------------------------------------


def test_validate_module_accepts_single_letter():
    assert _validate_module("A") is None
    assert _validate_module("Z") is None


def test_validate_module_rejects_invalid():
    assert _validate_module("") == "invalid_module"
    assert _validate_module("AB") == "invalid_module"
    assert _validate_module("1") == "invalid_module"
    assert _validate_module(" ") == "invalid_module"


def test_validate_module_rejects_non_ascii_letter() -> None:
    """Cyrillic A (U+0410) looks like Latin A but is not ASCII."""
    assert _validate_module("А") == "invalid_module"  # noqa: RUF001


def test_occupied_outputs_light_only():
    outputs = {"1": {"type": "light", "name": "L1"}}
    assert _occupied_outputs_in_module(outputs) == {1}


def test_occupied_outputs_shutter_claims_both():
    outputs = {"3": {"type": "shutter", "down_output": "4", "name": "S1"}}
    assert _occupied_outputs_in_module(outputs) == {3, 4}


def test_occupied_outputs_mixed():
    outputs = {
        "1": {"type": "light", "name": "L1"},
        "3": {"type": "shutter", "down_output": "4", "name": "S1"},
    }
    assert _occupied_outputs_in_module(outputs) == {1, 3, 4}


# ---------------------------------------------------------------------------
# ModuleSubentryFlowHandler.async_step_user (add module)
# ---------------------------------------------------------------------------


async def test_subentry_add_module_success(
    hass: HomeAssistant, mock_controller
) -> None:
    """Adding a module subentry creates an entry with the right data."""
    entry = await _setup_loaded_entry(hass, mock_controller)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "user"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"module": "A", "name": "Living Room", "dimmable": True},
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"]["module"] == "A"
    assert result2["data"]["dimmable"] is True
    assert result2["data"]["outputs"] == {}


async def test_subentry_add_module_invalid_letter(
    hass: HomeAssistant, mock_controller
) -> None:
    """A non-letter module identifier is rejected."""
    entry = await _setup_loaded_entry(hass, mock_controller)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "user"},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"module": "1"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"]["module"] == "invalid_module"


async def test_subentry_add_module_duplicate(
    hass: HomeAssistant, mock_controller
) -> None:
    """Cannot add a module with the same letter as an existing one."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "user"},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"module": "A"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"]["module"] == "module_already_exists"


# ---------------------------------------------------------------------------
# ModuleSubentryFlowHandler.async_step_reconfigure (menu)
# ---------------------------------------------------------------------------


async def test_subentry_reconfigure_shows_menu(
    hass: HomeAssistant, mock_controller
) -> None:
    """Reconfigure shows a menu with add_light, add_shutter, add_switch, edit_module."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    assert result["type"] == FlowResultType.MENU
    assert "add_light" in result["menu_options"]
    assert "add_shutter" in result["menu_options"]
    assert "add_switch" in result["menu_options"]
    assert "edit_module" in result["menu_options"]
    # remove_output should NOT be present when outputs is empty
    assert "remove_output" not in result["menu_options"]


async def test_subentry_reconfigure_menu_has_remove_when_outputs_exist(
    hass: HomeAssistant, mock_controller
) -> None:
    """Remove output appears in menu when outputs are configured."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
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
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    assert result["type"] == FlowResultType.MENU
    assert "remove_output" in result["menu_options"]


# ---------------------------------------------------------------------------
# ModuleSubentryFlowHandler.async_step_add_light
# ---------------------------------------------------------------------------


async def test_subentry_add_light_success(hass: HomeAssistant, mock_controller) -> None:
    """Adding a light output updates the subentry data."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_light"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "add_light"

    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 1, "name": "Ceiling"},
    )
    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert "1" in sub.data["outputs"]
    assert sub.data["outputs"]["1"]["type"] == "light"
    assert sub.data["outputs"]["1"]["name"] == "Ceiling"


async def test_subentry_add_light_invalid_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Output number < 1 is rejected."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_light"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 0, "name": "Bad"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["output"] == "invalid_output"


async def test_subentry_add_light_output_too_high(
    hass: HomeAssistant, mock_controller
) -> None:
    """Output number above 12 is rejected."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_light"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 13, "name": "Bad"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["output"] == "invalid_output"


async def test_subentry_add_light_duplicate_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Cannot add a light on an already-occupied output."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
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
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_light"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 1, "name": "Duplicate"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["output"] == "duplicate_output"


# ---------------------------------------------------------------------------
# ModuleSubentryFlowHandler.async_step_add_shutter
# ---------------------------------------------------------------------------


async def test_subentry_add_shutter_success(
    hass: HomeAssistant, mock_controller
) -> None:
    """Adding a shutter pair updates the subentry data."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_shutter"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "add_shutter"

    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"up_output": 9, "down_output": 10, "name": "Blind"},
    )
    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert "9" in sub.data["outputs"]
    assert sub.data["outputs"]["9"]["type"] == "shutter"
    assert sub.data["outputs"]["9"]["down_output"] == 10


async def test_subentry_add_shutter_same_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Up and down on the same output number is rejected."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_shutter"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"up_output": 5, "down_output": 5, "name": "Bad"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["base"] == "same_output"


async def test_subentry_add_shutter_down_output_zero_rejected(
    hass: HomeAssistant, mock_controller
) -> None:
    """down_output below 1 is rejected."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_shutter"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"up_output": 1, "down_output": 0, "name": "Bad"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["down_output"] == "invalid_output"


async def test_subentry_add_shutter_down_output_too_high(
    hass: HomeAssistant, mock_controller
) -> None:
    """down_output above 12 is rejected."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_shutter"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"up_output": 1, "down_output": 13, "name": "Bad"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["down_output"] == "invalid_output"


async def test_subentry_add_shutter_up_output_occupied(
    hass: HomeAssistant, mock_controller
) -> None:
    """Cannot add a shutter if the up output is already occupied."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {
                    "module": "A",
                    "dimmable": False,
                    "outputs": {"9": {"type": "light", "name": "L9"}},
                },
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_shutter"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"up_output": 9, "down_output": 10, "name": "Blind"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["up_output"] == "duplicate_output"


async def test_subentry_add_shutter_down_output_occupied(
    hass: HomeAssistant, mock_controller
) -> None:
    """Cannot add a shutter if the down output is already occupied."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {
                    "module": "A",
                    "dimmable": False,
                    "outputs": {"10": {"type": "light", "name": "L10"}},
                },
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_shutter"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"up_output": 9, "down_output": 10, "name": "Blind"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["down_output"] == "duplicate_output"


# ---------------------------------------------------------------------------
# ModuleSubentryFlowHandler.async_step_add_switch
# ---------------------------------------------------------------------------


async def test_subentry_add_switch_success(
    hass: HomeAssistant, mock_controller
) -> None:
    """Adding a switch output updates the subentry data."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_switch"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "add_switch"

    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 1, "name": "Door Buzzer"},
    )
    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert "1" in sub.data["outputs"]
    assert sub.data["outputs"]["1"]["type"] == "switch"
    assert sub.data["outputs"]["1"]["name"] == "Door Buzzer"


async def test_subentry_add_switch_invalid_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Output number outside 1-12 is rejected."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_switch"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 0, "name": "Bad"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["output"] == "invalid_output"

    result4 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 13, "name": "Bad"},
    )
    assert result4["type"] == FlowResultType.FORM
    assert result4["errors"]["output"] == "invalid_output"


async def test_subentry_add_switch_duplicate_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Cannot add a switch on an already-occupied output."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
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
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "add_switch"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": 1, "name": "Duplicate"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["output"] == "duplicate_output"


# ---------------------------------------------------------------------------
# ModuleSubentryFlowHandler.async_step_remove_output
# ---------------------------------------------------------------------------


async def test_subentry_remove_output_success(
    hass: HomeAssistant, mock_controller
) -> None:
    """Removing an output removes it from the subentry data."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
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
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "remove_output"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "remove_output"

    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "1"},
    )
    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert "1" not in sub.data["outputs"]


async def test_subentry_remove_output_no_outputs_aborts(
    hass: HomeAssistant, mock_controller
) -> None:
    """Navigating to remove_output on a module with no outputs aborts.

    The reconfigure menu omits remove_output when outputs is empty, and the
    menu's next_step_id is schema-validated against menu_options, so this
    step can only be reached by invoking it directly on the flow handler,
    the same way a stale/cached form submission would.
    """
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    flow = ModuleSubentryFlowHandler()
    flow.hass = hass
    flow.handler = (entry.entry_id, SUBENTRY_TYPE_MODULE)
    flow.context = {"source": "reconfigure", "subentry_id": sub_id}

    result = await flow.async_step_remove_output(None)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_outputs_to_remove"


async def test_subentry_remove_output_invalid_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Submitting an output key that is not in the outputs dict is rejected.

    The remove_output form's SelectSelector only offers existing output
    keys, so this branch is only reachable by invoking the step directly,
    the same way a stale/cached form submission would.
    """
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
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
    sub_id = next(iter(entry.subentries))

    flow = ModuleSubentryFlowHandler()
    flow.hass = hass
    flow.handler = (entry.entry_id, SUBENTRY_TYPE_MODULE)
    flow.context = {"source": "reconfigure", "subentry_id": sub_id}

    result = await flow.async_step_remove_output({"output": "5"})

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["output"] == "invalid_output"


# ---------------------------------------------------------------------------
# ModuleSubentryFlowHandler.async_step_edit_module
# ---------------------------------------------------------------------------


async def test_subentry_edit_module_rename(
    hass: HomeAssistant, mock_controller
) -> None:
    """Editing a module can change the letter and name."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "edit_module"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "edit_module"

    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"module": "B", "name": "Kitchen", "dimmable": True},
    )
    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert sub.data["module"] == "B"
    assert sub.data["dimmable"] is True
    assert sub.title == "Kitchen"


async def test_subentry_edit_module_invalid_letter(
    hass: HomeAssistant, mock_controller
) -> None:
    """A non-alpha module letter is rejected."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            }
        ],
    )
    sub_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "edit_module"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"module": "1", "name": "", "dimmable": False},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["module"] == "invalid_module"


async def test_subentry_edit_module_duplicate_letter(
    hass: HomeAssistant, mock_controller
) -> None:
    """Cannot rename a module to a letter already used by another module."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module A",
                "unique_id": "module:A",
                "data": {"module": "A", "dimmable": False, "outputs": {}},
            },
            {
                "subentry_type": SUBENTRY_TYPE_MODULE,
                "title": "Module B",
                "unique_id": "module:B",
                "data": {"module": "B", "dimmable": False, "outputs": {}},
            },
        ],
    )
    # Get the subentry ID for module A
    sub_id = next(
        sid for sid, sub in entry.subentries.items() if sub.data["module"] == "A"
    )

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODULE),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "edit_module"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"module": "B", "name": "Same as B"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["module"] == "module_already_exists"


# ---------------------------------------------------------------------------
# USB config flow paths
# ---------------------------------------------------------------------------


async def test_usb_manual_flow_success(
    hass: HomeAssistant, mock_probe, mock_usb_ports
) -> None:
    """USB manual flow creates a config entry with USB connection type."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"connection_type": CONNECTION_TYPE_USB},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "usb_manual"

    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        user_input={CONF_DEVICE: MOCK_USB_DEVICE},
    )
    await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"]["connection_type"] == CONNECTION_TYPE_USB
    assert result3["data"][CONF_DEVICE] == MOCK_USB_DEVICE


async def test_usb_manual_flow_cannot_connect(
    hass: HomeAssistant, mock_usb_ports
) -> None:
    """USB manual flow shows error when probe fails."""
    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        side_effect=OSError("device not found"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"connection_type": CONNECTION_TYPE_USB},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            user_input={CONF_DEVICE: MOCK_USB_DEVICE},
        )

    assert result3["type"] == FlowResultType.FORM
    assert result3["step_id"] == "usb_manual"
    assert result3["errors"] == {"base": "cannot_connect"}


async def test_usb_discovery_flow(
    hass: HomeAssistant, mock_probe, mock_usb_ports
) -> None:
    """USB discovery sets the discovered device and routes to usb_manual."""
    from homeassistant.helpers.service_info.usb import UsbServiceInfo  # noqa: PLC0415

    discovery_info = UsbServiceInfo(
        device=MOCK_USB_DEVICE,
        vid="1234",
        pid="5678",
        serial_number="ABC123",
        manufacturer="MockCorp",
        description="Mock CAN Adapter",
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USB},
        data=discovery_info,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "usb_manual"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_DEVICE: MOCK_USB_DEVICE},
    )
    await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"]["connection_type"] == CONNECTION_TYPE_USB


async def test_reauth_usb_flow_success(
    hass: HomeAssistant, mock_probe, mock_usb_ports
) -> None:
    """USB reauth flow updates the entry on successful probe."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_USB,
            CONF_DEVICE: "/dev/serial/by-id/old-device",
        },
        unique_id=f"{CONNECTION_TYPE_USB}:/dev/serial/by-id/old-device",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_usb"

    with patch(
        "custom_components.dobiss_sx_evolution.async_setup_entry",
        return_value=True,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DEVICE: MOCK_USB_DEVICE},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data[CONF_DEVICE] == MOCK_USB_DEVICE


async def test_reauth_usb_flow_cannot_connect(
    hass: HomeAssistant, mock_usb_ports
) -> None:
    """USB reauth flow shows error on probe failure."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_USB,
            CONF_DEVICE: "/dev/serial/by-id/old-device",
        },
        unique_id=f"{CONNECTION_TYPE_USB}:/dev/serial/by-id/old-device",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        side_effect=OSError("device lost"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_usb"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_DEVICE: MOCK_USB_DEVICE},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}
