"""Tests for the DOBISS SX Evolution config flow."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import DOMAIN

from .conftest import MOCK_CONFIG


async def test_user_flow_success(hass: HomeAssistant, mock_probe) -> None:
    """Happy-path user flow creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=MOCK_CONFIG
    )
    await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"] == MOCK_CONFIG
    assert mock_probe.called


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """User flow re-renders the form with cannot_connect when probe raises."""
    with patch(
        "custom_components.dobiss_sx_evolution.config_flow._probe_bus_sync",
        side_effect=OSError("boom"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=MOCK_CONFIG,
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_probe
) -> None:
    """Second entry with the same host/port/interface aborts."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=f"{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=MOCK_CONFIG,
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_success(hass: HomeAssistant, mock_probe) -> None:
    """Reauth flow with a working probe updates and reloads the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG,
        unique_id=f"{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
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
    assert result["step_id"] == "reauth_confirm"

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
        data=MOCK_CONFIG,
        unique_id=f"{MOCK_CONFIG['host']}:{MOCK_CONFIG['port']}/{MOCK_CONFIG['interface']}",
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
        assert result["step_id"] == "reauth_confirm"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=MOCK_CONFIG
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}
