"""Light platform for DOBISS SX Evolution."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MAX_CAN_BRIGHTNESS_TX, SUBENTRY_TYPE_MODULE
from .controller import OutputKey
from .coordinator import DobissConfigEntry, DobissCoordinator
from .entity import DobissEntity
from .protocol import can_to_ha_brightness, can_tx_to_rx, ha_to_can_brightness

# Each write goes directly to the CAN bus - no shared resource needs
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
        dimmable: bool = subentry.data.get("dimmable", False)
        entities: list[DobissLight] = []
        for output_str, cfg in subentry.data.get("outputs", {}).items():
            if cfg.get("type") != "light":
                continue
            output = int(output_str)
            entity_name: str = cfg.get("name") or f"{module}{output}"
            entities.append(
                DobissLight(
                    coordinator=coordinator,
                    module_subentry_id=subentry_id,
                    key=(module, output),
                    entity_name=entity_name,
                    dimmable=dimmable,
                )
            )
        if entities:
            async_add_entities(entities, config_subentry_id=subentry_id)


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
            platform_key=f"light_{output}",
            entity_name=entity_name,
            module=module,
            output=output,
        )
        self._key = key
        # Tracks the CAN-scale value we most recently wrote so we can detect
        # when an external event (wall switch) changes the brightness and know
        # it is safe to discard the optimistic HA-native brightness.
        self._optimistic_can_value: int | None = None
        if dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear the optimistic brightness when an external change arrives.

        The coordinator fires on every state push (wall switch presses as well
        as echoes of our own writes). When the CAN value reported by the
        controller differs from what we sent, the change came from outside
        (e.g. a wall switch) and we must fall back to the coordinator-derived
        brightness so the slider reflects the real hardware state.
        """
        if self._optimistic_can_value is not None:
            current_can = self.coordinator.controller.states.get(self._key, 0)
            if current_can != self._optimistic_can_value:
                self._attr_brightness = None
                self._optimistic_can_value = None
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool:
        """Return whether the light is on."""
        return self.coordinator.controller.states.get(self._key, 0) > 0

    @property
    def brightness(self) -> int | None:
        """Return brightness scaled to 0-255, or None for non-dimmable.

        When we have an optimistic value (set by async_turn_on before the CAN
        echo returns) we return it verbatim so the HA slider does not snap to
        the coarser quantised value that the round-trip conversion would produce.
        Once an external state change is detected by _handle_coordinator_update
        the optimistic value is cleared and we fall back to the coordinator state.
        """
        if not self.coordinator.controller.dimmable(self._key):
            return None
        if self._attr_brightness is not None:
            return self._attr_brightness
        return can_to_ha_brightness(
            self.coordinator.controller.states.get(self._key, 0)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally with a brightness."""
        ha_brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
        if self.coordinator.controller.dimmable(self._key):
            if ha_brightness is not None:
                # Store the HA-native brightness optimistically so the slider
                # does not snap back to the quantised round-trip value while
                # the CAN echo is in flight.  We also record the CAN-scale
                # value we are about to send so _handle_coordinator_update can
                # recognise the echo as ours.
                self._attr_brightness = ha_brightness
                self._optimistic_can_value = can_tx_to_rx(
                    ha_to_can_brightness(ha_brightness)
                )
            else:
                # Turning on without an explicit brightness sends the
                # controller's "full on" CAN value (MAX_CAN_BRIGHTNESS_TX).
                # can_to_ha_brightness() assumes the narrower RX echo range
                # (0-90), so feeding it the TX-scale value overflows past 255.
                # Set the optimistic HA-native brightness directly instead.
                self._attr_brightness = 255
                self._optimistic_can_value = can_tx_to_rx(MAX_CAN_BRIGHTNESS_TX)
        try:
            await self.coordinator.controller.async_turn_on(
                self._key, brightness=ha_brightness
            )
        except Exception as err:
            self._attr_brightness = None
            self._optimistic_can_value = None
            raise HomeAssistantError(
                translation_domain="dobiss_sx_evolution",
                translation_key="cannot_send",
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        self._attr_brightness = None
        self._optimistic_can_value = None
        async with self._bus_call():
            await self.coordinator.controller.async_turn_off(self._key)
