"""Diagnostics support for the DOBISS SX Evolution integration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import SUBENTRY_TYPE_MODULE
from .coordinator import DobissConfigEntry

# Redact the host address before sharing: it identifies the LAN topology and
# the socketcand daemon endpoint.  All other fields are safe to share as-is.
_TO_REDACT: set[str] = {"host"}


def _serialise_states(states: dict[tuple[str, int], int]) -> dict[str, int]:
    """Flatten tuple-keyed state map to JSON-safe "M<mod>O<out>" strings."""
    return {
        f"M{module}O{output}": value
        for (module, output), value in states.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: DobissConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics data for a config entry."""
    coordinator = entry.runtime_data
    ctrl = coordinator.controller

    subentries_info: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_MODULE:
            continue
        subentries_info.append(
            {
                "subentry_id": subentry_id,
                "type": subentry.subentry_type,
                "title": subentry.title,
                "data": dict(subentry.data),
            }
        )

    raw: dict[str, Any] = {
        "controller": {
            "host": ctrl.host,
            "port": ctrl.port,
            "interface": ctrl.interface,
            "modules": list(ctrl.modules),
            "lights": [list(k) for k in ctrl.lights],
            "dimmers": [list(k) for k in ctrl.dimmers],
            "shutters": [
                asdict(s) if is_dataclass(s) else s for s in ctrl.shutters
            ],
            "reconnect_count": ctrl.reconnect_count,
        },
        "subentries": subentries_info,
        "states": _serialise_states(ctrl.states),
    }

    return async_redact_data(raw, _TO_REDACT)
