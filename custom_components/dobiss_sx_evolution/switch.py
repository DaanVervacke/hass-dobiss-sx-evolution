"""Switch platform for DOBISS SX Evolution."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_TYPE_MODULE
from .controller import OutputKey
from .coordinator import DobissConfigEntry, DobissCoordinator
from .entity import DobissEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DobissConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up DOBISS switches from module config subentries."""
    coordinator = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_MODULE:
            continue
        module: str = subentry.data["module"]
        entities: list[DobissSwitch] = []
        for output_str, cfg in subentry.data.get("outputs", {}).items():
            if cfg.get("type") != "switch":
                continue
            output = int(output_str)
            entity_name: str = cfg.get("name") or f"{module}{output}"
            entities.append(
                DobissSwitch(
                    coordinator=coordinator,
                    module_subentry_id=subentry_id,
                    key=(module, output),
                    entity_name=entity_name,
                )
            )
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


class DobissSwitch(DobissEntity, SwitchEntity):
    """A generic on/off relay controlled via the DOBISS CAN bus."""

    def __init__(
        self,
        coordinator: DobissCoordinator,
        module_subentry_id: str,
        key: OutputKey,
        entity_name: str,
    ) -> None:
        """Initialize the switch."""
        module, output = key
        super().__init__(
            coordinator,
            subentry_id=module_subentry_id,
            platform_key=f"switch_{output}",
            entity_name=entity_name,
            module=module,
            output=output,
        )
        self._key = key

    @property
    def is_on(self) -> bool:
        """Return whether the switch is on."""
        return self.coordinator.controller.states.get(self._key, 0) > 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        async with self._bus_call():
            await self.coordinator.controller.async_turn_on(self._key)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        async with self._bus_call():
            await self.coordinator.controller.async_turn_off(self._key)
