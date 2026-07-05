"""Tests for the dobiss_sx_evolution integration setup and unload."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from custom_components.dobiss_sx_evolution.__init__ import (  # noqa: PLC0415
    _connection_key,
    _make_reload_listener,
    _module_config,
)

from .conftest import MOCK_CONFIG


# Helper to create config entry data with connection type
def _make_entry_data(**extra) -> dict:
    """Create entry data with connection_type."""
    return {
        "connection_type": CONNECTION_TYPE_SOCKETCAND,
        **MOCK_CONFIG,
        **extra,
    }


def _make_subentry_data(module: str = "A", outputs: dict | None = None) -> dict:
    """Return a minimal subentry_data dict for one module."""
    return {
        "subentry_type": SUBENTRY_TYPE_MODULE,
        "title": f"Module {module}",
        "unique_id": f"module:{module}",
        "data": {
            "module": module,
            "dimmable": False,
            "outputs": outputs or {},
        },
    }


async def test_setup_entry(hass: HomeAssistant, mock_controller) -> None:
    """Entry loads successfully and reaches LOADED state."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert mock_controller.async_setup.called


async def test_unload_entry(hass: HomeAssistant, mock_controller) -> None:
    """Entry loads, then unloads cleanly to NOT_LOADED state."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
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

    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# Smart reload listener tests
# ---------------------------------------------------------------------------


async def test_reload_listener_output_only_change_skips_full_reload(
    hass: HomeAssistant, mock_controller
) -> None:
    """Adding an output to an existing module must NOT trigger a full entry reload.

    The listener should unload/re-forward platforms but leave the bus intact.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[_make_subentry_data("A", {"1": {"type": "light", "name": "L1"}})],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    reload_calls: list[str] = []

    async def _fake_reload(entry_id: str) -> None:
        reload_calls.append(entry_id)

    # Simulate adding a second output to the same module (output-only change).
    updated_entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[
            _make_subentry_data(
                "A",
                {
                    "1": {"type": "light", "name": "L1"},
                    "2": {"type": "light", "name": "L2"},
                },
            )
        ],
    )
    updated_entry.add_to_hass(hass)
    # Give runtime_data (coordinator) so the listener sees it.
    updated_entry.runtime_data = entry.runtime_data

    listener = _make_reload_listener(entry)

    with patch(
        "custom_components.dobiss_sx_evolution.__init__.hass",
        create=True,
    ):
        with patch.object(
            hass.config_entries, "async_reload", side_effect=_fake_reload
        ):
            unload_calls: list = []
            forward_calls: list = []

            with (
                patch.object(
                    hass.config_entries,
                    "async_unload_platforms",
                    new=AsyncMock(return_value=True, side_effect=lambda e, p: unload_calls.append(1) or True),
                ),
                patch.object(
                    hass.config_entries,
                    "async_forward_entry_setups",
                    new=AsyncMock(side_effect=lambda e, p: forward_calls.append(1)),
                ),
            ):
                await listener(hass, updated_entry)

    # Full reload must NOT have been called.
    assert reload_calls == [], (
        f"Expected no full reload for output-only change, got: {reload_calls}"
    )
    # Platform unload and re-forward must have been called.
    assert unload_calls, "Expected async_unload_platforms to be called"
    assert forward_calls, "Expected async_forward_entry_setups to be called"
    # The fast path must refresh the state cache before recreating entities,
    # otherwise a newly-added output would render as off until a wall-switch
    # event or user-invoked refresh service arrives.
    assert mock_controller.async_refresh_and_settle.await_count == 1, (
        "Expected async_refresh_and_settle to be awaited once on the fast path"
    )


