"""Tests for the dobiss_sx_evolution integration setup and unload."""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.dobiss_sx_evolution.const import DOMAIN

from .conftest import MOCK_CONFIG


async def test_setup_entry(hass: HomeAssistant, mock_controller) -> None:
    """Entry loads successfully and reaches LOADED state."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, title="DOBISS", version=1)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert mock_controller.async_setup.called


async def test_unload_entry(hass: HomeAssistant, mock_controller) -> None:
    """Entry loads, then unloads cleanly to NOT_LOADED state."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, title="DOBISS", version=1)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert mock_controller.async_shutdown.called


async def test_setup_entry_not_ready(hass: HomeAssistant, mock_controller) -> None:
    """OSError from controller.async_setup yields SETUP_RETRY (ConfigEntryNotReady)."""
    mock_controller.async_setup.side_effect = OSError("No such device")

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, title="DOBISS", version=1)
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
