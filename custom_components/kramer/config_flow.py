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
    CONF_AUDIO_INPUT_NAMES,
    CONF_AUDIO_OUTPUT_NAMES,
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
    DOMAIN,
    SOURCE_MIXED,
    SOURCE_OFF,
)
from .entity import KramerData
from .models import (
    MAX_PORTS,
    MAX_PRESET_SLOTS,
    MODELS,
    OTHER,
    VP729_KEY,
    VP729_NAME,
    default_port_for,
    is_vp729,
    model_choices,
    resolve_model_key,
    resolve_notes,
    resolve_preset_slots,
)
from .presets import (
    parse_descriptions,
    serialise_descriptions,
    slot_label,
    summarise_routes,
    validate_description,
)
from .protocol import (
    KramerConnectionError,
    KramerError,
    KramerTimeoutError,
    async_probe,
)
from .vp729_protocol import async_probe_vp729

CONF_NAME = "name"


def _number(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX))


def _model_selector() -> SelectSelector:
    options = [{"value": key, "label": label} for key, label in model_choices()]
    return SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))


class KramerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup and reconfiguration."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._data: dict[str, Any] = {}
        self._model: str | None = None
        self._carried: dict[str, str] = {}

    # -- helpers --------------------------------------------------------

    def _current(self) -> dict[str, Any]:
        """Values to pre-fill: the entry being reconfigured, else defaults."""
        if self.source == "reconfigure":
            entry = self._get_reconfigure_entry()
            return {CONF_NAME: entry.title, **entry.data, **self._data}
        return dict(self._data)

    async def _finish(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Test the connection and save. Returns None if there were errors."""
        data = self._data
        vp729 = is_vp729(data)
        host, port = data[CONF_HOST], data[CONF_PORT]
        machine = data.get(CONF_MACHINE, DEFAULT_MACHINE)
        skip_probe = False
        if self.source == "reconfigure":
            old = self._get_reconfigure_entry().data
            skip_probe = (
                old.get(CONF_HOST) == host
                and int(old.get(CONF_PORT, 0)) == port
                and (vp729 or int(old.get(CONF_MACHINE, 0)) == machine)
            )
        if not skip_probe:
            try:
                if vp729:
                    await async_probe_vp729(host, port)
                else:
                    await async_probe(host, port, machine)
            except KramerTimeoutError:
                errors["base"] = "no_reply_vp729" if vp729 else "no_reply"
                return None
            except KramerConnectionError:
                errors["base"] = "cannot_connect"
                return None
            except KramerError:
                errors["base"] = "no_reply_vp729" if vp729 else "no_reply"
                return None

        title = data.pop(CONF_NAME)
        if self.source == "reconfigure":
            return self.async_update_reload_and_abort(self._get_reconfigure_entry(), title=title, data=data)
        await self.async_set_unique_id(f"{host}:{port}:vp729" if vp729 else f"{host}:{port}:{machine}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    # -- steps ----------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """First screen: which model it is. The address comes next."""
        errors: dict[str, str] = {}
        cur = self._current()
        if user_input is not None:
            model = user_input[CONF_MODEL]
            if self.source == "reconfigure" and (model == VP729_KEY) != is_vp729(self._get_reconfigure_entry().data):
                # A VP-729 and a matrix switcher are different kinds of unit: add a new entry instead.
                errors["base"] = "type_change"
                return self._model_form({**cur, CONF_MODEL: model}, errors)
            if model != self._model:
                self._data = {}  # a different model: start the next screens afresh
            self._model = model
            return await self.async_step_connection()
        return self._model_form(cur, errors)

    def _model_form(self, cur: dict[str, Any], errors: dict[str, str]) -> ConfigFlowResult:
        """Show the model screen."""
        choices = [key for key, _ in model_choices()]
        current = cur.get(CONF_MODEL)
        default = resolve_model_key(current) or (current if current in (OTHER, VP729_KEY) else choices[0])
        schema = vol.Schema({vol.Required(CONF_MODEL, default=default): _model_selector()})
        step = "reconfigure" if self.source == "reconfigure" else "user"
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the setup screens, pre-filled."""
        return await self.async_step_user(user_input)

    async def async_step_connection(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Second screen: name and address (and machine number for a matrix switcher)."""
        errors: dict[str, str] = self._carried
        self._carried = {}
        cur = self._current()
        model = self._model or VP729_KEY
        vp729 = model == VP729_KEY
        if user_input is not None:
            self._data.update(
                {
                    CONF_NAME: user_input[CONF_NAME].strip(),
                    CONF_HOST: user_input[CONF_HOST].strip(),
                    CONF_PORT: int(user_input[CONF_PORT]),
                }
            )
            if vp729:
                self._data.pop(CONF_MACHINE, None)
                self._data.update({CONF_MODEL: VP729_KEY, CONF_MODEL_NAME: VP729_NAME})
            else:
                self._data[CONF_MACHINE] = int(user_input[CONF_MACHINE])
                if model == OTHER:
                    return await self.async_step_other()
                else:
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
            cur = {**cur, **user_input}  # an error: keep what was typed

        label = dict(model_choices())[model]
        # Keep a saved port when the unit is being reconfigured as the same kind of unit.
        saved = self._get_reconfigure_entry().data if self.source == "reconfigure" else {}
        same_kind = bool(saved) and is_vp729(saved) == vp729
        port = int(cur[CONF_PORT]) if CONF_PORT in cur and (same_kind or self._data.get(CONF_PORT)) else default_port_for(model)
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=cur.get(CONF_NAME, "Kramer VP-729" if vp729 else "Kramer Matrix")): TextSelector(),
            vol.Required(CONF_HOST, default=cur.get(CONF_HOST, "")): TextSelector(),
            vol.Required(CONF_PORT, default=port): _number(1, 65535),
        }
        if not vp729:
            fields[vol.Required(CONF_MACHINE, default=int(cur.get(CONF_MACHINE, DEFAULT_MACHINE)))] = _number(1, 15)
        note = (
            "Its Ethernet port is 5000 unless changed on the unit's network module, and \"Password Required\" "
            "on that port must be set to No."
            if vp729
            else "The machine number is 1 unless units are daisy-chained."
        )
        return self.async_show_form(
            step_id="connection",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"model": label, "note": note},
        )

    async def async_step_other(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Third screen for models not in the library."""
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
            # The connection details are on the previous screen, so go back there with
            # the error and everything typed so far (on both screens) kept.
            self._carried = errors
            return await self.async_step_connection()

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
        filled = data.client.preset_defined.get(self._slot)
        state = "Filled" if filled else "Empty" if filled is False else "Not checked"
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
        """Show the device name and notes, then choose between names and presets."""
        if is_vp729(self.config_entry.data):
            return self.async_abort(reason="no_options")
        notes = resolve_notes(self.config_entry.data)
        has_presets = resolve_preset_slots(self.config_entry.data) > 0
        return self.async_show_menu(
            step_id="init",
            menu_options=["names", "presets"] if has_presets else ["names"],
            description_placeholders={"notes": notes or " ", "name": self.config_entry.title},
        )

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit the names, one per line (units with separate audio also have audio input names)."""
        entry = self.config_entry
        inputs, outputs = int(entry.data[CONF_INPUTS]), int(entry.data[CONF_OUTPUTS])
        breakaway = bool(entry.data.get(CONF_MODES, True))
        in_defaults = default_input_names(inputs, " Video" if breakaway else "")
        audio_defaults = default_input_names(inputs, " Audio")
        out_defaults = default_output_names(outputs, " Video" if breakaway else "")
        audio_out_defaults = default_output_names(outputs, " Audio")
        errors: dict[str, str] = {}

        def check_inputs(key: str, names: list[str], error: str | None) -> None:
            if error:
                errors[key] = error
            elif {n.casefold() for n in names} & {SOURCE_OFF.casefold(), SOURCE_MIXED.casefold()}:
                errors[key] = "reserved_name"
            elif len({n.casefold() for n in names}) != len(names):
                errors[key] = "duplicate_names"

        if user_input is not None:
            in_names, err_in = _parse_names(user_input[CONF_INPUT_NAMES], in_defaults)
            out_names, err_out = _parse_names(user_input[CONF_OUTPUT_NAMES], out_defaults)
            check_inputs(CONF_INPUT_NAMES, in_names, err_in)
            audio_names: list[str] = []
            if breakaway:
                audio_names, err_audio = _parse_names(user_input[CONF_AUDIO_INPUT_NAMES], audio_defaults)
                check_inputs(CONF_AUDIO_INPUT_NAMES, audio_names, err_audio)
            audio_out_names: list[str] = []
            if err_out:
                errors[CONF_OUTPUT_NAMES] = err_out
            elif len({n.casefold() for n in out_names}) != len(out_names):
                errors[CONF_OUTPUT_NAMES] = "duplicate_names"
            elif breakaway:
                audio_out_names, err_ao = _parse_names(user_input[CONF_AUDIO_OUTPUT_NAMES], audio_out_defaults)
                if err_ao:
                    errors[CONF_AUDIO_OUTPUT_NAMES] = err_ao
                elif len({n.casefold() for n in audio_out_names}) != len(audio_out_names):
                    errors[CONF_AUDIO_OUTPUT_NAMES] = "duplicate_names"
            if not errors:
                saved = {
                    **entry.options,
                    CONF_INPUT_NAMES: [n if n != d else "" for n, d in zip(in_names, in_defaults, strict=True)],
                    CONF_OUTPUT_NAMES: [n if n != d else "" for n, d in zip(out_names, out_defaults, strict=True)],
                }
                if breakaway:
                    saved[CONF_AUDIO_INPUT_NAMES] = [
                        n if n != d else "" for n, d in zip(audio_names, audio_defaults, strict=True)
                    ]
                    saved[CONF_AUDIO_OUTPUT_NAMES] = [
                        n if n != d else "" for n, d in zip(audio_out_names, audio_out_defaults, strict=True)
                    ]
                return self.async_create_entry(data=saved)

        saved_in = effective_names(entry.options.get(CONF_INPUT_NAMES), in_defaults, legacy_input_names(inputs))
        saved_out = effective_names(entry.options.get(CONF_OUTPUT_NAMES), out_defaults, legacy_output_names(outputs))
        multiline = TextSelector(TextSelectorConfig(multiline=True))
        typed = user_input or {}  # after an error, keep what was typed
        fields: dict[Any, Any] = {
            vol.Required(CONF_INPUT_NAMES, default=typed.get(CONF_INPUT_NAMES, "\n".join(saved_in))): multiline
        }
        if breakaway:
            saved_audio = effective_names(entry.options.get(CONF_AUDIO_INPUT_NAMES), audio_defaults)
            fields[
                vol.Required(CONF_AUDIO_INPUT_NAMES, default=typed.get(CONF_AUDIO_INPUT_NAMES, "\n".join(saved_audio)))
            ] = multiline
        fields[vol.Required(CONF_OUTPUT_NAMES, default=typed.get(CONF_OUTPUT_NAMES, "\n".join(saved_out)))] = multiline
        if breakaway:
            saved_audio_out = effective_names(
                entry.options.get(CONF_AUDIO_OUTPUT_NAMES), audio_out_defaults, legacy_output_names(outputs)
            )
            fields[
                vol.Required(
                    CONF_AUDIO_OUTPUT_NAMES, default=typed.get(CONF_AUDIO_OUTPUT_NAMES, "\n".join(saved_audio_out))
                )
            ] = multiline
        # Units with separate audio get a form with "Video output names" and "Audio output names".
        step = "names_av" if breakaway else "names"
        return self.async_show_form(step_id=step, data_schema=vol.Schema(fields), errors=errors)

    async def async_step_names_av(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the names step for units with separate audio (this form has its own wording)."""
        return await self.async_step_names(user_input)

    async def async_step_presets(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a slot (every slot is listed, with whether it is filled on the unit)."""
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
            filled = await client.async_preset_defined(self._slot)
        except KramerError:
            return self.async_abort(reason="unit_unavailable")
        if filled:
            await self._read_contents(data)
        errors: dict[str, str] = {}
        placeholders = self._placeholders(data)

        if user_input is not None:
            if filled and not user_input.get("confirm"):
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
        if filled:
            fields[vol.Required("confirm", default=False)] = BooleanSelector()
        placeholders["warning"] = (
            f"**Preset {self._slot:02d} is filled and storing will overwrite it.** {placeholders['contents']}"
            if filled
            else f"Preset {self._slot:02d} is empty, so nothing will be overwritten."
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
            filled = await client.async_preset_defined(self._slot)
        except KramerError:
            return self.async_abort(reason="unit_unavailable")
        placeholders = self._placeholders(data)
        if not filled:
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
