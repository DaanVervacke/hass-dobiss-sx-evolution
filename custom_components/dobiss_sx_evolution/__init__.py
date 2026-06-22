"""The DOBISS SX Evolution integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, SUBENTRY_TYPE_MODULE
from .coordinator import DobissConfigEntry, DobissCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.COVER, Platform.LIGHT]

_SERVICE_REFRESH = "refresh"


async def _async_handle_refresh(call: ServiceCall) -> None:
    """Handle the dobiss_sx_evolution.refresh service call."""
    hass = call.hass
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator: DobissCoordinator = entry.runtime_data
        try:
            await coordinator.controller.async_request_dump()
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to send refresh request for entry %s", entry.entry_id,
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register a device for each module subentry and associate it with the
    # subentry so the device appears under its module in the integration UI.
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

    # Reload when subentries are added/removed/updated so platforms re-read them
    # and create/destroy entities accordingly.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    if not hass.services.has_service(DOMAIN, _SERVICE_REFRESH):
        hass.services.async_register(DOMAIN, _SERVICE_REFRESH, _async_handle_refresh)

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: DobissConfigEntry) -> None:
    """Reload the entry when subentries change."""
    await hass.config_entries.async_reload(entry.entry_id)


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
