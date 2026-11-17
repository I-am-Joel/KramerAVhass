"""Buttons: the VP-729's remote keys, auto image, capture, volume keys and profile save/recall."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KramerConfigEntry, Vp729Data
from .vp729_defs import KIND_BUTTON
from .vp729_entity import build as vp729_build


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the buttons (presentation scalers only)."""
    if isinstance(entry.runtime_data, Vp729Data):
        async_add_entities(vp729_build(entry, KIND_BUTTON))
