"""Config, reconfigure and options flows for the Kramer integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from . import default_input_names, default_output_names
from .const import (
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_INPUTS,
    CONF_MACHINE,
    CONF_MODEL,
    CONF_MODEL_NAME,
    CONF_MODES,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
    CONF_PORT,
    DEFAULT_MACHINE,
    DEFAULT_PORT,
    DOMAIN,
    SOURCE_MIXED,
    SOURCE_OFF,
)
from .models import MAX_PORTS, MODELS, OTHER
from .protocol import KramerConnectionError, KramerTimeoutError, async_probe

CONF_NAME = "name"


def _number(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX))


def _model_selector() -> SelectSelector:
    options = [{"value": k, "label": m.name} for k, m in MODELS.items()]
    options.append({"value": OTHER, "label": "Other (enter the size yourself)"})
    return SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))


class KramerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup and reconfiguration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._data: dict[str, Any] = {}

    # -- helpers --------------------------------------------------------

    def _current(self) -> dict[str, Any]:
        """Values to pre-fill: the entry being reconfigured, else defaults."""
        if self.source == "reconfigure":
            entry = self._get_reconfigure_entry()
            return {CONF_NAME: entry.title, **entry.data}
        return {}

    async def _finish(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Test the connection and save. Returns None if there were errors."""
        data = self._data
        host, port, machine = data[CONF_HOST], data[CONF_PORT], data[CONF_MACHINE]
        skip_probe = False
        if self.source == "reconfigure":
            old = self._get_reconfigure_entry().data
            skip_probe = (
                old.get(CONF_HOST) == host
                and int(old.get(CONF_PORT, 0)) == port
                and int(old.get(CONF_MACHINE, 0)) == machine
            )
        if not skip_probe:
            try:
                await async_probe(host, port, machine)
            except KramerTimeoutError:
                errors["base"] = "no_reply"
                return None
            except KramerConnectionError:
                errors["base"] = "cannot_connect"
                return None

        title = data.pop(CONF_NAME)
        if self.source == "reconfigure":
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(), title=title, data=data)
        await self.async_set_unique_id(f"{host}:{port}:{machine}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    # -- steps ----------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """First screen: connection details and model."""
        errors: dict[str, str] = {}
        cur = self._current()
        if user_input is not None:
            self._data = {
                CONF_NAME: user_input[CONF_NAME].strip(),
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_PORT: int(user_input[CONF_PORT]),
                CONF_MACHINE: int(user_input[CONF_MACHINE]),
            }
            model = user_input[CONF_MODEL]
            if model == OTHER:
                return await self.async_step_other()
            m = MODELS[model]
            self._data.update(
                {
                    CONF_MODEL: m.key,
                    CONF_MODEL_NAME: m.name,
                    CONF_INPUTS: m.inputs,
                    CONF_OUTPUTS: m.outputs,
                    CONF_MODES: m.supports_modes,
                }
            )
            if (result := await self._finish(errors)) is not None:
                return result

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=cur.get(CONF_NAME, "Kramer Matrix")): TextSelector(),
                vol.Required(CONF_HOST, default=cur.get(CONF_HOST, "")): TextSelector(),
                vol.Required(CONF_PORT, default=int(cur.get(CONF_PORT, DEFAULT_PORT))): _number(1, 65535),
                vol.Required(CONF_MACHINE, default=int(cur.get(CONF_MACHINE, DEFAULT_MACHINE))): _number(1, 15),
                vol.Required(CONF_MODEL, default=cur.get(CONF_MODEL, next(iter(MODELS)))): _model_selector(),
            }
        )
        step = "reconfigure" if self.source == "reconfigure" else "user"
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the setup screens, pre-filled."""
        return await self.async_step_user(user_input)

    async def async_step_other(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Second screen for models not in the library."""
        errors: dict[str, str] = {}
        cur = self._current()
        if user_input is not None:
            self._data.update(
                {
                    CONF_MODEL: OTHER,
                    CONF_MODEL_NAME: user_input[CONF_MODEL_NAME].strip() or "Matrix switcher",
                    CONF_INPUTS: int(user_input[CONF_INPUTS]),
                    CONF_OUTPUTS: int(user_input[CONF_OUTPUTS]),
                    CONF_MODES: user_input[CONF_MODES],
                }
            )
            if (result := await self._finish(errors)) is not None:
                return result

        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL_NAME, default=cur.get(CONF_MODEL_NAME, "")): TextSelector(),
                vol.Required(CONF_INPUTS, default=int(cur.get(CONF_INPUTS, 8))): _number(1, MAX_PORTS),
                vol.Required(CONF_OUTPUTS, default=int(cur.get(CONF_OUTPUTS, 8))): _number(1, MAX_PORTS),
                vol.Required(CONF_MODES, default=bool(cur.get(CONF_MODES, False))): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="other", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return KramerOptionsFlow()


def _parse_names(text: str, defaults: list[str]) -> tuple[list[str], str | None]:
    """Turn one-name-per-line text into a full list, or return an error key."""
    lines = [line.strip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    if len(lines) > len(defaults):
        return [], "too_many_names"
    names = [lines[i] if i < len(lines) and lines[i] else d for i, d in enumerate(defaults)]
    return names, None


class KramerOptionsFlow(OptionsFlow):
    """Rename inputs and outputs after setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the names, one per line."""
        entry = self.config_entry
        inputs, outputs = int(entry.data[CONF_INPUTS]), int(entry.data[CONF_OUTPUTS])
        in_defaults, out_defaults = default_input_names(inputs), default_output_names(outputs)
        errors: dict[str, str] = {}

        if user_input is not None:
            in_names, err_in = _parse_names(user_input[CONF_INPUT_NAMES], in_defaults)
            out_names, err_out = _parse_names(user_input[CONF_OUTPUT_NAMES], out_defaults)
            if err_in:
                errors[CONF_INPUT_NAMES] = err_in
            elif {n.casefold() for n in in_names} & {SOURCE_OFF.casefold(), SOURCE_MIXED.casefold()}:
                errors[CONF_INPUT_NAMES] = "reserved_name"
            elif len({n.casefold() for n in in_names}) != len(in_names):
                errors[CONF_INPUT_NAMES] = "duplicate_names"
            if err_out:
                errors[CONF_OUTPUT_NAMES] = err_out
            elif len({n.casefold() for n in out_names}) != len(out_names):
                errors[CONF_OUTPUT_NAMES] = "duplicate_names"
            if not errors:
                return self.async_create_entry(data={CONF_INPUT_NAMES: in_names, CONF_OUTPUT_NAMES: out_names})

        saved_in = entry.options.get(CONF_INPUT_NAMES) or in_defaults
        saved_out = entry.options.get(CONF_OUTPUT_NAMES) or out_defaults
        multiline = TextSelector(TextSelectorConfig(multiline=True))
        schema = vol.Schema(
            {
                vol.Required(CONF_INPUT_NAMES, default="\n".join(saved_in)): multiline,
                vol.Required(CONF_OUTPUT_NAMES, default="\n".join(saved_out)): multiline,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
