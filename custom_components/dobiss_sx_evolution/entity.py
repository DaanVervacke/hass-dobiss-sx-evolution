"""Base entity for DOBISS SX Evolution.

Each light or shutter entity attaches to its module device (one device per
physical DOBISS module). Entities are owned by the module subentry; there
are no per-entity subentries.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DobissCoordinator


class DobissEntity(CoordinatorEntity[DobissCoordinator]):
    """Base entity for one DOBISS light or shutter subentry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DobissCoordinator,
        subentry_id: str,
        platform_key: str,
        entity_name: str,
        module: str,
    ) -> None:
        """Initialize the entity.

        Args:
            coordinator:  The shared coordinator.
            subentry_id:  The config-subentry ID this entity belongs to.
            platform_key: Unique string for this entity within its platform
                          (e.g. "light_A4" or "cover_A9"). Combined with the
                          subentry_id to form the registry unique_id.
            entity_name:  Friendly name for the entity (the subentry title).
            module:       Module letter. The entity's device identifier
                          points at the module device shared by all entities
                          on this module.
        """
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{subentry_id}-{platform_key}"
        self._attr_name = entity_name
        self._dobiss_entity_name = entity_name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_module_{module}")},
        )

    @property
    def suggested_object_id(self) -> str | None:
        """Return a suggested entity object id with the sx_evo_ prefix.

        Overrides the default (which returns the plain entity name) so that
        HA registers entity_ids like light.sx_evo_kitchen instead of
        light.kitchen.  The friendly name is unaffected.
        """
        return f"sx_evo_{self._dobiss_entity_name}"

    @property
    def available(self) -> bool:
        """Return True if coordinator update succeeded and the CAN bus is connected."""
        return super().available and self.coordinator.controller.is_bus_connected
