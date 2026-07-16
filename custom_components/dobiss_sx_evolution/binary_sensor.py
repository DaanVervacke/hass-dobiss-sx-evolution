"""Binary sensor platform for DOBISS SX Evolution - CAN bus connectivity."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DobissConfigEntry, DobissCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DobissConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the CAN bus connectivity binary sensor."""
    async_add_entities([DobissBusConnectivity(entry.runtime_data)])


class DobissBusConnectivity(CoordinatorEntity[DobissCoordinator], BinarySensorEntity):
    """Binary sensor indicating whether the CAN bus connection is alive."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "bus_connected"

    def __init__(self, coordinator: DobissCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_bus_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return True when the CAN bus is connected."""
        return self.coordinator.controller.is_bus_connected
