"""Base entity for DOBISS SX Evolution."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import DobissCoordinator


class DobissEntity(CoordinatorEntity[DobissCoordinator]):
    """Base entity for one DOBISS light or shutter output."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: DobissCoordinator,
        subentry_id: str,
        platform_key: str,
        entity_name: str,
        module: str,
        output: int,
    ) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{subentry_id}-{platform_key}"
        self._attr_name = entity_name
        # Bypass HA's object_id_base logic (which prepends device name on
        # some HA versions) by setting the internal suggested object_id
        # directly.  This gives us full control over the entity_id format:
        # light.sx_evo_module_a_output_7_centraal
        self.internal_integration_suggested_object_id = slugify(
            f"sx_evo_module_{module}_output_{output}_{entity_name}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_module_{module}")},
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.controller.is_bus_connected
