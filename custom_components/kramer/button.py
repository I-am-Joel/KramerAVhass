"""Buttons: the VP-729's remote keys, auto image, capture, volume keys and profile save/recall."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KramerConfigEntry, Sp11dData, Vp436Data, Vp729Data
from .sp11d_entity import build as sp11d_build
from .vp436_entity import build as vp436_build
from .vp729_defs import KIND_BUTTON
from .vp729_entity import build as vp729_build


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the buttons (presentation scalers and video processors only)."""
    if isinstance(entry.runtime_data, Vp729Data):
        async_add_entities(vp729_build(entry, KIND_BUTTON))
    elif isinstance(entry.runtime_data, Sp11dData):
        async_add_entities(sp11d_build(entry, "button"))
    elif isinstance(entry.runtime_data, Vp436Data):
        async_add_entities(vp436_build(entry, "button"))
