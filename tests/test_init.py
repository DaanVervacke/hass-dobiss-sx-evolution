"""Tests for the dobiss_sx_evolution integration setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dobiss_sx_evolution.__init__ import (
    _connection_key,
    _make_reload_listener,
    _module_config,
    async_remove_config_entry_device,
)
from custom_components.dobiss_sx_evolution.const import (
    CONNECTION_TYPE_SOCKETCAND,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
    SUBENTRY_TYPE_MOOD,
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


async def test_unload_entry_partial_failure(
    hass: HomeAssistant, mock_controller
) -> None:
    """When platform unload fails, shutdown must NOT be called."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        result = await hass.config_entries.async_unload(entry.entry_id)

    assert result is False
    mock_controller.async_shutdown.assert_not_awaited()


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
        subentries_data=[
            _make_subentry_data("A", {"1": {"type": "light", "name": "L1"}})
        ],
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

    with (
        patch(
            "custom_components.dobiss_sx_evolution.__init__.hass",
            create=True,
        ),
        patch.object(hass.config_entries, "async_reload", side_effect=_fake_reload),
    ):
        unload_calls: list = []
        forward_calls: list = []

        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                new=AsyncMock(
                    return_value=True,
                    side_effect=lambda e, p: unload_calls.append(1) or True,
                ),
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
    # The fast path refreshes from the bus and waits for the response burst
    # to settle so newly added entities see real hardware state.
    assert mock_controller.async_refresh_and_settle.await_count == 1


async def test_reload_listener_output_only_suppresses_dump_failure(
    hass: HomeAssistant, mock_controller
) -> None:
    """A failing post-reload refresh must not propagate from the listener."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[
            _make_subentry_data("A", {"1": {"type": "light", "name": "L1"}})
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    mock_controller.async_refresh_and_settle = AsyncMock(side_effect=Exception("boom"))

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
    updated_entry.runtime_data = entry.runtime_data

    listener = _make_reload_listener(entry)

    with (
        patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ) as full_reload,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        await listener(hass, updated_entry)

    assert not full_reload.called
    mock_controller.async_refresh_and_settle.assert_awaited_once()


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

    # Simulate adding module B (new module letter, full reload required).
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
        hass.config_entries,
        "async_reload",
        new=AsyncMock(side_effect=reload_calls.append),
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

    # Simulate changing the host (connection param change, full reload).
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
        hass.config_entries,
        "async_reload",
        new=AsyncMock(side_effect=reload_calls.append),
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


def test_connection_key_includes_master_device() -> None:
    """_connection_key includes master_device so changes trigger reload."""
    e1 = MockConfigEntry(domain=DOMAIN, data=_make_entry_data())
    e2 = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(master_device="/dev/ttyUSB1")
    )
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
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

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
        patch.object(
            hass.config_entries, "async_reload", new=AsyncMock()
        ) as full_reload,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
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
        new=AsyncMock(side_effect=reload_calls.append),
    ):
        await listener(hass, updated_entry)

    assert reload_calls == [updated_entry.entry_id], (
        f"Expected full reload on dimmable toggle, got: {reload_calls}"
    )


# ---------------------------------------------------------------------------
# async_remove_config_entry_device tests
# ---------------------------------------------------------------------------


async def test_remove_device_allows_module_device(
    hass: HomeAssistant, mock_controller
) -> None:
    """A module device (not the hub) may be removed once its subentry is gone."""
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

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
    module_identifier = (DOMAIN, f"{entry.entry_id}_module_A")
    module_device = device_registry.async_get_device(identifiers={module_identifier})
    assert module_device is not None

    result = await async_remove_config_entry_device(hass, entry, module_device)
    assert result is True


async def test_remove_device_blocks_hub_device(
    hass: HomeAssistant, mock_controller
) -> None:
    """The hub (Max200) device must never be removable via the device page."""
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    hub_identifier = (DOMAIN, entry.entry_id)
    hub_device = device_registry.async_get_device(identifiers={hub_identifier})
    assert hub_device is not None

    result = await async_remove_config_entry_device(hass, entry, hub_device)
    assert result is False


# ---------------------------------------------------------------------------
# Refresh service tests
# ---------------------------------------------------------------------------


def _make_mood_subentry_data(mood_number: int = 0, title: str = "Mood One") -> dict:
    """Return a minimal subentry_data dict for one mood (scene)."""
    return {
        "subentry_type": SUBENTRY_TYPE_MOOD,
        "title": title,
        "unique_id": f"mood:{mood_number}",
        "data": {"mood_number": mood_number},
    }


async def test_prune_removes_stale_entity_on_output_retype(
    hass: HomeAssistant, mock_controller
) -> None:
    """Retyping an output from light to switch must remove the stale light entity."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[
            _make_subentry_data("A", {"3": {"type": "light", "name": "L3"}})
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    module_sub = next(
        sub for sub in entry.subentries.values() if sub.subentry_type == "module"
    )
    light_unique_id = f"{module_sub.subentry_id}-light_3"
    switch_unique_id = f"{module_sub.subentry_id}-switch_3"
    assert entity_registry.async_get_entity_id("light", DOMAIN, light_unique_id)

    hass.config_entries.async_update_subentry(
        entry,
        module_sub,
        data={
            "module": "A",
            "dimmable": False,
            "outputs": {"3": {"type": "switch", "name": "L3"}},
        },
    )
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id("light", DOMAIN, light_unique_id) is None
    assert (
        entity_registry.async_get_entity_id("switch", DOMAIN, switch_unique_id)
        is not None
    )


