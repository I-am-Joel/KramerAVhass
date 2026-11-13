"""Actions (services) for the Kramer integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SOURCE_OFF
from .entity import KramerConfigEntry, KramerData
from .presets import resolve_slot
from .protocol import KramerError

SERVICE_SET_ROUTE = "set_route"
SERVICE_STORE_PRESET = "store_preset"
SERVICE_RECALL_PRESET = "recall_preset"

ATTR_INPUT = "input"
ATTR_OUTPUTS = "outputs"
ATTR_SLOT = "slot"
ATTR_AUDIO_ONLY = "audio_only"
ATTR_CONFIRM_OVERWRITE = "confirm_overwrite"

ALL = "all"

_SET_ROUTE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_INPUT): cv.string,
        vol.Required(ATTR_OUTPUTS): vol.All(cv.ensure_list, [cv.string]),
    }
).extend(cv.TARGET_SERVICE_FIELDS)

_STORE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SLOT): cv.string,
        vol.Optional(ATTR_AUDIO_ONLY, default=False): cv.boolean,
        vol.Optional(ATTR_CONFIRM_OVERWRITE, default=False): cv.boolean,
    }
).extend(cv.TARGET_SERVICE_FIELDS)

_RECALL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SLOT): cv.string,
        vol.Optional(ATTR_AUDIO_ONLY, default=False): cv.boolean,
    }
).extend(cv.TARGET_SERVICE_FIELDS)


def _target_entries(hass: HomeAssistant, call: ServiceCall) -> list[KramerConfigEntry]:
    """Return the loaded Kramer entries named by the call's target."""
    device_ids: set[str] = set(cv.ensure_list(call.data.get("device_id") or []))
    ent_reg = er.async_get(hass)
    for entity_id in cv.ensure_list(call.data.get("entity_id") or []):
        entity = ent_reg.async_get(entity_id)
        if entity and entity.device_id:
            device_ids.add(entity.device_id)

    dev_reg = dr.async_get(hass)
    entries: list[KramerConfigEntry] = []
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            continue
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN or entry in entries:
                continue
            if entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(f"{entry.title} is not loaded")
            entries.append(entry)
    if not entries:
        raise ServiceValidationError("Choose a Kramer switcher as the target of this action")
    return entries


def _resolve_input(data: KramerData, token: str) -> int:
    """Turn an input number or name into an input number (Off = 0)."""
    text = token.strip()
    if text.casefold() == SOURCE_OFF.casefold():
        if not data.supports_off:
            raise ServiceValidationError(f"{data.model_name} cannot disconnect an output")
        return 0
    if text.isdigit():
        number = int(text)
        if number == 0 and not data.supports_off:
            raise ServiceValidationError(f"{data.model_name} cannot disconnect an output")
        if 0 <= number <= len(data.input_names):
            return number
        raise ServiceValidationError(f"Input {number} does not exist (1 to {len(data.input_names)})")
    for index, name in enumerate(data.input_names, start=1):
        if name.casefold() == text.casefold():
            return index
    raise ServiceValidationError(f'No input is named "{text}"')


def _resolve_outputs(data: KramerData, tokens: list[str]) -> list[int]:
    """Turn output numbers and names into output numbers (0 = all outputs)."""
    if any(t.strip().casefold() == ALL for t in tokens):
        return [0]
    outputs: list[int] = []
    for token in tokens:
        text = token.strip()
        number: int | None = None
        if text.isdigit():
            number = int(text)
            if not 1 <= number <= len(data.output_names):
                raise ServiceValidationError(f"Output {number} does not exist (1 to {len(data.output_names)})")
        else:
            for index, name in enumerate(data.output_names, start=1):
                if name.casefold() == text.casefold():
                    number = index
                    break
        if number is None:
            raise ServiceValidationError(f'No output is named "{text}"')
        if number not in outputs:
            outputs.append(number)
    if not outputs:
        raise ServiceValidationError('Name at least one output, or use "all"')
    return outputs


def _resolve_preset_slot(data: KramerData, token: str) -> int:
    try:
        return resolve_slot(token, data.preset_descriptions, data.preset_slots)
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err


def _check_audio_only(data: KramerData, audio_only: bool) -> None:
    if audio_only and not data.supports_modes:
        raise ServiceValidationError(f"{data.model_name} has no separate audio setups")


async def _run(coro: Any) -> None:
    try:
        await coro
    except KramerError as err:
        raise HomeAssistantError(f"Kramer command failed: {err}") from err


async def _handle_set_route(hass: HomeAssistant, call: ServiceCall) -> None:
    """Route one input to one or more outputs."""
    for entry in _target_entries(hass, call):
        data = entry.runtime_data
        input_number = _resolve_input(data, call.data[ATTR_INPUT])
        outputs = _resolve_outputs(data, call.data[ATTR_OUTPUTS])
        done: list[int] = []
        for output in outputs:
            try:
                await data.client.async_set_route(output, input_number)
            except KramerError as err:
                if done:
                    raise HomeAssistantError(
                        f"Kramer command failed on output {output}: {err}. Already switched: {done}"
                    ) from err
                raise HomeAssistantError(f"Kramer command failed: {err}") from err
            done.append(output)


async def _handle_store_preset(hass: HomeAssistant, call: ServiceCall) -> None:
    """Store the present routing in a slot (refuses an occupied slot unless confirmed)."""
    for entry in _target_entries(hass, call):
        data = entry.runtime_data
        slot = _resolve_preset_slot(data, call.data[ATTR_SLOT])
        audio_only = call.data[ATTR_AUDIO_ONLY]
        _check_audio_only(data, audio_only)
        try:
            occupied = await data.client.async_preset_defined(slot)
        except KramerError as err:
            raise HomeAssistantError(f"Kramer command failed: {err}") from err
        if occupied and not call.data[ATTR_CONFIRM_OVERWRITE]:
            raise ServiceValidationError(
                f"Slot {slot} already holds a stored setup and storing would overwrite it. "
                f"Set {ATTR_CONFIRM_OVERWRITE} to true if that is what you want."
            )
        await _run(data.client.async_store_preset(slot, audio_only))


async def _handle_recall_preset(hass: HomeAssistant, call: ServiceCall) -> None:
    """Recall a stored setup."""
    for entry in _target_entries(hass, call):
        data = entry.runtime_data
        slot = _resolve_preset_slot(data, call.data[ATTR_SLOT])
        audio_only = call.data[ATTR_AUDIO_ONLY]
        _check_audio_only(data, audio_only)
        await _run(data.client.async_recall_preset(slot, audio_only))


def async_register_services(hass: HomeAssistant) -> None:
    """Register the actions."""

    async def set_route(call: ServiceCall) -> None:
        await _handle_set_route(hass, call)

    async def store_preset(call: ServiceCall) -> None:
        await _handle_store_preset(hass, call)

    async def recall_preset(call: ServiceCall) -> None:
        await _handle_recall_preset(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_SET_ROUTE, set_route, schema=_SET_ROUTE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STORE_PRESET, store_preset, schema=_STORE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_RECALL_PRESET, recall_preset, schema=_RECALL_SCHEMA)
