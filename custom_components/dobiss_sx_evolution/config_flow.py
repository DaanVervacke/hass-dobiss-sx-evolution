"""Config flow for DOBISS SX Evolution."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any

import serial.tools.list_ports
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_DEVICE,
    CONF_HOST,
    CONF_INTERFACE,
    CONF_MASTER_DEVICE,
    CONF_MAX200_HOST,
    CONF_MODULE,
    CONF_NAME,
    CONF_PORT,
    CONNECTION_TYPE_SOCKETCAND,
    CONNECTION_TYPE_USB,
    DEFAULT_INTERFACE,
    DEFAULT_PORT,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
    SUBENTRY_TYPE_MODULE_IMPORT,
    SUBENTRY_TYPE_MOOD,
)
from .controller import ConnectionConfig, SocketcandConnection, UsbConnection
from .protocol import OUTPUTS_PER_MODULE
from .serial_client import Max200SerialClient
from .tcp_client import Max200TcpClient

_LOGGER = logging.getLogger(__name__)

CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            int, vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_INTERFACE, default=DEFAULT_INTERFACE): str,
    }
)


def _probe_bus_sync(connection: ConnectionConfig) -> None:
    """Open and immediately close the bus to validate connectivity.

    Must be called from an executor thread. Raises on any failure.
    """
    bus = connection.make_bus()
    with contextlib.suppress(Exception):
        bus.shutdown()


def _list_usb_devices() -> list[SelectOptionDict]:
    """Enumerate USB serial ports and resolve stable device paths."""
    from homeassistant.components import usb  # noqa: PLC0415

    options: list[SelectOptionDict] = []
    for port in serial.tools.list_ports.comports():
        device_path = usb.get_serial_by_id(port.device)
        display_name = usb.human_readable_device_name(
            device=device_path,
            serial_number=port.serial_number,
            manufacturer=port.manufacturer,
            description=port.description,
            vid=port.vid,
            pid=port.pid,
        )
        options.append(SelectOptionDict(label=display_name, value=device_path))
    return options


def _validate_module(module: str) -> str | None:
    """Return error key if module letter is invalid, else None."""
    if len(module) != 1 or not module.isascii() or not module.isalpha():
        return "invalid_module"
    return None


def _occupied_outputs_in_module(outputs: dict[str, Any]) -> set[int]:
    """Return the full set of output numbers claimed in an outputs dict.

    For lights: the key itself.
    For shutters: the key (up) AND the down_output value.
    """
    occupied: set[int] = set()
    for output_str, cfg in outputs.items():
        occupied.add(int(output_str))
        if cfg.get("type") == "shutter":
            occupied.add(int(cfg["down_output"]))
    return occupied


class DobissConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for DOBISS SX Evolution."""

    VERSION = 1

    @staticmethod
    def _get_connection_type_schema() -> vol.Schema:
        """Return schema for connection type selection."""
        connection_options: list[SelectOptionDict] = [
            SelectOptionDict(
                label="socketcand (TCP to socketcand daemon)",
                value=CONNECTION_TYPE_SOCKETCAND,
            ),
            SelectOptionDict(
                label="USB CAN adapter (direct serial connection)",
                value=CONNECTION_TYPE_USB,
            ),
        ]
        return vol.Schema(
            {
                vol.Required(CONF_CONNECTION_TYPE): SelectSelector(
                    SelectSelectorConfig(
                        options=connection_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def _async_probe(self, connection: ConnectionConfig) -> str | None:
        """Probe the bus. Returns error key on failure, None on success."""
        try:
            await self.hass.async_add_executor_job(_probe_bus_sync, connection)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("CAN probe failed", exc_info=True)
            return "cannot_connect"
        return None

    async def _build_usb_device_options(self) -> list[SelectOptionDict]:
        return await self.hass.async_add_executor_job(_list_usb_devices)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the first step: connection type selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            connection_type = user_input.get(CONF_CONNECTION_TYPE)
            if connection_type == CONNECTION_TYPE_SOCKETCAND:
                return await self.async_step_socketcand(None)
            elif connection_type == CONNECTION_TYPE_USB:
                return await self.async_step_usb_manual(None)
            else:
                errors[CONF_CONNECTION_TYPE] = "invalid_connection_type"

        return self.async_show_form(
            step_id="user",
            data_schema=self._get_connection_type_schema(),
            errors=errors,
        )

    async def async_step_socketcand(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect socketcand connection details and create the entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{CONNECTION_TYPE_SOCKETCAND}:{user_input[CONF_HOST]}:{user_input[CONF_PORT]}/{user_input[CONF_INTERFACE]}"
            )
            self._abort_if_unique_id_configured()
            conn = SocketcandConnection.from_config(user_input)
            error = await self._async_probe(conn)
            if error:
                errors["base"] = error
            else:
                data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_SOCKETCAND,
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_INTERFACE: user_input[CONF_INTERFACE],
                }
                if master := user_input.get(CONF_MASTER_DEVICE, ""):
                    data[CONF_MASTER_DEVICE] = master
                if max200_host := user_input.get(CONF_MAX200_HOST, ""):
                    data[CONF_MAX200_HOST] = max200_host
                return self.async_create_entry(
                    title=f"Max200 ({conn.description})",
                    data=data,
                )

        master_options = await self._build_usb_device_options()
        schema = CONNECTION_SCHEMA.extend(
            {
                vol.Optional(CONF_MASTER_DEVICE, default=""): SelectSelector(
                    SelectSelectorConfig(
                        options=master_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(CONF_MAX200_HOST, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="socketcand", data_schema=schema, errors=errors
        )

    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle USB discovery of a CAN adapter by Home Assistant core."""
        self._discovered_usb_device = discovery_info.device
        return await self.async_step_usb_manual(None)

    async def async_step_usb_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect USB CAN device connection details and create the entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{CONNECTION_TYPE_USB}:{user_input[CONF_DEVICE]}"
            )
            self._abort_if_unique_id_configured()
            conn = UsbConnection.from_config(user_input)
            error = await self._async_probe(conn)
            if error:
                errors["base"] = error
            else:
                data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_USB,
                    CONF_DEVICE: user_input[CONF_DEVICE],
                }
                if master := user_input.get(CONF_MASTER_DEVICE, ""):
                    data[CONF_MASTER_DEVICE] = master
                if max200_host := user_input.get(CONF_MAX200_HOST, ""):
                    data[CONF_MAX200_HOST] = max200_host
                return self.async_create_entry(
                    title=f"Max200 ({conn.description})",
                    data=data,
                )

        device_options = await self._build_usb_device_options()
        default_device = getattr(self, "_discovered_usb_device", None)
        device_key = (
            vol.Required(CONF_DEVICE, default=default_device)
            if default_device
            else vol.Required(CONF_DEVICE)
        )
        schema = vol.Schema(
            {
                device_key: SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_MASTER_DEVICE, default=""): SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(CONF_MAX200_HOST, default=""): str,
            }
        )

        return self.async_show_form(
            step_id="usb_manual", data_schema=schema, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication when the connection details change."""
        connection_type = entry_data.get(
            CONF_CONNECTION_TYPE, CONNECTION_TYPE_SOCKETCAND
        )

        if connection_type == CONNECTION_TYPE_SOCKETCAND:
            self._reauth_defaults = {
                CONF_HOST: entry_data.get(CONF_HOST, ""),
                CONF_PORT: entry_data.get(CONF_PORT, DEFAULT_PORT),
                CONF_INTERFACE: entry_data.get(CONF_INTERFACE, DEFAULT_INTERFACE),
                CONF_MASTER_DEVICE: entry_data.get(CONF_MASTER_DEVICE, ""),
                CONF_MAX200_HOST: entry_data.get(CONF_MAX200_HOST, ""),
            }
            return await self.async_step_reauth_socketcand()
        else:
            self._reauth_defaults = {
                CONF_DEVICE: entry_data.get(CONF_DEVICE, ""),
                CONF_MASTER_DEVICE: entry_data.get(CONF_MASTER_DEVICE, ""),
                CONF_MAX200_HOST: entry_data.get(CONF_MAX200_HOST, ""),
            }
            return await self.async_step_reauth_usb()

    async def async_step_reauth_socketcand(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth by re-probing with new socketcand connection details."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            conn = SocketcandConnection.from_config(user_input)
            error = await self._async_probe(conn)
            if error:
                errors["base"] = error
            else:
                new_data = {
                    **entry.data,
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_INTERFACE: user_input[CONF_INTERFACE],
                }
                if master := user_input.get(CONF_MASTER_DEVICE, ""):
                    new_data[CONF_MASTER_DEVICE] = master
                else:
                    new_data.pop(CONF_MASTER_DEVICE, None)
                if max200_host := user_input.get(CONF_MAX200_HOST, ""):
                    new_data[CONF_MAX200_HOST] = max200_host
                else:
                    new_data.pop(CONF_MAX200_HOST, None)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{user_input[CONF_HOST]}:{user_input[CONF_PORT]}/{user_input[CONF_INTERFACE]}",
                    data=new_data,
                    reason="reauth_successful",
                )

        master_options = await self._build_usb_device_options()
        defaults = (
            user_input
            or getattr(self, "_reauth_defaults", {})
            or {
                CONF_HOST: entry.data.get(CONF_HOST, ""),
                CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
                CONF_INTERFACE: entry.data.get(CONF_INTERFACE, DEFAULT_INTERFACE),
                CONF_MASTER_DEVICE: entry.data.get(CONF_MASTER_DEVICE, ""),
                CONF_MAX200_HOST: entry.data.get(CONF_MAX200_HOST, ""),
            }
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(
                    CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
                ): vol.All(int, vol.Range(min=1, max=65535)),
                vol.Required(
                    CONF_INTERFACE,
                    default=defaults.get(CONF_INTERFACE, DEFAULT_INTERFACE),
                ): str,
                vol.Optional(
                    CONF_MASTER_DEVICE,
                    default=defaults.get(CONF_MASTER_DEVICE, ""),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=master_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(
                    CONF_MAX200_HOST,
                    default=defaults.get(CONF_MAX200_HOST, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_socketcand", data_schema=schema, errors=errors
        )

    async def async_step_reauth_usb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth by re-probing with new USB connection details."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            conn = UsbConnection.from_config(user_input)
            error = await self._async_probe(conn)
            if error:
                errors["base"] = error
            else:
                new_data = {
                    **entry.data,
                    CONF_DEVICE: user_input[CONF_DEVICE],
                }
                if master := user_input.get(CONF_MASTER_DEVICE, ""):
                    new_data[CONF_MASTER_DEVICE] = master
                else:
                    new_data.pop(CONF_MASTER_DEVICE, None)
                if max200_host := user_input.get(CONF_MAX200_HOST, ""):
                    new_data[CONF_MAX200_HOST] = max200_host
                else:
                    new_data.pop(CONF_MAX200_HOST, None)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{CONNECTION_TYPE_USB}:{user_input[CONF_DEVICE]}",
                    data=new_data,
                    reason="reauth_successful",
                )

        device_options = await self._build_usb_device_options()
        defaults = {
            CONF_DEVICE: entry.data.get(CONF_DEVICE, ""),
            CONF_MASTER_DEVICE: entry.data.get(CONF_MASTER_DEVICE, ""),
            CONF_MAX200_HOST: entry.data.get(CONF_MAX200_HOST, ""),
        }

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE, default=defaults.get(CONF_DEVICE, "")
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MASTER_DEVICE,
                    default=defaults.get(CONF_MASTER_DEVICE, ""),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(
                    CONF_MAX200_HOST,
                    default=defaults.get(CONF_MAX200_HOST, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_usb", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user-initiated reconfiguration of connection parameters."""
        errors: dict[str, str] = {}

        if user_input is not None:
            connection_type = user_input.get(CONF_CONNECTION_TYPE)
            if connection_type == CONNECTION_TYPE_SOCKETCAND:
                return await self.async_step_reconfigure_socketcand(None)
            elif connection_type == CONNECTION_TYPE_USB:
                return await self.async_step_reconfigure_usb(None)
            else:
                errors[CONF_CONNECTION_TYPE] = "invalid_connection_type"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._get_connection_type_schema(),
            errors=errors,
        )

    async def async_step_reconfigure_socketcand(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure with new socketcand connection details."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            conn = SocketcandConnection.from_config(user_input)
            error = await self._async_probe(conn)
            if error:
                errors["base"] = error
            else:
                data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_SOCKETCAND,
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_INTERFACE: user_input[CONF_INTERFACE],
                }
                if master := user_input.get(CONF_MASTER_DEVICE, ""):
                    data[CONF_MASTER_DEVICE] = master
                if max200_host := user_input.get(CONF_MAX200_HOST, ""):
                    data[CONF_MAX200_HOST] = max200_host
                await self.async_set_unique_id(
                    f"{CONNECTION_TYPE_SOCKETCAND}:{user_input[CONF_HOST]}:{user_input[CONF_PORT]}/{user_input[CONF_INTERFACE]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{CONNECTION_TYPE_SOCKETCAND}:{user_input[CONF_HOST]}:{user_input[CONF_PORT]}/{user_input[CONF_INTERFACE]}",
                    data=data,
                    reason="reconfigure_successful",
                )

        master_options = await self._build_usb_device_options()
        defaults = {
            CONF_HOST: entry.data.get(CONF_HOST, ""),
            CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
            CONF_INTERFACE: entry.data.get(CONF_INTERFACE, DEFAULT_INTERFACE),
            CONF_MASTER_DEVICE: entry.data.get(CONF_MASTER_DEVICE, ""),
            CONF_MAX200_HOST: entry.data.get(CONF_MAX200_HOST, ""),
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults[CONF_HOST]): str,
                vol.Required(CONF_PORT, default=defaults[CONF_PORT]): vol.All(
                    int, vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_INTERFACE, default=defaults[CONF_INTERFACE]): str,
                vol.Optional(
                    CONF_MASTER_DEVICE, default=defaults[CONF_MASTER_DEVICE]
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=master_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(CONF_MAX200_HOST, default=defaults[CONF_MAX200_HOST]): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure_socketcand", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure_usb(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure with new USB connection details."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            conn = UsbConnection.from_config(user_input)
            error = await self._async_probe(conn)
            if error:
                errors["base"] = error
            else:
                data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_USB,
                    CONF_DEVICE: user_input[CONF_DEVICE],
                }
                if master := user_input.get(CONF_MASTER_DEVICE, ""):
                    data[CONF_MASTER_DEVICE] = master
                if max200_host := user_input.get(CONF_MAX200_HOST, ""):
                    data[CONF_MAX200_HOST] = max200_host
                await self.async_set_unique_id(
                    f"{CONNECTION_TYPE_USB}:{user_input[CONF_DEVICE]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=f"{CONNECTION_TYPE_USB}:{user_input[CONF_DEVICE]}",
                    data=data,
                    reason="reconfigure_successful",
                )

        device_options = await self._build_usb_device_options()
        defaults = {
            CONF_DEVICE: entry.data.get(CONF_DEVICE, ""),
            CONF_MASTER_DEVICE: entry.data.get(CONF_MASTER_DEVICE, ""),
            CONF_MAX200_HOST: entry.data.get(CONF_MAX200_HOST, ""),
        }
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE, default=defaults[CONF_DEVICE]
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_MASTER_DEVICE, default=defaults[CONF_MASTER_DEVICE]
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(CONF_MAX200_HOST, default=defaults[CONF_MAX200_HOST]): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure_usb", data_schema=schema, errors=errors
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration."""
        types: dict[str, type[ConfigSubentryFlow]] = {
            SUBENTRY_TYPE_MODULE: ModuleSubentryFlowHandler,
            SUBENTRY_TYPE_MOOD: MoodSubentryFlowHandler,
        }
        if config_entry.data.get(CONF_MASTER_DEVICE) or config_entry.data.get(
            CONF_MAX200_HOST
        ):
            types[SUBENTRY_TYPE_MODULE_IMPORT] = ModuleImportSubentryFlowHandler
        return types


class ModuleSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and reconfiguring a DOBISS module."""

    # ------------------------------------------------------------------ #
    # Add flow                                                             #
    # ------------------------------------------------------------------ #

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new module subentry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            module = user_input[CONF_MODULE].strip().upper()
            name = user_input.get(CONF_NAME, "").strip()
            dimmable: bool = user_input.get("dimmable", False)

            err = _validate_module(module)
            if err:
                errors[CONF_MODULE] = err
            else:
                entry = self._get_entry()
                existing_letters = {
                    sub.data[CONF_MODULE]
                    for sub in entry.subentries.values()
                    if sub.subentry_type == SUBENTRY_TYPE_MODULE
                }
                if module in existing_letters:
                    errors[CONF_MODULE] = "module_already_exists"
                else:
                    title = name or f"Module {module}"
                    return self.async_create_entry(
                        title=title,
                        data={
                            CONF_MODULE: module,
                            "dimmable": dimmable,
                            "outputs": {},
                        },
                        unique_id=f"module:{module}",
                    )

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_MODULE, default=defaults.get(CONF_MODULE, "A")): str,
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
                vol.Optional("dimmable", default=defaults.get("dimmable", False)): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # ------------------------------------------------------------------ #
    # Reconfigure - menu                                                   #
    # ------------------------------------------------------------------ #

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the reconfigure action menu."""
        subentry = self._get_reconfigure_subentry()
        outputs: dict[str, Any] = subentry.data.get("outputs", {})

        menu_options = ["add_light", "add_shutter", "add_switch"]
        if outputs:
            menu_options.extend(["edit_output", "remove_output"])
        menu_options.append("edit_module")

        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=menu_options,
        )

    # ------------------------------------------------------------------ #
    # add_light                                                            #
    # ------------------------------------------------------------------ #

    async def async_step_add_light(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a light output to this module."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            output: int = user_input["output"]
            name: str = user_input.get(CONF_NAME, "").strip()

            if output < 1 or output > OUTPUTS_PER_MODULE:
                errors["output"] = "invalid_output"
            else:
                outputs: dict[str, Any] = dict(subentry.data.get("outputs", {}))
                occupied = _occupied_outputs_in_module(outputs)
                if output in occupied:
                    errors["output"] = "duplicate_output"
                else:
                    outputs[str(output)] = {
                        "type": "light",
                        "name": name,
                    }
                    new_data = dict(subentry.data) | {"outputs": outputs}
                    return self.async_update_and_abort(
                        self._get_entry(),
                        subentry,
                        data=new_data,
                        title=subentry.title,
                    )

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required("output", default=defaults.get("output", 1)): int,
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(
            step_id="add_light", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------ #
    # add_shutter                                                          #
    # ------------------------------------------------------------------ #

    async def async_step_add_shutter(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a shutter output pair to this module."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            up_output: int = user_input["up_output"]
            down_output: int = user_input["down_output"]
            name: str = user_input.get(CONF_NAME, "").strip()

            if up_output < 1 or up_output > OUTPUTS_PER_MODULE:
                errors["up_output"] = "invalid_output"
            elif down_output < 1 or down_output > OUTPUTS_PER_MODULE:
                errors["down_output"] = "invalid_output"
            elif up_output == down_output:
                errors["base"] = "same_output"
            else:
                outputs: dict[str, Any] = dict(subentry.data.get("outputs", {}))
                occupied = _occupied_outputs_in_module(outputs)
                if up_output in occupied:
                    errors["up_output"] = "duplicate_output"
                elif down_output in occupied:
                    errors["down_output"] = "duplicate_output"
                else:
                    outputs[str(up_output)] = {
                        "type": "shutter",
                        "down_output": down_output,
                        "name": name,
                    }
                    new_data = dict(subentry.data) | {"outputs": outputs}
                    return self.async_update_and_abort(
                        self._get_entry(),
                        subentry,
                        data=new_data,
                        title=subentry.title,
                    )

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required("up_output", default=defaults.get("up_output", 1)): int,
                vol.Required(
                    "down_output", default=defaults.get("down_output", 2)
                ): int,
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(
            step_id="add_shutter", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------ #
    # add_switch                                                           #
    # ------------------------------------------------------------------ #

    async def async_step_add_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a switch output to this module."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            output: int = user_input["output"]
            name: str = user_input.get(CONF_NAME, "").strip()

            if output < 1 or output > OUTPUTS_PER_MODULE:
                errors["output"] = "invalid_output"
            else:
                outputs: dict[str, Any] = dict(subentry.data.get("outputs", {}))
                occupied = _occupied_outputs_in_module(outputs)
                if output in occupied:
                    errors["output"] = "duplicate_output"
                else:
                    outputs[str(output)] = {
                        "type": "switch",
                        "name": name,
                    }
                    new_data = dict(subentry.data) | {"outputs": outputs}
                    return self.async_update_and_abort(
                        self._get_entry(),
                        subentry,
                        data=new_data,
                        title=subentry.title,
                    )

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required("output", default=defaults.get("output", 1)): int,
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(
            step_id="add_switch", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------ #
    # remove_output                                                        #
    # ------------------------------------------------------------------ #

    async def async_step_remove_output(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Remove an existing output from this module."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()
        outputs: dict[str, Any] = dict(subentry.data.get("outputs", {}))

        if not outputs:
            return self.async_abort(reason="no_outputs_to_remove")

        if user_input is not None:
            chosen = user_input["output"]
            if chosen not in outputs:
                errors["output"] = "invalid_output"
            else:
                outputs.pop(chosen)
                new_data = dict(subentry.data) | {"outputs": outputs}
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    data=new_data,
                    title=subentry.title,
                )

        options: list[SelectOptionDict] = []
        for output_str, cfg in sorted(outputs.items(), key=lambda kv: int(kv[0])):
            kind = cfg.get("type", "")
            label_name = cfg.get("name") or ""
            label = f"{output_str}: {kind} {label_name}".strip()
            options.append(SelectOptionDict(label=label, value=output_str))

        schema = vol.Schema(
            {
                vol.Required("output"): SelectSelector(
                    SelectSelectorConfig(
                        options=options, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="remove_output", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------ #
    # edit_output                                                          #
    # ------------------------------------------------------------------ #

    async def async_step_edit_output(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick an existing output to change its type."""
        subentry = self._get_reconfigure_subentry()
        outputs: dict[str, Any] = dict(subentry.data.get("outputs", {}))

        if not outputs:
            return self.async_abort(reason="no_outputs_to_edit")

        if user_input is not None:
            chosen = user_input["output"]
            if chosen not in outputs:
                return self.async_abort(reason="no_outputs_to_edit")
            self._edit_output_key = chosen
            return await self.async_step_edit_output_type()

        options: list[SelectOptionDict] = []
        for output_str, cfg in sorted(outputs.items(), key=lambda kv: int(kv[0])):
            kind = cfg.get("type", "")
            label_name = cfg.get("name") or ""
            label = f"{output_str}: {kind} {label_name}".strip()
            options.append(SelectOptionDict(label=label, value=output_str))

        schema = vol.Schema(
            {
                vol.Required("output"): SelectSelector(
                    SelectSelectorConfig(
                        options=options, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="edit_output", data_schema=schema, errors={}
        )

    async def async_step_edit_output_type(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick a new type for the selected output."""
        subentry = self._get_reconfigure_subentry()
        output_key: str = self._edit_output_key
        outputs: dict[str, Any] = dict(subentry.data.get("outputs", {}))
        current_cfg = outputs.get(output_key, {})
        current_type = current_cfg.get("type", "light")

        if user_input is not None:
            new_type: str = user_input["type"]
            if new_type == "shutter":
                return await self.async_step_edit_output_down()
            new_cfg: dict[str, Any] = {
                "type": new_type,
                "name": current_cfg.get("name", ""),
            }
            outputs[output_key] = new_cfg
            new_data = dict(subentry.data) | {"outputs": outputs}
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data=new_data,
                title=subentry.title,
            )

        schema = vol.Schema(
            {
                vol.Required("type", default=current_type): SelectSelector(
                    SelectSelectorConfig(
                        options=["light", "shutter", "switch"],
                        translation_key="output_type",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="edit_output_type", data_schema=schema, errors={}
        )

    async def async_step_edit_output_down(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for the down output when changing to shutter."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()
        output_key: str = self._edit_output_key
        outputs: dict[str, Any] = dict(subentry.data.get("outputs", {}))
        current_cfg = outputs.get(output_key, {})
        up_output = int(output_key)

        if user_input is not None:
            down_output: int = user_input["down_output"]
            if down_output < 1 or down_output > OUTPUTS_PER_MODULE:
                errors["down_output"] = "invalid_output"
            elif down_output == up_output:
                errors["base"] = "same_output"
            if not errors:
                # Remove existing entry on the down_output slot if present.
                outputs.pop(str(down_output), None)
                outputs[output_key] = {
                    "type": "shutter",
                    "down_output": down_output,
                    "name": current_cfg.get("name", ""),
                }
                new_data = dict(subentry.data) | {"outputs": outputs}
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    data=new_data,
                    title=subentry.title,
                )

        defaults = user_input or {}
        current_down = current_cfg.get("down_output", up_output + 1)
        schema = vol.Schema(
            {
                vol.Required("up_output", default=up_output): int,
                vol.Required(
                    "down_output",
                    default=defaults.get("down_output", current_down),
                ): int,
            }
        )
        return self.async_show_form(
            step_id="edit_output_down", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------ #
    # rename_module                                                        #
    # ------------------------------------------------------------------ #

    async def async_step_edit_module(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the module letter, friendly name, and dimmable flag."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            module = user_input[CONF_MODULE].strip().upper()
            name = user_input.get(CONF_NAME, "").strip()
            dimmable: bool = user_input.get("dimmable", False)

            err = _validate_module(module)
            if err:
                errors[CONF_MODULE] = err
            else:
                entry = self._get_entry()
                existing_letters = {
                    sub.data[CONF_MODULE]
                    for sub_id, sub in entry.subentries.items()
                    if sub.subentry_type == SUBENTRY_TYPE_MODULE
                    and sub_id != subentry.subentry_id
                }
                if module in existing_letters:
                    errors[CONF_MODULE] = "module_already_exists"
                else:
                    title = name or f"Module {module}"
                    new_data = dict(subentry.data) | {
                        CONF_MODULE: module,
                        "dimmable": dimmable,
                    }
                    return self.async_update_and_abort(
                        entry,
                        subentry,
                        data=new_data,
                        title=title,
                        unique_id=f"module:{module}",
                    )

        defaults = user_input or {
            CONF_MODULE: subentry.data.get(CONF_MODULE, "A"),
            CONF_NAME: subentry.title,
            "dimmable": subentry.data.get("dimmable", False),
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_MODULE, default=defaults.get(CONF_MODULE, "A")): str,
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
                vol.Optional("dimmable", default=defaults.get("dimmable", False)): bool,
            }
        )
        return self.async_show_form(
            step_id="edit_module", data_schema=schema, errors=errors
        )


class ModuleImportSubentryFlowHandler(ConfigSubentryFlow):
    """Handle the "Import modules from Max200" subentry flow."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Download module config from Max200 and create subentries."""
        entry = self._get_entry()
        max200_host = entry.data.get(CONF_MAX200_HOST)
        master_device = entry.data.get(CONF_MASTER_DEVICE)

        if not max200_host and not master_device:
            return self.async_abort(reason="no_max200_connection")

        existing_letters = {
            sub.data[CONF_MODULE]
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_MODULE
        }

        dl_config: Callable[[], Awaitable[list[tuple[str, int]]]]
        dl_names: Callable[[int], Awaitable[dict[int, str]]]

        if max200_host:
            tcp_client = Max200TcpClient(max200_host)
            dl_config = tcp_client.download_config

            async def _tcp_dl_names(module_index: int) -> dict[int, str]:
                names: dict[int, str] = {}
                for output_index in range(OUTPUTS_PER_MODULE):
                    name = await tcp_client.download_output_name(
                        module_index, output_index
                    )
                    if name is not None:
                        names[output_index] = name
                return names

            dl_names = _tcp_dl_names
        else:
            assert master_device
            serial_client = Max200SerialClient(master_device)
            hass = self.hass

            async def dl_config() -> list[tuple[str, int]]:
                return await hass.async_add_executor_job(
                    serial_client.download_config,
                )

            async def dl_names(module_index: int) -> dict[int, str]:
                return await hass.async_add_executor_job(
                    serial_client.download_module_output_names,
                    module_index,
                    OUTPUTS_PER_MODULE,
                )

        try:
            modules = await dl_config()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to download config from Max200: %s", err)
            return self.async_abort(reason="import_failed")

        new_modules = [
            (letter, idx) for letter, idx in modules if letter not in existing_letters
        ]

        if not new_modules:
            return self.async_abort(reason="no_new_modules")

        all_names: dict[str, dict[int, str]] = {}
        for letter, module_index in new_modules:
            try:
                all_names[letter] = await dl_names(module_index)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to fetch output names for module %s: %s",
                    letter,
                    err,
                )
                all_names[letter] = {}

        imported = 0
        for letter, _module_index in new_modules:
            outputs: dict[str, dict[str, str]] = {}
            for output_index, name in all_names[letter].items():
                outputs[str(output_index + 1)] = {
                    "type": "light",
                    "name": name,
                }

            subentry = ConfigSubentry(
                data=MappingProxyType(
                    {
                        CONF_MODULE: letter,
                        "dimmable": False,
                        "outputs": outputs,
                    }
                ),
                subentry_type=SUBENTRY_TYPE_MODULE,
                title=f"Module {letter}",
                unique_id=f"module:{letter}",
            )
            if self.hass.config_entries.async_add_subentry(entry, subentry):
                imported += 1

        return self.async_abort(
            reason="import_successful",
            description_placeholders={"count": str(imported)},
        )


CONF_MOOD_NUMBER = "mood_number"


class MoodSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and reconfiguring a DOBISS mood."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new mood subentry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mood_number: int = user_input[CONF_MOOD_NUMBER]
            name: str = user_input.get(CONF_NAME, "").strip()

            if not 0 <= mood_number <= 99:
                errors[CONF_MOOD_NUMBER] = "mood_number_out_of_range"
            else:
                entry = self._get_entry()
                existing_moods = {
                    sub.data[CONF_MOOD_NUMBER]
                    for sub in entry.subentries.values()
                    if sub.subentry_type == SUBENTRY_TYPE_MOOD
                }
                if mood_number in existing_moods:
                    errors[CONF_MOOD_NUMBER] = "mood_already_exists"
                else:
                    title = name or f"Mood {mood_number}"
                    return self.async_create_entry(
                        title=title,
                        data={CONF_MOOD_NUMBER: mood_number},
                        unique_id=f"mood:{mood_number}",
                    )

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MOOD_NUMBER, default=defaults.get(CONF_MOOD_NUMBER, 0)
                ): vol.All(int, vol.Range(min=0, max=99)),
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure a mood (rename only, mood number is immutable)."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            name: str = user_input.get(CONF_NAME, "").strip()
            title = name or f"Mood {subentry.data[CONF_MOOD_NUMBER]}"
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data=dict(subentry.data),
                title=title,
            )

        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=subentry.title): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )
