"""The DOBISS SX Evolution integration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONF_HOST,
    CONF_INTERFACE,
    CONF_MASTER_DEVICE,
    CONF_MODULE,
    CONF_PORT,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from .coordinator import DobissConfigEntry, DobissCoordinator, parse_output_lists

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SWITCH,
]

_SERVICE_REFRESH = "refresh"


async def _async_handle_refresh(call: ServiceCall) -> None:
    """Handle the dobiss_sx_evolution.refresh service call."""
    hass = call.hass
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        coordinator: DobissCoordinator = entry.runtime_data
        try:
            await coordinator.controller.async_request_dump()
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to send refresh request for entry %s",
                entry.entry_id,
                exc_info=True,
            )


async def async_setup_entry(hass: HomeAssistant, entry: DobissConfigEntry) -> bool:
    """Set up DOBISS SX Evolution from a config entry."""
    coordinator = DobissCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    device_registry = dr.async_get(hass)

    # Register the hub as a SERVICE device so per-module `via_device` links resolve.
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="DOBISS",
        model="Max200",
        name="Max200",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    # Register a device per module subentry so the module surfaces in the
    # Devices UI.  Placed before platform setup so the via_device link and
    # device name are available when entities register.
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_TYPE_MODULE:
            continue
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=sub.subentry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_module_{sub.data['module']}")},
            manufacturer="DOBISS",
            model="SX Evolution module",
            name=sub.title,
            via_device=(DOMAIN, entry.entry_id),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload when subentries change so platforms re-read them and
    # create/destroy entities accordingly.  _make_reload_listener snapshots
    # the current connection key and module set so the listener can diff
    # against the new entry and choose between a full bus reconnect and a
    # lighter platform-only reload (e.g. adding an output to an existing module).
    entry.async_on_unload(entry.add_update_listener(_make_reload_listener(entry)))

    if not hass.services.has_service(DOMAIN, _SERVICE_REFRESH):
        hass.services.async_register(
            DOMAIN, _SERVICE_REFRESH, _async_handle_refresh, schema=vol.Schema({})
        )

    return True


type _ConnectionKey = tuple[
    str | None, str | None, int | None, str | None, str | None, str | None
]


def _connection_key(entry: DobissConfigEntry) -> _ConnectionKey:
    """Return the parts of entry.data that identify the CAN connection."""
    return (
        entry.data.get(CONF_CONNECTION_TYPE),
        entry.data.get(CONF_HOST),
        entry.data.get(CONF_PORT),
        entry.data.get(CONF_INTERFACE),
        entry.data.get(CONF_DEVICE),
        entry.data.get(CONF_MASTER_DEVICE),
    )


def _module_config(entry: DobissConfigEntry) -> frozenset[tuple[str, bool]]:
    """Return the set of (module letter, dimmable) pairs configured in subentries.

    Both the module letter set AND the dimmable flag are bus-topology inputs:
    toggling `dimmable` reclassifies every light on that module as a dimmer
    (and vice versa), which affects the controller's `lights` vs. `dimmers`
    output lists.  Including it in the snapshot ensures a full reload is
    triggered when it changes.
    """
    return frozenset(
        (sub.data[CONF_MODULE], bool(sub.data.get("dimmable", False)))
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_MODULE
    )


def _make_reload_listener(
    entry: DobissConfigEntry,
) -> Callable[[HomeAssistant, DobissConfigEntry], Coroutine[Any, Any, None]]:
    """Return an update-listener that intelligently reloads on subentry changes.

    The listener snapshots the connection key and module config at the time the
    entry finishes setup.  When a subentry changes, it compares those snapshots
    against the new entry to decide:

    - Full reload: connection params, module set, or a module's dimmable flag
      changed (bus must reconnect or controller must rebuild output lists).
    - Platform reload: only output-level data changed (output add, remove, or
      rename, or a subentry title rename).  Module devices are updated in
      place in the device registry so the rename flows through to the
      module device name and to entity friendly names via has_entity_name.
      No bus reconnect is needed.
    """
    # Snapshot taken at setup time so the listener can diff old vs new.
    prev_conn: _ConnectionKey = _connection_key(entry)
    prev_modules: frozenset[tuple[str, bool]] = _module_config(entry)

    async def _listener(hass: HomeAssistant, updated_entry: DobissConfigEntry) -> None:
        nonlocal prev_conn, prev_modules

        new_conn = _connection_key(updated_entry)
        new_modules = _module_config(updated_entry)

        coordinator: DobissCoordinator | None = getattr(
            updated_entry, "runtime_data", None
        )

        if coordinator is None or new_conn != prev_conn or new_modules != prev_modules:
            # Connection or module topology changed — full reload required.
            _LOGGER.debug(
                "Subentry change requires full reload "
                "(coordinator missing: %s, connection changed: %s, "
                "modules changed: %s)",
                coordinator is None,
                new_conn != prev_conn,
                new_modules != prev_modules,
            )
            prev_conn = new_conn
            prev_modules = new_modules
            await hass.config_entries.async_reload(updated_entry.entry_id)
            return

        # Only output-level data changed (names, add/remove outputs within
        # existing modules).  Rebuild the controller's output lists and reload
        # platforms to re-create entities without touching the bus.
        _LOGGER.debug(
            "Subentry change is output-only; reloading platforms without bus reconnect"
        )
        prev_modules = new_modules  # unchanged, but keep in sync

        lights, dimmers, shutters, switches = parse_output_lists(updated_entry)

        ctrl = coordinator.controller
        ctrl.lights = lights
        ctrl.dimmers = dimmers
        ctrl.shutters = shutters
        ctrl.switches = switches
        ctrl.modules = sorted(
            {m for m, _ in (*lights, *dimmers, *switches)}
            | {s.module for s in shutters}
        )

        # Push any subentry title change to the corresponding module device
        # so the device name stays in sync without needing a full reload.
        device_registry = dr.async_get(hass)
        for sub in updated_entry.subentries.values():
            if sub.subentry_type != SUBENTRY_TYPE_MODULE:
                continue
            identifier = (
                DOMAIN,
                f"{updated_entry.entry_id}_module_{sub.data[CONF_MODULE]}",
            )
            device = device_registry.async_get_device(identifiers={identifier})
            if device is not None and device.name != sub.title:
                device_registry.async_update_device(device.id, name=sub.title)

        await hass.config_entries.async_unload_platforms(updated_entry, PLATFORMS)
        await hass.config_entries.async_forward_entry_setups(updated_entry, PLATFORMS)

        # Refresh the state cache from the bus and wait for the response
        # burst to settle so newly added entities see the real hardware
        # state on their first render instead of defaulting to off.
        try:
            await ctrl.async_refresh_and_settle()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Post-reload state refresh failed", exc_info=True)

        coordinator.async_set_updated_data(dict(ctrl.states))

    return _listener


async def async_unload_entry(hass: HomeAssistant, entry: DobissConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
        remaining = hass.config_entries.async_entries(DOMAIN)
        # The current entry is still in the list until unload completes; exclude it.
        if not any(e.entry_id != entry.entry_id for e in remaining):
            hass.services.async_remove(DOMAIN, _SERVICE_REFRESH)
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: DobissConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removal of orphaned module devices, but never the hub device."""
    hub_identifier = (DOMAIN, entry.entry_id)
    return hub_identifier not in device_entry.identifiers
