"""Sensor platform for DOBISS SX Evolution - reconnect counter."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
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
    """Set up the reconnect counter and Max200 link diagnostic sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [DobissReconnectCount(coordinator)]
    if coordinator.serial_client is not None or coordinator.tcp_client is not None:
        entities.append(DobissLastClockSync(coordinator))
    async_add_entities(entities)


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


class DobissLastClockSync(CoordinatorEntity[DobissCoordinator], SensorEntity):
    """Sensor exposing the timestamp of the last Max200 clock sync attempt."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_clock_sync"

    def __init__(self, coordinator: DobissCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_last_clock_sync"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the last clock sync attempt, if any."""
        return self.coordinator.last_clock_sync
