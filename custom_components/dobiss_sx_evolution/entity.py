"""Base entity for DOBISS SX Evolution.

Each light or shutter belongs to a module subentry, which is represented in
HA by a device (one device per DOBISS module).  That gives users the module
grouping in the Devices UI.

Entity naming choices:
- has_entity_name is False so the friendly name is exactly the output name
  the user typed at setup, without any module prefix.
- suggested_object_id includes the module letter so entity_ids stay unique
  across identically-named outputs on different modules.  With
  has_entity_name False, HA uses this value as-is and does not prepend the
  device name to it.

Area assignment: DOBISS modules physically span rooms, so leave the module
device's area unset.  Users then assign each light or cover to a room
individually; the explicit per-entity area always wins over any device
area inheritance.
"""

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
    ) -> None:
        """Initialize the entity.

        Args:
            coordinator:  The shared coordinator.
            subentry_id:  The config-subentry ID this entity belongs to.
            platform_key: Unique string for this entity within its platform
                          (e.g. "light_A4" or "cover_A9"). Combined with the
                          subentry_id to form the registry unique_id.
            entity_name:  User-facing name for the output (used verbatim as
                          the friendly name).
            module:       Module letter, used for the device link and to
                          keep entity_ids unique across modules.
        """
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{subentry_id}-{platform_key}"
        self._attr_name = entity_name
        self._dobiss_slug = slugify(f"module_{module}_{entity_name}")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_module_{module}")},
        )

    @property
    def suggested_object_id(self) -> str | None:
        """Return the desired object_id with the sx_evo_ prefix.

        With has_entity_name False, HA uses this value as-is without
        prepending the device name, giving e.g. light.sx_evo_module_a_kitchen.
        """
        return f"sx_evo_{self._dobiss_slug}"

    @property
    def available(self) -> bool:
        """Return True if coordinator update succeeded and the CAN bus is connected."""
        return super().available and self.coordinator.controller.is_bus_connected
