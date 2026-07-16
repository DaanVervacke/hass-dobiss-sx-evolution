"""DataUpdateCoordinator that wraps the DOBISS CAN controller.

Pure local_push: there is no polling. State updates arrive via
DobissController._read_loop and are flushed into the coordinator
via async_set_updated_data.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONF_HOST,
    CONF_INTERFACE,
    CONF_MODULE,
    CONF_PORT,
    CONNECTION_TYPE_SOCKETCAND,
    DEFAULT_BAUDRATE,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from .controller import (
    ConnectionConfig,
    DobissController,
    OutputKey,
    ShutterConfig,
    SocketcandConnection,
    UsbConnection,
)

_LOGGER = logging.getLogger(__name__)

type DobissConfigEntry = ConfigEntry[DobissCoordinator]


def parse_output_lists(
    entry: DobissConfigEntry,
) -> tuple[list[OutputKey], set[OutputKey], list[ShutterConfig]]:
    """Build lights, dimmers, and shutters lists from module subentries."""
    lights: list[OutputKey] = []
    dimmers: set[OutputKey] = set()
    shutters: list[ShutterConfig] = []

    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_TYPE_MODULE:
            continue
        module: str = sub.data[CONF_MODULE]
        module_dimmable: bool = sub.data.get("dimmable", False)
        for output_str, cfg in sub.data.get("outputs", {}).items():
            output = int(output_str)
            if cfg["type"] == "light":
                if module_dimmable:
                    dimmers.add((module, output))
                else:
                    lights.append((module, output))
            elif cfg["type"] == "shutter":
                shutters.append(
                    ShutterConfig(
                        module=module,
                        up_output=output,
                        down_output=int(cfg["down_output"]),
                    )
                )

    return lights, dimmers, shutters


class DobissCoordinator(DataUpdateCoordinator[dict[OutputKey, int]]):
    """Coordinator exposing the controller's state cache to entities."""

    config_entry: DobissConfigEntry
    controller: DobissController

    def __init__(self, hass: HomeAssistant, entry: DobissConfigEntry) -> None:
        """Initialize the coordinator.

        Lights, dimmers, and shutters are derived from the outputs dict
        stored inside each module subentry.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
            # Controller already drops unchanged frames; suppress no-op wakes.
            always_update=False,
        )

        lights, dimmers, shutters = parse_output_lists(entry)

        connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_SOCKETCAND)
        connection: ConnectionConfig
        if connection_type == CONNECTION_TYPE_SOCKETCAND:
            connection = SocketcandConnection(
                host=entry.data.get(CONF_HOST, ""),
                port=entry.data.get(CONF_PORT, 0),
                interface=entry.data.get(CONF_INTERFACE, ""),
            )
        else:
            connection = UsbConnection(
                device=entry.data.get(CONF_DEVICE, ""),
                baudrate=DEFAULT_BAUDRATE,
                can_interface="slcan",
            )

        self.controller = DobissController(
            hass,
            connection=connection,
            lights=lights,
            dimmers=dimmers,
            shutters=shutters,
            entry_id=entry.entry_id,
        )

    async def _async_setup(self) -> None:
        """Open the CAN bus and run initial discovery."""
        try:
            await self.controller.async_setup()
        except Exception as err:
            await self.controller.async_shutdown()
            raise ConfigEntryNotReady(
                f"Cannot open CAN connection "
                f"{self.controller.connection.description}: {err}"
            ) from err
        self.async_set_updated_data(dict(self.controller.states))
        self.config_entry.async_on_unload(
            self.controller.async_add_listener(self._on_controller_update)
        )

    async def _async_update_data(self) -> dict[OutputKey, int]:
        """Return the current cached state (no polling).

        Raises UpdateFailed if the CAN bus is not currently connected, so
        callers that trigger a manual refresh (e.g. async_request_refresh)
        observe the failure instead of receiving a stale cache silently.
        """
        if not self.controller.is_bus_connected:
            raise UpdateFailed("Bus connection lost")
        return dict(self.controller.states)

    @callback
    def _on_controller_update(self, key: OutputKey, value: int) -> None:
        """Receive a push from the controller and notify listeners."""
        self.async_set_updated_data(dict(self.controller.states))

    async def async_shutdown(self) -> None:
        """Tear down the controller, then the coordinator."""
        await self.controller.async_shutdown()
        await super().async_shutdown()
