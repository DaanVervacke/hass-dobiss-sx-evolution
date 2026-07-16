"""Sensor platform for DOBISS SX Evolution - reconnect counter."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
    """Set up the reconnect counter sensor."""
    async_add_entities([DobissReconnectCount(entry.runtime_data)])


class DobissReconnectCount(CoordinatorEntity[DobissCoordinator], SensorEntity):
    """Sensor tracking the number of CAN bus reconnections since last load."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "reconnect_count"
    _attr_native_unit_of_measurement = "reconnections"

    def __init__(self, coordinator: DobissCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_reconnect_count"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
        )

    @property
    def native_value(self) -> int:
        """Return the number of reconnections."""
        return self.coordinator.controller.reconnect_count
