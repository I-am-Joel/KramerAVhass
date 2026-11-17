"""The Kramer matrix switcher integration."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence
from typing import Any

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUDIO_INPUT_NAMES,
    CONF_AUDIO_OUTPUT_NAMES,
    CONF_CUSTOM_REFRESH,
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_INPUTS,
    CONF_MACHINE,
    CONF_MODES,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
    CONF_PORT,
    CONF_PRESET_DESCRIPTIONS,
    CONF_REFRESH_SECONDS,
    DEFAULT_MACHINE,
    DEFAULT_PORT,
    DEFAULT_REFRESH,
    DEFAULT_VP729_PORT,
    DOMAIN,
    MAX_REFRESH,
    MIN_REFRESH,
)
from .entity import KramerConfigEntry, KramerData, Vp729Data
from .models import (
    is_vp729,
    resolve_model_name,
    resolve_preset_slots,
    resolve_supports_lock,
    resolve_supports_off,
)
from .presets import parse_descriptions
from .protocol import KramerClient
from .services import async_register_services
from .vp729_protocol import Vp729Client

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA: vol.Schema = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

VP729_PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

REFRESH_KEYS = (CONF_CUSTOM_REFRESH, CONF_REFRESH_SECONDS)


def refresh_settings(options: Mapping[str, Any]) -> tuple[bool, int]:
    """Return (custom refresh on, seconds) from saved options, kept within the allowed range."""
    try:
        seconds = int(options.get(CONF_REFRESH_SECONDS, DEFAULT_REFRESH))
    except (TypeError, ValueError):
        seconds = DEFAULT_REFRESH
    return bool(options.get(CONF_CUSTOM_REFRESH, False)), max(MIN_REFRESH, min(MAX_REFRESH, seconds))


def effective_refresh(custom: bool, seconds: int) -> int:
    """Return the interval in use: the default unless custom refresh is on."""
    return seconds if custom else DEFAULT_REFRESH


def default_input_names(count: int, suffix: str = "") -> list[str]:
    """Return Input 001, Input 002 ... (with a suffix such as " Video" if given)."""
    return [f"Input {i:03d}{suffix}" for i in range(1, count + 1)]


def default_output_names(count: int, suffix: str = "") -> list[str]:
    """Return Output 001, Output 002 ... with an optional suffix such as " Video"."""
    return [f"Output {i:03d}{suffix}" for i in range(1, count + 1)]


def effective_names(
    saved: list[str] | None,
    defaults: list[str],
    legacy: Sequence[Collection[str]] | None = None,
) -> list[str]:
    """Use saved names where customised; defaults for blanks and old-style defaults."""
    saved = saved or []
    names = []
    for i, default in enumerate(defaults):
        name = saved[i] if i < len(saved) else ""
        if not name or (legacy and name in legacy[i]):
            name = default
        names.append(name)
    return names


def legacy_input_names(count: int) -> list[set[str]]:
    """Return the earlier default names for each input: 01, then Input 01."""
    return [{f"{i:02d}", f"Input {i:02d}", f"Input {i:03d}"} for i in range(1, count + 1)]


def legacy_output_names(count: int) -> list[set[str]]:
    """Return the earlier default names for each output: Output 01, then Output 001."""
    return [{f"Output {i:02d}", f"Output {i:03d}"} for i in range(1, count + 1)]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions (once, for all units)."""
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KramerConfigEntry) -> bool:
    """Set up a unit from a config entry."""
    data = entry.data
    if is_vp729(data):
        return await _setup_vp729(hass, entry)
    try:
        inputs = int(data[CONF_INPUTS])
        outputs = int(data[CONF_OUTPUTS])
    except (KeyError, TypeError, ValueError) as err:
        raise ConfigEntryError(
            "This entry was created by an older version. Use the three-dot menu "
            "and choose Reconfigure to pick your model."
        ) from err

    custom_refresh, refresh_seconds = refresh_settings(entry.options)
    preset_slots = resolve_preset_slots(data)
    breakaway = bool(data.get(CONF_MODES, True))
    client = KramerClient(
        data[CONF_HOST],
        int(data.get(CONF_PORT, DEFAULT_PORT)),
        int(data.get(CONF_MACHINE, DEFAULT_MACHINE)),
        inputs,
        outputs,
        supports_modes=bool(data.get(CONF_MODES, True)),
        preset_slots=preset_slots,
        refresh_interval=effective_refresh(custom_refresh, refresh_seconds),
    )
    output_names = effective_names(
        entry.options.get(CONF_OUTPUT_NAMES),
        default_output_names(outputs, " Video" if breakaway else ""),
        legacy_output_names(outputs),
    )
    entry.runtime_data = KramerData(
        client=client,
        model_name=resolve_model_name(data),
        input_names=effective_names(
            entry.options.get(CONF_INPUT_NAMES),
            default_input_names(inputs, " Video" if breakaway else ""),
            legacy_input_names(inputs),
        ),
        audio_input_names=(
            effective_names(entry.options.get(CONF_AUDIO_INPUT_NAMES), default_input_names(inputs, " Audio"))
            if breakaway
            else []
        ),
        output_names=output_names,
        audio_output_names=(
            effective_names(
                entry.options.get(CONF_AUDIO_OUTPUT_NAMES),
                default_output_names(outputs, " Audio"),
                legacy_output_names(outputs),
            )
            if breakaway
            else []
        ),
        supports_modes=bool(data.get(CONF_MODES, True)),
        supports_off=resolve_supports_off(data),
        supports_lock=resolve_supports_lock(data),
        preset_slots=preset_slots,
        preset_descriptions=parse_descriptions(entry.options.get(CONF_PRESET_DESCRIPTIONS), preset_slots),
        custom_refresh=custom_refresh,
        refresh_seconds=refresh_seconds,
        applied_options=_without_refresh(entry.options),
    )

    await client.async_start()
    # Entities start unavailable and fill in when the unit answers, so a unit
    # that is off at start-up never blocks Home Assistant.
    if not await client.async_wait_ready(10):
        _LOGGER.warning("Kramer unit at %s is not answering yet; will keep trying", data[CONF_HOST])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _setup_vp729(hass: HomeAssistant, entry: KramerConfigEntry) -> bool:
    """Set up a VP-729 presentation scaler/switcher."""
    data = entry.data
    custom_refresh, refresh_seconds = refresh_settings(entry.options)
    client = Vp729Client(
        data[CONF_HOST],
        int(data.get(CONF_PORT, DEFAULT_VP729_PORT)),
        refresh_interval=effective_refresh(custom_refresh, refresh_seconds),
    )
    entry.runtime_data = Vp729Data(
        client=client,
        model_name=resolve_model_name(data),
        custom_refresh=custom_refresh,
        refresh_seconds=refresh_seconds,
        applied_options=_without_refresh(entry.options),
    )
    await client.async_start()
    # The first read of a unit takes a few seconds, so wait a little longer than for a matrix.
    if not await client.async_wait_ready(20):
        _LOGGER.warning("VP-729 at %s is not answering yet; will keep trying", data[CONF_HOST])

    await hass.config_entries.async_forward_entry_setups(entry, VP729_PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


def _without_refresh(options: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in options.items() if k not in REFRESH_KEYS}


async def _reload(hass: HomeAssistant, entry: KramerConfigEntry) -> None:
    """Reload after options or reconfiguration change (not for refresh-interval changes alone)."""
    if _without_refresh(entry.options) == entry.runtime_data.applied_options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: KramerConfigEntry) -> bool:
    """Unload an entry."""
    platforms = VP729_PLATFORMS if is_vp729(entry.data) else PLATFORMS
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        await entry.runtime_data.client.async_stop()
    return unloaded
