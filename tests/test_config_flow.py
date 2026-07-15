"""Tests for the DOBISS SX Evolution config flow."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
)

from .conftest import MOCK_CONFIG


async def test_user_flow_success(hass: HomeAssistant, mock_probe) -> None:
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


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_probe
) -> None:
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
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema
    }
    assert defaults["host"] == "10.0.0.5"
    assert defaults["port"] == 1234
    assert defaults["interface"] == "vcan0"
