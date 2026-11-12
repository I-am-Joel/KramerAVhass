"""Front-panel lock switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KramerConfigEntry, KramerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the lock switch."""
    async_add_entities([KramerLockSwitch(entry)])


class KramerLockSwitch(KramerEntity, SwitchEntity):
    """On = front panel locked."""

    _attr_translation_key = "panel_lock"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "panel_lock")

    @property
    def is_on(self) -> bool | None:
        """Return whether the panel is locked."""
        return self._client.locked

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the panel."""
        await self._call(self._client.async_set_lock(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the panel."""
        await self._call(self._client.async_set_lock(False))
