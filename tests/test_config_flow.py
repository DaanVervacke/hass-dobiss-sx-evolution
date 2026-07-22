"""Tests for the DOBISS SX Evolution config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.config_flow import (
    DobissConfigFlow,
    ModuleImportSubentryFlowHandler,
    ModuleSubentryFlowHandler,
    MoodImportSubentryFlowHandler,
    _occupied_outputs_in_module,
    _validate_module,
)
from custom_components.dobiss_sx_evolution.const import (
    CONF_DEVICE,
    CONF_MASTER_DEVICE,
    CONF_MAX200_HOST,
    CONF_MODULE,
    CONNECTION_TYPE_SOCKETCAND,
    CONNECTION_TYPE_USB,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
    SUBENTRY_TYPE_MODULE_IMPORT,
    SUBENTRY_TYPE_MOOD,
    SUBENTRY_TYPE_MOOD_IMPORT,
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


async def test_socketcand_stores_max200_host(
    hass: HomeAssistant, mock_probe, mock_controller
) -> None:
    """socketcand flow stores max200_host in entry data when provided."""
    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient",
    ) as mock_tcp_cls:
        mock_tcp_cls.return_value.sync_clock = AsyncMock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            user_input={**MOCK_CONFIG, CONF_MAX200_HOST: "10.0.0.2"},
        )
        await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_MAX200_HOST] == "10.0.0.2"


async def test_socketcand_omits_max200_host_when_blank(
    hass: HomeAssistant, mock_probe, mock_controller
) -> None:
    """socketcand flow does not store max200_host when left blank."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND}
    )
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], user_input=MOCK_CONFIG
    )
    await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_MAX200_HOST not in result3["data"]


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

    Complements test_user_flow_cannot_connect, which covers the OSError branch.
    This exercises the catch-all Exception branch in async_step_socketcand.
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


async def test_reauth_socketcand_updates_unique_id(
    hass: HomeAssistant, mock_probe
) -> None:
    """Reauth with a new host updates the entry's unique_id to match."""
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
    expected_unique_id = (
        f"{CONNECTION_TYPE_SOCKETCAND}:192.168.1.99"
        f":{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}"
    )
    assert entry.unique_id == expected_unique_id


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


async def test_reconfigure_socketcand_aborts_on_collision(
    hass: HomeAssistant, mock_probe
) -> None:
    """Reconfigure aborts when the new params collide with another entry."""
    other_config = {**MOCK_CONFIG, "host": "10.0.0.42"}
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **other_config},
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{other_config['host']}:{other_config['port']}/{other_config['interface']}",
    )
    other_entry.add_to_hass(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"connection_type": CONNECTION_TYPE_SOCKETCAND},
    )

    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], user_input=other_config
    )

    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "already_configured"
    assert entry.data["host"] == MOCK_CONFIG["host"]


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


async def test_reconfigure_usb_aborts_on_collision(
    hass: HomeAssistant, mock_probe, mock_usb_ports
) -> None:
    """Reconfigure to USB aborts when the device collides with another entry."""
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_USB,
            CONF_DEVICE: MOCK_USB_DEVICE,
        },
        unique_id=f"{CONNECTION_TYPE_USB}:{MOCK_USB_DEVICE}",
    )
    other_entry.add_to_hass(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"connection_type": CONNECTION_TYPE_SOCKETCAND, **MOCK_CONFIG},
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"connection_type": CONNECTION_TYPE_USB},
    )

    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"], user_input={CONF_DEVICE: MOCK_USB_DEVICE}
    )

    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "already_configured"
    assert entry.data["connection_type"] == CONNECTION_TYPE_SOCKETCAND


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


