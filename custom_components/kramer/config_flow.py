"""Config, reconfigure and options flows for the Kramer integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
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

from . import (
    default_input_names,
    default_output_names,
    effective_names,
    legacy_input_names,
    legacy_output_names,
)
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
    CONF_PRESET_DESCRIPTIONS,
    CONF_PRESET_SLOTS,
    DEFAULT_MACHINE,
    DEFAULT_PORT,
    DOMAIN,
    SOURCE_MIXED,
    SOURCE_OFF,
)
from .entity import KramerData
from .models import MAX_PORTS, MAX_PRESET_SLOTS, MODELS, OTHER, resolve_model_key, resolve_preset_slots
from .presets import (
    parse_descriptions,
    serialise_descriptions,
    slot_label,
    summarise_routes,
    validate_description,
)
from .protocol import KramerConnectionError, KramerError, KramerTimeoutError, async_probe

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
                vol.Required(
                    CONF_MODEL,
                    default=resolve_model_key(cur.get(CONF_MODEL))
                    or (OTHER if cur.get(CONF_MODEL) == OTHER else next(iter(MODELS))),
                ): _model_selector(),
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
                    CONF_PRESET_SLOTS: int(user_input[CONF_PRESET_SLOTS]),
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
                vol.Required(CONF_PRESET_SLOTS, default=int(cur.get(CONF_PRESET_SLOTS, 0))): _number(
                    0, MAX_PRESET_SLOTS
                ),
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
    """Rename inputs and outputs, and work with stored setups (presets)."""

    def __init__(self) -> None:
        """Initialise."""
        self._slot = 0
        self._contents: dict[int, str] = {}

    # -- helpers --------------------------------------------------------

    def _live_data(self) -> KramerData | None:
        """Return the running unit's data, or None if it is not loaded and answering."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return None
        data: KramerData = entry.runtime_data
        return data if data.client.available else None

    def _descriptions(self) -> dict[int, str]:
        return parse_descriptions(
            self.config_entry.options.get(CONF_PRESET_DESCRIPTIONS), resolve_preset_slots(self.config_entry.data)
        )

    def _placeholders(self, data: KramerData) -> dict[str, str]:
        occupied = data.client.preset_defined.get(self._slot)
        state = "Occupied" if occupied else "Blank" if occupied is False else "Not checked"
        return {
            "preset": f"{self._slot:02d}",
            "state": state,
            "description": self._descriptions().get(self._slot, "(none)"),
            "contents": self._contents.get(self._slot, ""),
            "error": "",
        }

    async def _read_contents(self, data: KramerData) -> None:
        """Remember a readable summary of what the chosen slot holds."""
        if self._slot in self._contents:
            return
        try:
            stored = await data.client.async_read_preset(self._slot)
        except KramerError:
            self._contents[self._slot] = "It could not be read just now."
            return
        self._contents[self._slot] = (
            f"It currently holds: video {summarise_routes(stored.video)}; audio {summarise_routes(stored.audio)}."
        )

    # -- steps ----------------------------------------------------------

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose between names and presets (models without presets go straight to names)."""
        if resolve_preset_slots(self.config_entry.data) > 0:
            return self.async_show_menu(step_id="init", menu_options=["names", "presets"])
        return await self.async_step_names()

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
                return self.async_create_entry(
                    data={
                        **entry.options,
                        CONF_INPUT_NAMES: [n if n != d else "" for n, d in zip(in_names, in_defaults, strict=True)],
                        CONF_OUTPUT_NAMES: [n if n != d else "" for n, d in zip(out_names, out_defaults, strict=True)],
                    }
                )

        saved_in = effective_names(entry.options.get(CONF_INPUT_NAMES), in_defaults, legacy_input_names(inputs))
        saved_out = effective_names(entry.options.get(CONF_OUTPUT_NAMES), out_defaults, legacy_output_names(outputs))
        multiline = TextSelector(TextSelectorConfig(multiline=True))
        schema = vol.Schema(
            {
                vol.Required(CONF_INPUT_NAMES, default="\n".join(saved_in)): multiline,
                vol.Required(CONF_OUTPUT_NAMES, default="\n".join(saved_out)): multiline,
            }
        )
        return self.async_show_form(step_id="names", data_schema=schema, errors=errors)

    async def async_step_presets(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a slot (every slot is listed, with whether it is occupied on the unit)."""
        data = self._live_data()
        if data is None:
            return self.async_abort(reason="unit_unavailable")
        if user_input is not None:
            self._slot = int(user_input["preset"])
            self._contents.pop(self._slot, None)
            return await self.async_step_slot()

        await data.client.async_scan_presets()
        descriptions = self._descriptions()
        options = [
            {
                "value": str(slot),
                "label": slot_label(slot, data.client.preset_defined.get(slot), descriptions.get(slot)),
            }
            for slot in range(1, data.preset_slots + 1)
        ]
        schema = vol.Schema(
            {
                vol.Required("preset", default=str(self._slot or 1)): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(step_id="presets", data_schema=schema)

    async def async_step_slot(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose what to do with the slot."""
        data = self._live_data()
        if data is None:
            return self.async_abort(reason="unit_unavailable")
        menu = ["store", "recall", "describe"]
        if self._slot in self._descriptions():
            menu.append("forget")
        menu.append("presets")
        return self.async_show_menu(
            step_id="slot", menu_options=menu, description_placeholders=self._placeholders(data)
        )

    def _scope_field(self, data: KramerData) -> dict[Any, Any]:
        """Return the 'everything or audio only' field for models with separate audio."""
        if not data.supports_modes:
            return {}
        return {
            vol.Required("scope", default="all"): SelectSelector(
                SelectSelectorConfig(
                    options=["all", "audio"], mode=SelectSelectorMode.LIST, translation_key="preset_scope"
                )
            )
        }

    async def async_step_store(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Store the present routing in the slot, confirming first if it would overwrite."""
        data = self._live_data()
        if data is None:
            return self.async_abort(reason="unit_unavailable")
        client = data.client
        try:
            occupied = await client.async_preset_defined(self._slot)
        except KramerError:
            return self.async_abort(reason="unit_unavailable")
        if occupied:
            await self._read_contents(data)
        errors: dict[str, str] = {}
        placeholders = self._placeholders(data)

        if user_input is not None:
            if occupied and not user_input.get("confirm"):
                errors["confirm"] = "confirm_required"
            else:
                try:
                    await client.async_store_preset(self._slot, user_input.get("scope") == "audio")
                except KramerError as err:
                    errors["base"] = "command_failed"
                    placeholders["error"] = str(err)
                else:
                    return self.async_abort(reason="preset_stored", description_placeholders=placeholders)

        fields: dict[Any, Any] = self._scope_field(data)
        if occupied:
            fields[vol.Required("confirm", default=False)] = BooleanSelector()
        placeholders["warning"] = (
            f"**Preset {self._slot:02d} is occupied and storing will overwrite it.** {placeholders['contents']}"
            if occupied
            else f"Preset {self._slot:02d} is blank, so nothing will be overwritten."
        )
        return self.async_show_form(
            step_id="store", data_schema=vol.Schema(fields), errors=errors, description_placeholders=placeholders
        )

    async def async_step_recall(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Recall the slot, after confirmation."""
        data = self._live_data()
        if data is None:
            return self.async_abort(reason="unit_unavailable")
        client = data.client
        try:
            occupied = await client.async_preset_defined(self._slot)
        except KramerError:
            return self.async_abort(reason="unit_unavailable")
        placeholders = self._placeholders(data)
        if not occupied:
            return self.async_abort(reason="slot_blank", description_placeholders=placeholders)
        await self._read_contents(data)
        placeholders = self._placeholders(data)
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get("confirm"):
                errors["confirm"] = "confirm_required"
            else:
                try:
                    await client.async_recall_preset(self._slot, user_input.get("scope") == "audio")
                except KramerError as err:
                    errors["base"] = "command_failed"
                    placeholders["error"] = str(err)
                else:
                    return self.async_abort(reason="preset_recalled", description_placeholders=placeholders)

        fields: dict[Any, Any] = self._scope_field(data)
        fields[vol.Required("confirm", default=False)] = BooleanSelector()
        return self.async_show_form(
            step_id="recall", data_schema=vol.Schema(fields), errors=errors, description_placeholders=placeholders
        )

    async def async_step_describe(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set the HASS description for the slot (kept in Home Assistant, not on the unit)."""
        data = self._live_data()
        if data is None:
            return self.async_abort(reason="unit_unavailable")
        descriptions = self._descriptions()
        errors: dict[str, str] = {}
        if user_input is not None:
            text = user_input["description"].strip()
            if error := validate_description(text, self._slot, descriptions):
                errors["description"] = error
            else:
                descriptions[self._slot] = text
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_PRESET_DESCRIPTIONS: serialise_descriptions(descriptions)}
                )
        schema = vol.Schema({vol.Required("description", default=descriptions.get(self._slot, "")): TextSelector()})
        return self.async_show_form(
            step_id="describe",
            data_schema=schema,
            errors=errors,
            description_placeholders=self._placeholders(data),
        )

    async def async_step_forget(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Remove the HASS description. The preset on the unit is not touched."""
        data = self._live_data()
        if data is None:
            return self.async_abort(reason="unit_unavailable")
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["confirm"] = "confirm_required"
            else:
                descriptions = self._descriptions()
                descriptions.pop(self._slot, None)
                return self.async_create_entry(
                    data={**self.config_entry.options, CONF_PRESET_DESCRIPTIONS: serialise_descriptions(descriptions)}
                )
        schema = vol.Schema({vol.Required("confirm", default=False): BooleanSelector()})
        return self.async_show_form(
            step_id="forget",
            data_schema=schema,
            errors=errors,
            description_placeholders=self._placeholders(data),
        )
