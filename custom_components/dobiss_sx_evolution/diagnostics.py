"""Diagnostics support for the DOBISS SX Evolution integration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import SUBENTRY_TYPE_MODULE
from .controller import SocketcandConnection
from .coordinator import DobissConfigEntry


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

    conn = ctrl.connection
    if isinstance(conn, SocketcandConnection):
        connection_info = {
            "type": "socketcand",
            "host": conn.host,
            "port": conn.port,
            "can_interface": conn.interface,
        }
        redact_fields = {"host"}
    else:
        connection_info = {
            "type": "usb",
            "device": conn.device,
            "baudrate": conn.baudrate,
            "can_interface": conn.can_interface,
        }
        redact_fields = {"device"}

    raw: dict[str, Any] = {
        "connection": connection_info,
        "controller": {
            "modules": list(ctrl.modules),
            "lights": [list(k) for k in ctrl.lights],
            "dimmers": [list(k) for k in ctrl.dimmers],
            "shutters": [
                asdict(s) if is_dataclass(s) else s for s in ctrl.shutters
            ],
            "reconnect_count": ctrl.reconnect_count,
            "is_bus_connected": ctrl.is_bus_connected,
        },
        "subentries": subentries_info,
        "states": _serialise_states(ctrl.states),
    }

    return async_redact_data(raw, redact_fields)