async def test_subentry_add_shutter_up_output_zero_rejected(
    hass: HomeAssistant, mock_controller
) -> None:
    """up_output below 1 is rejected."""
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
        user_input={"up_output": 0, "down_output": 2, "name": "Bad"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["up_output"] == "invalid_output"


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
# ModuleSubentryFlowHandler.async_step_edit_output
# ---------------------------------------------------------------------------


async def test_subentry_edit_output_light_to_switch(
    hass: HomeAssistant, mock_controller
) -> None:
    """Changing a light to a switch updates the type and preserves the name."""
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
                    "outputs": {"3": {"type": "light", "name": "Hall"}},
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
        user_input={"next_step_id": "edit_output"},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "edit_output"

    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "3"},
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["step_id"] == "edit_output_type"

    result4 = await hass.config_entries.subentries.async_configure(
        result3["flow_id"],
        user_input={"type": "switch"},
    )
    assert result4["type"] == FlowResultType.ABORT
    assert result4["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["3"]["type"] == "switch"
    assert sub.data["outputs"]["3"]["name"] == "Hall"
    assert "down_output" not in sub.data["outputs"]["3"]


async def test_subentry_edit_output_light_to_shutter(
    hass: HomeAssistant, mock_controller
) -> None:
    """Changing a light to a shutter stores the down_output."""
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
                    "outputs": {"1": {"type": "light", "name": "Blind"}},
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
        user_input={"next_step_id": "edit_output"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "1"},
    )
    result4 = await hass.config_entries.subentries.async_configure(
        result3["flow_id"],
        user_input={"type": "shutter"},
    )
    assert result4["type"] == FlowResultType.FORM
    assert result4["step_id"] == "edit_output_down"

    result5 = await hass.config_entries.subentries.async_configure(
        result4["flow_id"],
        user_input={"down_output": 2},
    )
    assert result5["type"] == FlowResultType.ABORT
    assert result5["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["1"]["type"] == "shutter"
    assert sub.data["outputs"]["1"]["down_output"] == 2
    assert sub.data["outputs"]["1"]["name"] == "Blind"


async def test_subentry_edit_output_shutter_to_light(
    hass: HomeAssistant, mock_controller
) -> None:
    """Changing a shutter back to a light drops down_output."""
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
                    "outputs": {
                        "5": {
                            "type": "shutter",
                            "down_output": 6,
                            "name": "Screen",
                        }
                    },
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
        user_input={"next_step_id": "edit_output"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "5"},
    )
    result4 = await hass.config_entries.subentries.async_configure(
        result3["flow_id"],
        user_input={"type": "light"},
    )
    assert result4["type"] == FlowResultType.ABORT
    assert result4["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["5"]["type"] == "light"
    assert "down_output" not in sub.data["outputs"]["5"]


async def test_subentry_edit_output_shutter_invalid_down_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Changing to shutter with an invalid down_output shows an error."""
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
                    "outputs": {
                        "1": {"type": "light", "name": "L1"},
                        "3": {"type": "light", "name": "L3"},
                    },
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
        user_input={"next_step_id": "edit_output"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "1"},
    )

    # pick shutter to get to the down_output step
    result4 = await hass.config_entries.subentries.async_configure(
        result3["flow_id"],
        user_input={"type": "shutter"},
    )
    assert result4["type"] == FlowResultType.FORM
    assert result4["step_id"] == "edit_output_down"

    # same output as up
    result5 = await hass.config_entries.subentries.async_configure(
        result4["flow_id"],
        user_input={"down_output": 1},
    )
    assert result5["type"] == FlowResultType.FORM
    assert result5["errors"]["base"] == "same_output"

    # out of range (below 1)
    result6 = await hass.config_entries.subentries.async_configure(
        result5["flow_id"],
        user_input={"down_output": 0},
    )
    assert result6["type"] == FlowResultType.FORM
    assert result6["errors"]["down_output"] == "invalid_output"

    # out of range (above OUTPUTS_PER_MODULE)
    result_high = await hass.config_entries.subentries.async_configure(
        result6["flow_id"],
        user_input={"down_output": 13},
    )
    assert result_high["type"] == FlowResultType.FORM
    assert result_high["errors"]["down_output"] == "invalid_output"

    # occupied by another output -- rejected, existing entry untouched
    result7 = await hass.config_entries.subentries.async_configure(
        result_high["flow_id"],
        user_input={"down_output": 3},
    )
    assert result7["type"] == FlowResultType.FORM
    assert result7["errors"]["down_output"] == "duplicate_output"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["3"] == {"type": "light", "name": "L3"}

    # finish with a valid, unclaimed down_output
    result8 = await hass.config_entries.subentries.async_configure(
        result7["flow_id"],
        user_input={"down_output": 5},
    )
    assert result8["type"] == FlowResultType.ABORT
    assert result8["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["1"]["type"] == "shutter"
    assert sub.data["outputs"]["1"]["down_output"] == 5
    assert sub.data["outputs"]["3"] == {"type": "light", "name": "L3"}


async def test_subentry_edit_output_shutter_down_collides_with_shutter_down(
    hass: HomeAssistant, mock_controller
) -> None:
    """Choosing a down_output already used as another shutter's down errors."""
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
                    "outputs": {
                        "1": {"type": "light", "name": "L1"},
                        "2": {
                            "type": "shutter",
                            "down_output": 3,
                            "name": "Blind",
                        },
                        "4": {"type": "light", "name": "L4"},
                    },
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
        user_input={"next_step_id": "edit_output"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "4"},
    )
    result4 = await hass.config_entries.subentries.async_configure(
        result3["flow_id"],
        user_input={"type": "shutter"},
    )
    assert result4["type"] == FlowResultType.FORM
    assert result4["step_id"] == "edit_output_down"

    # down_output=3 is not a dict key, but it's shutter "2"'s down_output.
    result5 = await hass.config_entries.subentries.async_configure(
        result4["flow_id"],
        user_input={"down_output": 3},
    )
    assert result5["type"] == FlowResultType.FORM
    assert result5["errors"]["down_output"] == "duplicate_output"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["4"] == {"type": "light", "name": "L4"}


