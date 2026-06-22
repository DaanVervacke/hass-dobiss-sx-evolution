"""Light platform for DOBISS SX Evolution."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_TYPE_MODULE
from .controller import OutputKey
from .coordinator import DobissConfigEntry, DobissCoordinator
from .entity import DobissEntity
from .protocol import can_to_ha_brightness

# Each write goes directly to the CAN bus — no shared resource needs
# serialisation at the platform level.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DobissConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up DOBISS lights from module config subentries."""
    coordinator = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_MODULE:
            continue
        module: str = subentry.data["module"]
        for output_str, cfg in subentry.data.get("outputs", {}).items():
            if cfg.get("type") != "light":
                continue
            output = int(output_str)
            dimmable: bool = cfg.get("dimmable", False)
            entity_name: str = cfg.get("name") or f"{module}{output}"
            async_add_entities(
                [
                    DobissLight(
                        coordinator=coordinator,
                        module_subentry_id=subentry_id,
                        key=(module, output),
                        entity_name=entity_name,
                        dimmable=dimmable,
                    )
                ],
                config_subentry_id=subentry_id,
            )


class DobissLight(DobissEntity, LightEntity):
    """A light controlled via the DOBISS CAN bus."""

    def __init__(
        self,
        coordinator: DobissCoordinator,
        module_subentry_id: str,
        key: OutputKey,
        entity_name: str,
        dimmable: bool,
    ) -> None:
        """Initialize the light."""
        module, output = key
        super().__init__(
            coordinator,
            subentry_id=module_subentry_id,
            platform_key=f"light_{module}{output}",
            entity_name=entity_name,
            module=module,
        )
        self._key = key
        if dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool:
        """Return whether the light is on."""
        return self.coordinator.controller.states.get(self._key, 0) > 0

    @property
    def brightness(self) -> int | None:
        """Return brightness scaled to 0–255, or None for non-dimmable."""
        if not self.coordinator.controller.dimmable(self._key):
            return None
        return can_to_ha_brightness(self.coordinator.controller.states.get(self._key, 0))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally with a brightness."""
        try:
            await self.coordinator.controller.async_turn_on(
                self._key, brightness=kwargs.get(ATTR_BRIGHTNESS)
            )
        except RuntimeError as err:
            raise HomeAssistantError(
                translation_domain="dobiss_sx_evolution",
                translation_key="cannot_send",
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        try:
            await self.coordinator.controller.async_turn_off(self._key)
        except RuntimeError as err:
            raise HomeAssistantError(
                translation_domain="dobiss_sx_evolution",
                translation_key="cannot_send",
            ) from err
