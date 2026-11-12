"""The Kramer matrix switcher integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import (
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_INPUTS,
    CONF_MACHINE,
    CONF_MODEL_NAME,
    CONF_MODES,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
    CONF_PORT,
    DEFAULT_MACHINE,
    DEFAULT_PORT,
)
from .entity import KramerConfigEntry, KramerData
from .protocol import KramerClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SELECT, Platform.SWITCH]


def default_input_names(count: int) -> list[str]:
    """Return 01, 02, 03 ..."""
    return [f"{i:02d}" for i in range(1, count + 1)]


def default_output_names(count: int) -> list[str]:
    """Return Output 01, Output 02 ..."""
    return [f"Output {i:02d}" for i in range(1, count + 1)]


def _names(saved: list[str] | None, defaults: list[str]) -> list[str]:
    """Use saved names where present, defaults for the rest."""
    saved = saved or []
    return [saved[i] if i < len(saved) and saved[i] else d for i, d in enumerate(defaults)]


async def async_setup_entry(hass: HomeAssistant, entry: KramerConfigEntry) -> bool:
    """Set up a unit from a config entry."""
    data = entry.data
    try:
        inputs = int(data[CONF_INPUTS])
        outputs = int(data[CONF_OUTPUTS])
    except (KeyError, TypeError, ValueError) as err:
        raise ConfigEntryError(
            "This entry was created by an older version. Use the three-dot menu "
            "and choose Reconfigure to pick your model."
        ) from err

    client = KramerClient(
        data[CONF_HOST],
        int(data.get(CONF_PORT, DEFAULT_PORT)),
        int(data.get(CONF_MACHINE, DEFAULT_MACHINE)),
        inputs,
        outputs,
        supports_modes=bool(data.get(CONF_MODES, True)),
    )
    entry.runtime_data = KramerData(
        client=client,
        model_name=data.get(CONF_MODEL_NAME, "Matrix switcher"),
        input_names=_names(entry.options.get(CONF_INPUT_NAMES), default_input_names(inputs)),
        output_names=_names(entry.options.get(CONF_OUTPUT_NAMES), default_output_names(outputs)),
        supports_modes=bool(data.get(CONF_MODES, True)),
    )

    await client.async_start()
    # Entities start unavailable and fill in when the unit answers, so a unit
    # that is off at start-up never blocks Home Assistant.
    if not await client.async_wait_ready(10):
        _LOGGER.warning("Kramer unit at %s is not answering yet; will keep trying", data[CONF_HOST])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: KramerConfigEntry) -> None:
    """Reload after options or reconfiguration change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: KramerConfigEntry) -> bool:
    """Unload an entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_stop()
    return unloaded