async def test_prune_removes_entity_on_output_removal(
    hass: HomeAssistant, mock_controller
) -> None:
    """Deleting an output entirely must remove its entity, with no replacement."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[
            _make_subentry_data("A", {"3": {"type": "light", "name": "L3"}})
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    module_sub = next(
        sub for sub in entry.subentries.values() if sub.subentry_type == "module"
    )
    light_unique_id = f"{module_sub.subentry_id}-light_3"
    assert entity_registry.async_get_entity_id("light", DOMAIN, light_unique_id)

    hass.config_entries.async_update_subentry(
        entry,
        module_sub,
        data={"module": "A", "dimmable": False, "outputs": {}},
    )
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id("light", DOMAIN, light_unique_id) is None


async def test_prune_removes_entity_on_shutter_retype(
    hass: HomeAssistant, mock_controller
) -> None:
    """Converting a shutter output to a light must remove the stale cover entity."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[
            _make_subentry_data(
                "A",
                {"1": {"type": "shutter", "down_output": 2, "name": "Shutter 1"}},
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    module_sub = next(
        sub for sub in entry.subentries.values() if sub.subentry_type == "module"
    )
    cover_unique_id = f"{module_sub.subentry_id}-cover_1"
    light_unique_id = f"{module_sub.subentry_id}-light_1"
    assert entity_registry.async_get_entity_id("cover", DOMAIN, cover_unique_id)

    hass.config_entries.async_update_subentry(
        entry,
        module_sub,
        data={
            "module": "A",
            "dimmable": False,
            "outputs": {"1": {"type": "light", "name": "Output 1"}},
        },
    )
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id("cover", DOMAIN, cover_unique_id) is None
    assert (
        entity_registry.async_get_entity_id("light", DOMAIN, light_unique_id)
        is not None
    )


async def test_prune_leaves_scene_and_diagnostic_entities_untouched(
    hass: HomeAssistant, mock_controller
) -> None:
    """Mood scenes and hub diagnostic entities must survive output-only pruning."""
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(),
        title="DOBISS",
        version=1,
        subentries_data=[
            _make_subentry_data("A", {"3": {"type": "light", "name": "L3"}}),
            _make_mood_subentry_data(0, "Movie Night"),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    module_sub = next(
        sub for sub in entry.subentries.values() if sub.subentry_type == "module"
    )
    mood_sub = next(
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MOOD
    )
    scene_unique_id = f"{mood_sub.subentry_id}-mood"
    bus_sensor_unique_id = f"{entry.entry_id}_bus_connected"

    scene_entity_id = entity_registry.async_get_entity_id(
        "scene", DOMAIN, scene_unique_id
    )
    bus_sensor_entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, bus_sensor_unique_id
    )
    assert scene_entity_id is not None
    assert bus_sensor_entity_id is not None

    hass.config_entries.async_update_subentry(
        entry,
        module_sub,
        data={
            "module": "A",
            "dimmable": False,
            "outputs": {"3": {"type": "switch", "name": "L3"}},
        },
    )
    await hass.async_block_till_done()

    # Untouched: same entity_id still resolves for both.
    assert (
        entity_registry.async_get_entity_id("scene", DOMAIN, scene_unique_id)
        == scene_entity_id
    )
    assert (
        entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, bus_sensor_unique_id
        )
        == bus_sensor_entity_id
    )