async def test_subentry_edit_output_shutter_reselect_own_down_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Re-selecting a shutter's existing own down_output succeeds."""
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
                    "outputs": {
                        "1": {
                            "type": "shutter",
                            "down_output": 2,
                            "name": "Blind",
                        },
                    },
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
        user_input={"next_step_id": "edit_output"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "1"},
    )
    result4 = await hass.config_entries.subentries.async_configure(
        result3["flow_id"],
        user_input={"type": "shutter"},
    )
    assert result4["type"] == FlowResultType.FORM
    assert result4["step_id"] == "edit_output_down"

    result5 = await hass.config_entries.subentries.async_configure(
        result4["flow_id"],
        user_input={"down_output": 2},
    )
    assert result5["type"] == FlowResultType.ABORT
    assert result5["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["1"]["down_output"] == 2


async def test_subentry_edit_output_shutter_change_down_output(
    hass: HomeAssistant, mock_controller
) -> None:
    """Changing a shutter's down_output frees the old one from occupied."""
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
                    "outputs": {
                        "1": {
                            "type": "shutter",
                            "down_output": 2,
                            "name": "Blind",
                        },
                        "3": {"type": "light", "name": "L3"},
                    },
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
        user_input={"next_step_id": "edit_output"},
    )
    result3 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"],
        user_input={"output": "1"},
    )
    # Pick shutter to get to down_output step
    result4 = await hass.config_entries.subentries.async_configure(
        result3["flow_id"],
        user_input={"type": "shutter"},
    )
    assert result4["type"] == FlowResultType.FORM
    assert result4["step_id"] == "edit_output_down"

    # Change down_output from 2 to 4 (2 was occupied, now freed)
    result5 = await hass.config_entries.subentries.async_configure(
        result4["flow_id"],
        user_input={"down_output": 4},
    )
    assert result5["type"] == FlowResultType.ABORT
    assert result5["reason"] == "reconfigure_successful"

    sub = entry.subentries[sub_id]
    assert sub.data["outputs"]["1"]["down_output"] == 4


async def test_subentry_edit_output_no_outputs_aborts(
    hass: HomeAssistant, mock_controller
) -> None:
    """Navigating to edit_output on a module with no outputs aborts."""
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

    result = await flow.async_step_edit_output(None)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_outputs_to_edit"


async def test_subentry_edit_output_menu_visible(
    hass: HomeAssistant, mock_controller
) -> None:
    """edit_output appears in the reconfigure menu when outputs exist."""
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
    assert "edit_output" in result["menu_options"]


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


async def test_reauth_usb_updates_unique_id(
    hass: HomeAssistant, mock_probe, mock_usb_ports
) -> None:
    """USB reauth with a new device path updates the entry's unique_id."""
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
    assert entry.unique_id == f"{CONNECTION_TYPE_USB}:{MOCK_USB_DEVICE}"


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


# ---------------------------------------------------------------------------
# ModuleImportSubentryFlowHandler
# ---------------------------------------------------------------------------

MOCK_MASTER_DEVICE = "/dev/serial/by-id/usb-mock-master"

MOCK_CONFIG_WITH_MASTER = {
    **MOCK_CONFIG,
    CONF_MASTER_DEVICE: MOCK_MASTER_DEVICE,
}