async def test_reload_listener_new_module_triggers_full_reload(
    hass: HomeAssistant, mock_controller
) -> None:
    """Adding a brand-new module subentry must trigger a full entry reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[_make_subentry_data("A")],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    reload_calls: list[str] = []

    # Simulate adding module B (new module letter → full reload required).
    updated_entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[_make_subentry_data("A"), _make_subentry_data("B")],
    )
    updated_entry.add_to_hass(hass)
    updated_entry.runtime_data = entry.runtime_data

    listener = _make_reload_listener(entry)

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(side_effect=lambda eid: reload_calls.append(eid))
    ):
        await listener(hass, updated_entry)

    assert reload_calls == [updated_entry.entry_id], (
        f"Expected full reload when module set changes, got: {reload_calls}"
    )


async def test_reload_listener_connection_change_triggers_full_reload(
    hass: HomeAssistant, mock_controller
) -> None:
    """Changing the connection host must trigger a full entry reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    reload_calls: list[str] = []

    # Simulate changing the host (connection param change → full reload).
    updated_entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(host="192.168.1.99"),
        title="DOBISS",
        version=1,
    )
    updated_entry.add_to_hass(hass)
    updated_entry.runtime_data = entry.runtime_data

    listener = _make_reload_listener(entry)

    with patch.object(
        hass.config_entries, "async_reload", new=AsyncMock(side_effect=lambda eid: reload_calls.append(eid))
    ):
        await listener(hass, updated_entry)

    assert reload_calls == [updated_entry.entry_id], (
        f"Expected full reload on connection change, got: {reload_calls}"
    )


def test_connection_key_differs_on_host_change() -> None:
    """_connection_key returns different tuples when host changes."""
    e1 = MockConfigEntry(domain=DOMAIN, data=_make_entry_data(host="1.2.3.4"))
    e2 = MockConfigEntry(domain=DOMAIN, data=_make_entry_data(host="9.9.9.9"))
    assert _connection_key(e1) != _connection_key(e2)


def test_module_config_returns_pairs() -> None:
    """_module_config returns (letter, dimmable) pairs for each module."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        subentries_data=[_make_subentry_data("A"), _make_subentry_data("B")],
    )
    assert _module_config(entry) == frozenset({("A", False), ("B", False)})


async def test_reload_listener_title_rename_updates_device_registry(
    hass: HomeAssistant, mock_controller
) -> None:
    """Renaming a module subentry must update the module device name via the fast path.

    The fast path recreates entities but does not touch devices by default.
    The listener additionally pushes the new title to the device registry so
    the module device name stays in sync without a full bus reconnect.
    """
    from homeassistant.helpers import device_registry as dr

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[_make_subentry_data("A")],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    identifier = (DOMAIN, f"{entry.entry_id}_module_A")
    device = device_registry.async_get_device(identifiers={identifier})
    assert device is not None
    assert device.name == "Module A"

    renamed = _make_subentry_data("A")
    renamed["title"] = "Living Room Panel"
    updated_entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        entry_id=entry.entry_id,
        subentries_data=[renamed],
    )
    updated_entry.runtime_data = entry.runtime_data

    listener = _make_reload_listener(entry)
    with (
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as full_reload,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        await listener(hass, updated_entry)

    assert not full_reload.called, "Rename must not trigger a full reload"
    device = device_registry.async_get_device(identifiers={identifier})
    assert device is not None
    assert device.name == "Living Room Panel"


async def test_reload_listener_dimmable_toggle_triggers_full_reload(
    hass: HomeAssistant, mock_controller
) -> None:
    """Toggling a module's dimmable flag must trigger a full entry reload.

    Dimmable is a bus-topology input: it reclassifies every light on the
    module as a dimmer (or vice versa), so the controller's output lists
    need a fresh setup.
    """
    subentry_off = _make_subentry_data("A", {"1": {"type": "light", "name": "L1"}})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[subentry_off],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    subentry_on = _make_subentry_data("A", {"1": {"type": "light", "name": "L1"}})
    subentry_on["data"]["dimmable"] = True
    updated_entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[subentry_on],
    )
    updated_entry.add_to_hass(hass)
    updated_entry.runtime_data = entry.runtime_data

    listener = _make_reload_listener(entry)
    reload_calls: list[str] = []
    with patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(side_effect=lambda eid: reload_calls.append(eid)),
    ):
        await listener(hass, updated_entry)

    assert reload_calls == [updated_entry.entry_id], (
        f"Expected full reload on dimmable toggle, got: {reload_calls}"
    )
