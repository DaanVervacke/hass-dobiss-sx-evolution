"""Base entity for DOBISS SX Evolution.

Entities are owned directly by their module subentry.  They do NOT link to a
per-module HA device: DOBISS modules span rooms (a single module drives
outputs in different physical rooms), so inheriting an area from the module
would be semantically wrong.  Every light or shutter therefore stands on its
own in HA and is area-assigned per entity.
"""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

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
        module_title: str,
    ) -> None:
        """Initialize the entity.

        Args:
            coordinator:  The shared coordinator.
            subentry_id:  The config-subentry ID this entity belongs to.
            platform_key: Unique string for this entity within its platform
                          (e.g. "light_A4" or "cover_A9"). Combined with the
                          subentry_id to form the registry unique_id.
            entity_name:  User-facing name for the output (e.g. "Kamer Daan").
            module:       Module letter used to keep entity_ids unique across
                          identically-named outputs on different modules.
            module_title: Subentry title, prepended to the friendly name for
                          context ("Module A Kamer Daan").
        """
        super().__init__(coordinator)
        self._attr_unique_id = f"{subentry_id}-{platform_key}"
        self._attr_name = f"{module_title} {entity_name}"
        self._dobiss_slug = slugify(f"module_{module}_{entity_name}")

    @property
    def suggested_object_id(self) -> str | None:
        """Return the desired object_id, with sx_evo_ before the module slug.

        has_entity_name is False, so HA uses this value as-is (no device-name
        prefix).  Result: light.sx_evo_module_a_kamer_daan.
        """
        return f"sx_evo_{self._dobiss_slug}"

    @property
    def available(self) -> bool:
        """Return True if coordinator update succeeded and the CAN bus is connected."""
        return super().available and self.coordinator.controller.is_bus_connected