def _make_config_response(*letters_and_slots: tuple[str, int]) -> bytes:
    """Build a 36-byte ConfigVars response with the given modules active."""
    data = bytearray(36)
    for letter, slot in letters_and_slots:
        data[slot] = ord(letter)
    return bytes(data)


def _make_output_name_response(name: str) -> bytes:
    """Build a 32-byte UitgangVars response with the given output name."""
    data = bytearray(32)
    encoded = name.encode("ascii")
    data[: len(encoded)] = encoded
    return bytes(data)


def _make_unconfigured_output_response() -> bytes:
    """Build a 32-byte response for an unconfigured output (byte 1 = 0xFF)."""
    data = bytearray(32)
    data[1] = 0xFF
    return bytes(data)


@pytest.fixture
def mock_coordinator_serial():
    """Patch Max200SerialClient in the coordinator to prevent real serial I/O."""
    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200SerialClient",
    ):
        yield


@pytest.fixture
def mock_coordinator_tcp():
    """Patch Max200TcpClient in the coordinator to prevent real TCP I/O."""
    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient",
    ) as mock_cls:
        mock_cls.return_value.sync_clock = AsyncMock()
        yield


async def _setup_entry_with_master(
    hass: HomeAssistant,
    mock_controller,
    subentries_data: list[dict] | None = None,
) -> MockConfigEntry:
    """Create a config entry with master_device set."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_SOCKETCAND,
            **MOCK_CONFIG_WITH_MASTER,
        },
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
        version=1,
        subentries_data=subentries_data or [],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


MOCK_MAX200_HOST = "10.0.0.2"

MOCK_CONFIG_WITH_MAX200_HOST = {
    **MOCK_CONFIG,
    CONF_MAX200_HOST: MOCK_MAX200_HOST,
}


async def _setup_entry_with_max200_host(
    hass: HomeAssistant,
    mock_controller,
    subentries_data: list[dict] | None = None,
) -> MockConfigEntry:
    """Create a config entry with max200_host set (no master_device)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "connection_type": CONNECTION_TYPE_SOCKETCAND,
            **MOCK_CONFIG_WITH_MAX200_HOST,
        },
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
        version=1,
        subentries_data=subentries_data or [],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_import_subentry_type_hidden_without_master(
    hass: HomeAssistant, mock_controller
) -> None:
    """module_import and mood_import are not offered without a Max200 link."""
    entry = await _setup_loaded_entry(hass, mock_controller)
    types = DobissConfigFlow.async_get_supported_subentry_types(entry)
    assert SUBENTRY_TYPE_MODULE_IMPORT not in types
    assert SUBENTRY_TYPE_MOOD_IMPORT not in types
    assert SUBENTRY_TYPE_MODULE in types
    assert list(types.keys()) == [SUBENTRY_TYPE_MODULE, SUBENTRY_TYPE_MOOD]


