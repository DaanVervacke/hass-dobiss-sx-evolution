"""DataUpdateCoordinator that wraps the DOBISS CAN controller.

Pure local_push: there is no polling. State updates arrive via
DobissController._read_loop and are flushed into the coordinator
via async_set_updated_data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CLOCK_SYNC_INTERVAL_HOURS,
    CONF_CONNECTION_TYPE,
    CONF_MASTER_DEVICE,
    CONF_MODULE,
    CONNECTION_TYPE_SOCKETCAND,
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
from .serial_client import Max200SerialClient

_LOGGER = logging.getLogger(__name__)

type DobissConfigEntry = ConfigEntry[DobissCoordinator]


def parse_output_lists(
    entry: DobissConfigEntry,
) -> tuple[list[OutputKey], set[OutputKey], list[ShutterConfig], list[OutputKey]]:
    """Build lights, dimmers, shutters, and switches lists from module subentries."""
    lights: list[OutputKey] = []
    dimmers: set[OutputKey] = set()
    shutters: list[ShutterConfig] = []
    switches: list[OutputKey] = []

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
            elif cfg["type"] == "switch":
                switches.append((module, output))

    return lights, dimmers, shutters, switches


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

        lights, dimmers, shutters, switches = parse_output_lists(entry)

        connection_type = entry.data.get(
            CONF_CONNECTION_TYPE, CONNECTION_TYPE_SOCKETCAND
        )
        connection: ConnectionConfig
        if connection_type == CONNECTION_TYPE_SOCKETCAND:
            connection = SocketcandConnection.from_config(entry.data)
        else:
            connection = UsbConnection.from_config(entry.data)

        self.controller = DobissController(
            hass,
            connection=connection,
            lights=lights,
            dimmers=dimmers,
            shutters=shutters,
            switches=switches,
            entry_id=entry.entry_id,
        )

        master_device = entry.data.get(CONF_MASTER_DEVICE)
        self.serial_client: Max200SerialClient | None = (
            Max200SerialClient(master_device) if master_device else None
        )

        self._debounce_unsub: Callable[[], None] | None = None

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

        if self.serial_client is not None:
            await self._sync_clock()
            self.config_entry.async_on_unload(
                async_track_time_interval(
                    self.hass,
                    self._sync_clock,
                    timedelta(hours=CLOCK_SYNC_INTERVAL_HOURS),
                )
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
        """Receive a push from the controller and schedule a coalesced notify."""
        if self._debounce_unsub is not None:
            self._debounce_unsub()
        self._debounce_unsub = async_call_later(self.hass, 0.05, self._flush_state)

    @callback
    def _flush_state(self, _now: Any = None) -> None:
        """Push the current state snapshot to all entity listeners."""
        self._debounce_unsub = None
        self.async_set_updated_data(dict(self.controller.states))

    async def _sync_clock(self, _now: Any = None) -> None:
        """Send current time to the Max200 over serial."""
        if self.serial_client is None:
            return
        try:
            await self.hass.async_add_executor_job(
                self.serial_client.sync_clock, dt_util.now()
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Clock sync to Max200 failed", exc_info=True)

    async def async_shutdown(self) -> None:
        """Tear down the controller, then the coordinator."""
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None
        await self.controller.async_shutdown()
        await super().async_shutdown()
