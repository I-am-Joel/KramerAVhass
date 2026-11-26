"""Diagnostics download, with addresses and personal names removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AUDIO_INPUT_NAMES,
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_OUTPUT_NAMES,
    CONF_PRESET_DESCRIPTIONS,
)
from .entity import KramerConfigEntry, Sp11dData, Vp729Data

TO_REDACT = {
    CONF_HOST,
    CONF_AUDIO_INPUT_NAMES,
    CONF_INPUT_NAMES,
    CONF_OUTPUT_NAMES,
    CONF_PRESET_DESCRIPTIONS,
    "unique_id",
    "title",
    "name",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: KramerConfigEntry) -> dict[str, Any]:
    """Return what is useful for fault-finding, without addresses or names."""
    client = entry.runtime_data.client
    data = entry.runtime_data
    if isinstance(data, Sp11dData):
        return {
            "entry": async_redact_data(
                {
                    "title": entry.title,
                    "unique_id": entry.unique_id,
                    "data": dict(entry.data),
                    "options": dict(entry.options),
                },
                TO_REDACT,
            ),
            "model": data.model_name,
            "protocol": data.protocol,
            "firmware": client.firmware,
            "model_code": client.model_code,
            "state": {
                "available": client.available,
                "values": {str(fn): value for fn, value in sorted(client.values.items())},
                "last_reply": client.last_reply.isoformat() if client.last_reply else None,
                "last_error": client.last_error,
                "last_error_time": client.last_error_time.isoformat() if client.last_error_time else None,
            },
        }
    if isinstance(data, Vp729Data):
        return {
            "entry": async_redact_data(
                {
                    "title": entry.title,
                    "unique_id": entry.unique_id,
                    "data": dict(entry.data),
                    "options": dict(entry.options),
                },
                TO_REDACT,
            ),
            "model": data.model_name,
            "protocol": data.protocol,
            "firmware": client.firmware,
            "state": {
                "available": client.available,
                "values": {str(fn): value for fn, value in sorted(client.values.items())},
                "unavailable_functions": {str(fn): code for fn, code in sorted(client.errors.items())},
                "last_reply": client.last_reply.isoformat() if client.last_reply else None,
                "last_error": client.last_error,
                "last_error_time": client.last_error_time.isoformat() if client.last_error_time else None,
            },
        }
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "model": data.model_name,
        "capabilities": {
            "supports_modes": data.supports_modes,
            "supports_off": data.supports_off,
            "supports_lock": data.supports_lock,
            "lock_supported_by_unit": client.lock_supported,
            "preset_slots": data.preset_slots,
            "audio_levels": list(data.audio_levels),
        },
        "protocol": data.protocol,
        "identity": dict(client.identity),
        "state": {
            "available": client.available,
            "mode": client.mode,
            "locked": client.locked,
            "routes": dict(client.routes),
            "audio_routes": dict(client.audio_routes),
            "levels": dict(client.levels),
            "filled_preset_slots": sorted(slot for slot, filled in client.preset_defined.items() if filled),
            "last_reply": client.last_reply.isoformat() if client.last_reply else None,
            "last_error": client.last_error,
            "last_error_time": client.last_error_time.isoformat() if client.last_error_time else None,
        },
    }
