"""Diagnostics download, with addresses and personal names removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_INPUT_NAMES,
    CONF_OUTPUT_NAMES,
    CONF_PRESET_DESCRIPTIONS,
)
from .entity import KramerConfigEntry

TO_REDACT = {CONF_HOST, CONF_INPUT_NAMES, CONF_OUTPUT_NAMES, CONF_PRESET_DESCRIPTIONS, "unique_id", "title", "name"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: KramerConfigEntry) -> dict[str, Any]:
    """Return what is useful for fault-finding, without addresses or names."""
    client = entry.runtime_data.client
    data = entry.runtime_data
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
        },
        "identity": dict(client.identity),
        "state": {
            "available": client.available,
            "mode": client.mode,
            "locked": client.locked,
            "routes": dict(client.routes),
            "occupied_preset_slots": sorted(slot for slot, filled in client.preset_defined.items() if filled),
            "last_reply": client.last_reply.isoformat() if client.last_reply else None,
            "last_error": client.last_error,
            "last_error_time": client.last_error_time.isoformat() if client.last_error_time else None,
        },
    }