async def test_prune_runs_before_full_reload_on_dimmable_toggle(
    hass: HomeAssistant, mock_controller
) -> None:
    """A dimmable toggle (full reload) that also drops an output must still prune it.

    Dimmable changes go through the full-reload branch, not the output-only
    fast path, so pruning must happen there too.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    entry = MockConfigEntry(
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
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    module_sub = next(
        sub for sub in entry.subentries.values() if sub.subentry_type == "module"
    )
    output2_unique_id = f"{module_sub.subentry_id}-light_2"
    assert entity_registry.async_get_entity_id("light", DOMAIN, output2_unique_id)

    # Toggle dimmable AND drop output 2 in the same update.
    hass.config_entries.async_update_subentry(
        entry,
        module_sub,
        data={
            "module": "A",
            "dimmable": True,
            "outputs": {"1": {"type": "light", "name": "L1"}},
        },
    )
    await hass.async_block_till_done()

    assert (
        entity_registry.async_get_entity_id("light", DOMAIN, output2_unique_id) is None
    )


async def test_refresh_service_calls_dump(hass: HomeAssistant, mock_controller) -> None:
    """The refresh service must call async_request_dump on each loaded entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "refresh")
    await hass.services.async_call(DOMAIN, "refresh", blocking=True)

    mock_controller.async_request_dump.assert_awaited_once()


async def test_refresh_service_skips_non_loaded_entries(
    hass: HomeAssistant, mock_controller
) -> None:
    """Refresh service must skip entries that are not in LOADED state."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    await hass.services.async_call(DOMAIN, "refresh", blocking=True)

    mock_controller.async_request_dump.assert_not_awaited()


async def test_refresh_service_catches_dump_error(
    hass: HomeAssistant, mock_controller
) -> None:
    """Refresh service must log and continue when dump raises."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    mock_controller.async_request_dump = AsyncMock(side_effect=Exception("bus error"))

    # Must not raise.
    await hass.services.async_call(DOMAIN, "refresh", blocking=True)

    mock_controller.async_request_dump.assert_awaited_once()


async def test_refresh_service_removed_on_last_unload(
    hass: HomeAssistant, mock_controller
) -> None:
    """The refresh service must be removed when the last entry unloads."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "refresh")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, "refresh")


async def test_sync_clock_service_registered_and_removed_on_last_unload(
    hass: HomeAssistant, mock_controller
) -> None:
    """The sync_clock service must be registered on setup and removed on last unload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(max200_host="10.0.0.2"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch("custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.services.has_service(DOMAIN, "sync_clock")

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert not hass.services.has_service(DOMAIN, "sync_clock")


async def test_sync_clock_service_calls_tcp_client(
    hass: HomeAssistant, mock_controller
) -> None:
    """Calling sync_clock invokes the coordinator's TCP client with a tz-aware time."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(max200_host="10.0.0.2"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200TcpClient"
    ) as mock_tcp_cls:
        mock_tcp = mock_tcp_cls.return_value
        mock_tcp.host = "10.0.0.2"
        mock_tcp.sync_clock = AsyncMock()

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # The initial setup sync already fired once, reset to isolate the
        # service-triggered call.
        mock_tcp.sync_clock.reset_mock()

        await hass.services.async_call(DOMAIN, "sync_clock", blocking=True)

        mock_tcp.sync_clock.assert_awaited_once()
        call_dt = mock_tcp.sync_clock.call_args[0][0]
        assert call_dt.tzinfo is not None


async def test_sync_clock_service_no_clock_link_raises(
    hass: HomeAssistant, mock_controller
) -> None:
    """The service raises ServiceValidationError when no entry has a Max200 link."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=_make_entry_data(), title="DOBISS", version=1
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "sync_clock", blocking=True)


async def test_sync_clock_service_serial_failure_raises_homeassistant_error(
    hass: HomeAssistant, mock_controller
) -> None:
    """A serial sync failure surfaces as HomeAssistantError on the service call."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_make_entry_data(master_device="/dev/ttyUSB1"),
        title="DOBISS",
        version=1,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.dobiss_sx_evolution.coordinator.Max200SerialClient"
    ) as mock_serial_cls:
        mock_serial = mock_serial_cls.return_value
        mock_serial.device = "/dev/ttyUSB1"
        mock_serial.sync_clock = MagicMock()

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        mock_serial.sync_clock.side_effect = ConnectionError("device gone")

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(DOMAIN, "sync_clock", blocking=True)
