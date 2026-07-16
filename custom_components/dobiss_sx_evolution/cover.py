"""Cover platform for DOBISS SX Evolution - window screens / shutters."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_TYPE_MODULE
from .controller import ShutterConfig
from .coordinator import DobissConfigEntry, DobissCoordinator
from .entity import DobissEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DobissConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up DOBISS shutters from module config subentries."""
    coordinator = entry.runtime_data
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_MODULE:
            continue
        module: str = subentry.data["module"]
        entities: list[DobissShutter] = []
        for output_str, cfg in subentry.data.get("outputs", {}).items():
            if cfg.get("type") != "shutter":
                continue
            up_output = int(output_str)
            down_output = int(cfg["down_output"])
            shutter = ShutterConfig(
                module=module, up_output=up_output, down_output=down_output
            )
            entity_name: str = cfg.get("name") or f"Shutter {module}{up_output}"
            entities.append(
                DobissShutter(
                    coordinator=coordinator,
                    module_subentry_id=subentry_id,
                    shutter=shutter,
                    entity_name=entity_name,
                )
            )
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


class DobissShutter(DobissEntity, CoverEntity):
    """A DOBISS-driven shutter / window screen.

    Stateless on the bus - the controller reports no position feedback.
    `is_closed` returns None so HA shows the canonical "unknown" state for
    this class of cover, and `assumed_state=True` keeps the open/close/stop
    buttons always available regardless of the reported state.
    """

    _attr_assumed_state = True
    _attr_device_class = CoverDeviceClass.SHADE
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )
    _attr_is_closed = None

    def __init__(
        self,
        coordinator: DobissCoordinator,
        module_subentry_id: str,
        shutter: ShutterConfig,
        entity_name: str,
    ) -> None:
        """Initialize the shutter."""
        module = shutter.module
        up = shutter.up_output
        super().__init__(
            coordinator,
            subentry_id=module_subentry_id,
            platform_key=f"cover_{up}",
            entity_name=entity_name,
            module=module,
            output=up,
        )
        self._shutter = shutter

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        async with self._bus_call():
            await self.coordinator.controller.async_open_shutter(self._shutter)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        async with self._bus_call():
            await self.coordinator.controller.async_close_shutter(self._shutter)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        async with self._bus_call():
            await self.coordinator.controller.async_stop_shutter(self._shutter)
