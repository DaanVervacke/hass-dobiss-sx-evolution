"""Scene platform for DOBISS SX Evolution moods."""

from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SUBENTRY_TYPE_MOOD
from .coordinator import DobissConfigEntry, DobissCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DobissConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up DOBISS mood scenes from config subentries."""
    coordinator = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_MOOD:
            continue
        async_add_entities(
            [
                DobissMoodScene(
                    coordinator=coordinator,
                    subentry_id=subentry_id,
                    mood_number=subentry.data["mood_number"],
                    entity_name=subentry.title,
                )
            ],
            config_subentry_id=subentry_id,
        )


class DobissMoodScene(CoordinatorEntity[DobissCoordinator], Scene):
    """A DOBISS mood exposed as a Home Assistant scene."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: DobissCoordinator,
        subentry_id: str,
        mood_number: int,
        entity_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._mood_number = mood_number
        self._attr_unique_id = f"{subentry_id}-mood"
        self._attr_name = entity_name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.controller.is_bus_connected

    async def async_activate(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.controller.async_activate_mood(self._mood_number)
        except Exception as err:
            raise HomeAssistantError(
                translation_domain="dobiss_sx_evolution",
                translation_key="cannot_send",
            ) from err
