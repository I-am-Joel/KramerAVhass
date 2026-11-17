"""Front-panel lock switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KramerConfigEntry, KramerEntity, Vp729Data, save_refresh
from .vp729_defs import KIND_SWITCH
from .vp729_entity import build as vp729_build


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the lock switch, only for units that have one."""
    data = entry.runtime_data
    entities: list[SwitchEntity] = [KramerCustomRefreshSwitch(entry)]
    if isinstance(data, Vp729Data):
        entities += vp729_build(entry, KIND_SWITCH)
    elif data.supports_lock and data.client.lock_supported is not False:
        entities.append(KramerLockSwitch(entry))
    async_add_entities(entities)


class KramerCustomRefreshSwitch(KramerEntity, SwitchEntity):
    """On = use the refresh interval set below instead of the default (60 seconds)."""

    _attr_translation_key = "custom_refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "custom_refresh")
        self._entry = entry

    @property
    def available(self) -> bool:
        """Return True: this is a Home Assistant setting, so it works even if the unit is off."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the custom interval is in use."""
        return self._data.custom_refresh

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Use the custom interval."""
        save_refresh(self.hass, self._entry, custom=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Go back to the default interval."""
        save_refresh(self.hass, self._entry, custom=False)


class KramerLockSwitch(KramerEntity, SwitchEntity):
    """On = front panel locked."""

    _attr_translation_key = "panel_lock"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "panel_lock")

    @property
    def available(self) -> bool:
        """Return whether the unit answers and supports the lock."""
        return super().available and self._client.lock_supported is not False

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
