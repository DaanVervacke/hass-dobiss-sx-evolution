"""Config flow for DOBISS SX Evolution."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
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

from .const import (
    CONF_HOST,
    CONF_INTERFACE,
    CONF_MODULE,
    CONF_NAME,
    CONF_PORT,
    DEFAULT_INTERFACE,
    DEFAULT_PORT,
    DOMAIN,
    SUBENTRY_TYPE_MODULE,
)
from .controller import make_bus_sync

_LOGGER = logging.getLogger(__name__)

CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_INTERFACE, default=DEFAULT_INTERFACE): str,
    }
)


def _probe_bus_sync(host: str, port: int, interface: str) -> None:
    """Open and immediately close the bus to validate connectivity.

    Must be called from an executor thread. Raises on any failure.
    """
    bus = make_bus_sync(host, port, interface)
    try:
        pass
    finally:
        bus.shutdown()


def _validate_module(module: str) -> str | None:
    """Return error key if module letter is invalid, else None."""
    if len(module) != 1 or not module.isalpha():
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect socketcand connection details and create the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}/{user_input[CONF_INTERFACE]}"
            )
            self._abort_if_unique_id_configured()
            try:
                await self.hass.async_add_executor_job(
                    _probe_bus_sync,
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_INTERFACE],
                )
            except OSError as err:
                _LOGGER.debug("CAN probe failed (OSError): %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("CAN probe failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                title = (
                    f"Max200 ({user_input[CONF_HOST]}:"
                    f"{user_input[CONF_PORT]}/"
                    f"{user_input[CONF_INTERFACE]})"
                )
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_INTERFACE: user_input[CONF_INTERFACE],
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=CONNECTION_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the connection details change."""
        self._reauth_defaults = {
            CONF_HOST: entry_data.get(CONF_HOST, ""),
            CONF_PORT: entry_data.get(CONF_PORT, DEFAULT_PORT),
            CONF_INTERFACE: entry_data.get(CONF_INTERFACE, DEFAULT_INTERFACE),
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth by re-probing with new connection details."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    _probe_bus_sync,
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_INTERFACE],
                )
            except OSError as err:
                _LOGGER.debug("CAN reauth probe failed (OSError): %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("CAN reauth probe failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                new_data = {
                    **entry.data,
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_INTERFACE: user_input[CONF_INTERFACE],
                }
                return self.async_update_reload_and_abort(
                    entry,
                    data=new_data,
                    reason="reauth_successful",
                )

        defaults = user_input or getattr(self, "_reauth_defaults", {}) or {
            CONF_HOST: entry.data.get(CONF_HOST, ""),
            CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
            CONF_INTERFACE: entry.data.get(CONF_INTERFACE, DEFAULT_INTERFACE),
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(
                    CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
                ): int,
                vol.Required(
                    CONF_INTERFACE,
                    default=defaults.get(CONF_INTERFACE, DEFAULT_INTERFACE),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentry types supported by this integration."""
        return {
            SUBENTRY_TYPE_MODULE: ModuleSubentryFlowHandler,
        }


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

        menu_options = ["add_light", "add_shutter", "edit_module"]
        if outputs:
            menu_options.insert(2, "remove_output")

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

            if output < 1:
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

            if up_output < 1:
                errors["up_output"] = "invalid_output"
            elif down_output < 1:
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
                vol.Required("down_output", default=defaults.get("down_output", 2)): int,
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(
            step_id="add_shutter", data_schema=schema, errors=errors
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
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                ),
            }
        )
        return self.async_show_form(
            step_id="remove_output", data_schema=schema, errors=errors
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