async def test_import_subentry_type_shown_with_master(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """module_import/mood_import are offered when master_device is configured."""
    entry = await _setup_entry_with_master(hass, mock_controller)
    types = DobissConfigFlow.async_get_supported_subentry_types(entry)
    assert SUBENTRY_TYPE_MODULE_IMPORT in types
    assert SUBENTRY_TYPE_MOOD_IMPORT in types
    assert list(types.keys()) == [
        SUBENTRY_TYPE_MODULE,
        SUBENTRY_TYPE_MODULE_IMPORT,
        SUBENTRY_TYPE_MOOD,
        SUBENTRY_TYPE_MOOD_IMPORT,
    ]


async def test_import_subentry_type_shown_with_max200_host(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """module_import/mood_import are offered when max200_host is configured."""
    entry = await _setup_entry_with_max200_host(hass, mock_controller)
    types = DobissConfigFlow.async_get_supported_subentry_types(entry)
    assert SUBENTRY_TYPE_MODULE_IMPORT in types
    assert SUBENTRY_TYPE_MOOD_IMPORT in types
    assert list(types.keys()) == [
        SUBENTRY_TYPE_MODULE,
        SUBENTRY_TYPE_MODULE_IMPORT,
        SUBENTRY_TYPE_MOOD,
        SUBENTRY_TYPE_MOOD_IMPORT,
    ]


async def test_import_creates_subentries(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """Import downloads config and creates module subentries with output names."""
    entry = await _setup_entry_with_master(hass, mock_controller)

    modules = [("A", 0), ("B", 1)]
    module_names = {
        0: {0: "Kitchen", 2: "Living"},
        1: {0: "Bedroom"},
    }

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = MagicMock(return_value=modules)
        mock_client.download_module_output_names = MagicMock(
            side_effect=lambda mod_idx, count: module_names.get(mod_idx, {})
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    assert result["description_placeholders"]["count"] == "2"

    module_subentries = {
        sub.data[CONF_MODULE]: sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MODULE
    }
    assert "A" in module_subentries
    assert "B" in module_subentries

    a_outputs = module_subentries["A"].data["outputs"]
    assert "1" in a_outputs
    assert a_outputs["1"]["name"] == "Kitchen"
    assert a_outputs["1"]["type"] == "light"
    assert "3" in a_outputs
    assert a_outputs["3"]["name"] == "Living"

    b_outputs = module_subentries["B"].data["outputs"]
    assert "1" in b_outputs
    assert b_outputs["1"]["name"] == "Bedroom"


async def test_import_via_tcp_creates_subentries(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """Import downloads config over TCP and creates module subentries."""
    entry = await _setup_entry_with_max200_host(hass, mock_controller)

    modules = [("A", 0)]
    module_names = {0: {0: "Kitchen"}}

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200TcpClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = AsyncMock(return_value=modules)
        mock_client.download_module_output_names = AsyncMock(
            side_effect=lambda mod_idx, count: module_names.get(mod_idx, {})
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    assert result["description_placeholders"]["count"] == "1"

    module_subentries = {
        sub.data[CONF_MODULE]: sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MODULE
    }
    assert "A" in module_subentries
    a_outputs = module_subentries["A"].data["outputs"]
    assert a_outputs["1"]["name"] == "Kitchen"


async def test_import_via_tcp_failure_aborts(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """TCP connection failure during import aborts with import_failed."""
    entry = await _setup_entry_with_max200_host(hass, mock_controller)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200TcpClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = AsyncMock(
            side_effect=OSError("connection refused")
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_failed"


async def test_import_via_tcp_continues_on_name_failure(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """Import continues when download_module_output_names fails over TCP."""
    entry = await _setup_entry_with_max200_host(hass, mock_controller)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200TcpClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = AsyncMock(return_value=[("A", 0)])
        mock_client.download_module_output_names = AsyncMock(
            side_effect=ConnectionError("boom")
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"

    module_subs = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MODULE
    ]
    assert len(module_subs) == 1
    outputs = module_subs[0].data["outputs"]
    assert outputs == {}  # batch download failed, module created with no outputs


async def test_import_prefers_tcp_over_serial(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """When both max200_host and master_device are set, TCP is used for import."""
    entry_data = {
        "connection_type": CONNECTION_TYPE_SOCKETCAND,
        **MOCK_CONFIG,
        CONF_MAX200_HOST: MOCK_MAX200_HOST,
        CONF_MASTER_DEVICE: MOCK_MASTER_DEVICE,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
        version=1,
        subentries_data=[],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with (
        patch(
            "custom_components.dobiss_sx_evolution.config_flow.Max200TcpClient"
        ) as mock_tcp_cls,
        patch(
            "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
        ) as mock_serial_cls,
    ):
        mock_tcp = mock_tcp_cls.return_value
        mock_tcp.download_config = AsyncMock(return_value=[("A", 0)])
        mock_tcp.download_module_output_names = AsyncMock(return_value={})

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    mock_tcp.download_config.assert_awaited_once()
    mock_serial_cls.assert_not_called()


async def test_import_continues_on_output_download_failure(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """Import continues when download_module_output_names fails."""
    entry = await _setup_entry_with_master(hass, mock_controller)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = MagicMock(return_value=[("A", 0)])
        mock_client.download_module_output_names = MagicMock(
            side_effect=ConnectionError("Serial timeout")
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"

    module_subs = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MODULE
    ]
    assert len(module_subs) == 1
    outputs = module_subs[0].data["outputs"]
    assert outputs == {}  # batch download failed, module created with no outputs


async def test_import_skips_existing_modules(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """Modules that already have a subentry are skipped."""
    entry = await _setup_entry_with_master(
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

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = MagicMock(return_value=[("A", 0), ("B", 1)])
        mock_client.download_module_output_names = MagicMock(return_value={})

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    assert result["description_placeholders"]["count"] == "1"

    module_letters = {
        sub.data[CONF_MODULE]
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MODULE
    }
    assert module_letters == {"A", "B"}


async def test_import_no_new_modules(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """When all modules already exist, abort with no_new_modules."""
    entry = await _setup_entry_with_master(
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

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = MagicMock(return_value=[("A", 0)])

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_new_modules"


async def test_import_serial_failure(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """Serial connection failure aborts with import_failed."""
    entry = await _setup_entry_with_master(hass, mock_controller)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_config = MagicMock(
            side_effect=ConnectionError("device gone")
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_failed"


async def test_import_aborts_without_max200_connection(
    hass: HomeAssistant, mock_controller
) -> None:
    """Import flow aborts when neither master_device nor max200_host is configured.

    The import subentry type is hidden from async_get_supported_subentry_types
    when neither is configured, so it cannot be reached through the normal
    subentries.async_init flow manager (that raises UnknownHandler). The
    no_max200_connection guard is a defensive check inside the handler
    itself, so it is exercised by invoking the handler's step method directly.
    """
    entry = await _setup_loaded_entry(hass, mock_controller)
    assert CONF_MASTER_DEVICE not in entry.data
    assert CONF_MAX200_HOST not in entry.data

    flow = ModuleImportSubentryFlowHandler()
    flow.hass = hass
    flow.handler = (entry.entry_id, SUBENTRY_TYPE_MODULE_IMPORT)

    result = await flow.async_step_user(None)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_max200_connection"


# ---------------------------------------------------------------------------
# Mood subentry flow
# ---------------------------------------------------------------------------


async def test_mood_subentry_type_shown(hass: HomeAssistant, mock_controller) -> None:
    """Mood subentry type is always available (no Max200 required)."""
    entry = await _setup_loaded_entry(hass, mock_controller)
    types = DobissConfigFlow.async_get_supported_subentry_types(entry)
    assert SUBENTRY_TYPE_MOOD in types


async def test_add_mood_subentry(hass: HomeAssistant, mock_controller) -> None:
    """Adding a mood creates a subentry with the correct data."""
    entry = await _setup_loaded_entry(hass, mock_controller)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MOOD),
        context={"source": "user"},
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"mood_number": 5, "name": "Night Mode"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Night Mode"

    mood_subs = [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MOOD
    ]
    assert len(mood_subs) == 1
    assert mood_subs[0].data["mood_number"] == 5


async def test_add_mood_default_name(hass: HomeAssistant, mock_controller) -> None:
    """A mood without a name gets a default title."""
    entry = await _setup_loaded_entry(hass, mock_controller)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MOOD),
        context={"source": "user"},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"mood_number": 12, "name": ""},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mood 12"


async def test_add_mood_duplicate(hass: HomeAssistant, mock_controller) -> None:
    """Adding a mood with an already-used number shows an error."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MOOD,
                "title": "Existing",
                "unique_id": "mood:3",
                "data": {"mood_number": 3},
            }
        ],
    )

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MOOD),
        context={"source": "user"},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"mood_number": 3, "name": "Duplicate"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["mood_number"] == "mood_already_exists"


async def test_mood_reconfigure_rename(hass: HomeAssistant, mock_controller) -> None:
    """Reconfiguring a mood updates its title."""
    entry = await _setup_loaded_entry(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MOOD,
                "title": "Mood 5",
                "unique_id": "mood:5",
                "data": {"mood_number": 5},
            }
        ],
    )
    sub_id = next(
        sid
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_TYPE_MOOD
    )

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MOOD),
        context={"source": "reconfigure", "subentry_id": sub_id},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={"name": "Evening Ambiance"},
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert entry.subentries[sub_id].title == "Evening Ambiance"


# ---------------------------------------------------------------------------
# MoodImportSubentryFlowHandler
# ---------------------------------------------------------------------------


async def test_mood_import_creates_subentries(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """Import downloads mood names and creates mood subentries."""
    entry = await _setup_entry_with_master(hass, mock_controller)

    mood_names = {5: "Gaan slapen", 12: "Alles uit"}

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_mood_names = MagicMock(return_value=mood_names)

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    assert result["description_placeholders"]["count"] == "2"

    mood_subentries = {
        sub.data["mood_number"]: sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MOOD
    }
    assert mood_subentries[5].title == "Gaan slapen"
    assert mood_subentries[12].title == "Alles uit"


async def test_mood_import_via_tcp_creates_subentries(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """Import downloads mood names over TCP and creates mood subentries."""
    entry = await _setup_entry_with_max200_host(hass, mock_controller)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200TcpClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_mood_names = AsyncMock(return_value={3: "Thuiskomen"})

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    assert result["description_placeholders"]["count"] == "1"

    mood_subentries = {
        sub.data["mood_number"]: sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MOOD
    }
    assert mood_subentries[3].title == "Thuiskomen"


async def test_mood_import_prefers_tcp_over_serial(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """When both max200_host and master_device are set, TCP is used for import."""
    entry_data = {
        "connection_type": CONNECTION_TYPE_SOCKETCAND,
        **MOCK_CONFIG,
        CONF_MAX200_HOST: MOCK_MAX200_HOST,
        CONF_MASTER_DEVICE: MOCK_MASTER_DEVICE,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
        version=1,
        subentries_data=[],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with (
        patch(
            "custom_components.dobiss_sx_evolution.config_flow.Max200TcpClient"
        ) as mock_tcp_cls,
        patch(
            "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
        ) as mock_serial_cls,
    ):
        mock_tcp = mock_tcp_cls.return_value
        mock_tcp.download_mood_names = AsyncMock(return_value={0: "Alles uit"})

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    mock_tcp.download_mood_names.assert_awaited_once()
    mock_serial_cls.assert_not_called()


async def test_mood_import_skips_existing_moods(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """Moods that already have a subentry are skipped."""
    entry = await _setup_entry_with_master(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MOOD,
                "title": "Existing",
                "unique_id": "mood:3",
                "data": {"mood_number": 3},
            }
        ],
    )

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_mood_names = MagicMock(
            return_value={3: "Duplicate", 4: "New Mood"}
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_successful"
    assert result["description_placeholders"]["count"] == "1"

    mood_numbers = {
        sub.data["mood_number"]
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MOOD
    }
    assert mood_numbers == {3, 4}


async def test_mood_import_no_new_moods(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """When all named moods already exist, abort with no_new_moods."""
    entry = await _setup_entry_with_master(
        hass,
        mock_controller,
        subentries_data=[
            {
                "subentry_type": SUBENTRY_TYPE_MOOD,
                "title": "Existing",
                "unique_id": "mood:3",
                "data": {"mood_number": 3},
            }
        ],
    )

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_mood_names = MagicMock(return_value={3: "Duplicate"})

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_new_moods"


async def test_mood_import_serial_failure(
    hass: HomeAssistant, mock_controller, mock_coordinator_serial
) -> None:
    """Serial connection failure aborts with import_failed."""
    entry = await _setup_entry_with_master(hass, mock_controller)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200SerialClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_mood_names = MagicMock(
            side_effect=ConnectionError("device gone")
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_failed"


async def test_mood_import_tcp_failure(
    hass: HomeAssistant, mock_controller, mock_coordinator_tcp
) -> None:
    """TCP connection failure during import aborts with import_failed."""
    entry = await _setup_entry_with_max200_host(hass, mock_controller)

    with patch(
        "custom_components.dobiss_sx_evolution.config_flow.Max200TcpClient"
    ) as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.download_mood_names = AsyncMock(
            side_effect=OSError("connection refused")
        )

        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT),
            context={"source": "user"},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "import_failed"


async def test_mood_import_aborts_without_max200_connection(
    hass: HomeAssistant, mock_controller
) -> None:
    """Import flow aborts when neither master_device nor max200_host is configured.

    mood_import is hidden from async_get_supported_subentry_types when
    neither is configured, so it cannot be reached through the normal
    subentries.async_init flow manager (that raises UnknownHandler). The
    no_max200_connection guard is a defensive check inside the handler
    itself, so it is exercised by invoking the handler's step method
    directly, mirroring the module_import equivalent.
    """
    entry = await _setup_loaded_entry(hass, mock_controller)
    assert CONF_MASTER_DEVICE not in entry.data
    assert CONF_MAX200_HOST not in entry.data

    flow = MoodImportSubentryFlowHandler()
    flow.hass = hass
    flow.handler = (entry.entry_id, SUBENTRY_TYPE_MOOD_IMPORT)

    result = await flow.async_step_user(None)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_max200_connection"
