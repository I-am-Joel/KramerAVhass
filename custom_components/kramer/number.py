"""Refresh interval setting."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import MAX_REFRESH, MIN_REFRESH
from .entity import KramerConfigEntry, KramerEntity, save_refresh


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the refresh interval setting."""
    async_add_entities([KramerRefreshInterval(entry)])


class KramerRefreshInterval(KramerEntity, NumberEntity):
    """How often the whole unit is re-read. Only usable while Custom refresh is on."""

    _attr_translation_key = "refresh_interval"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_REFRESH
    _attr_native_max_value = MAX_REFRESH
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "refresh_interval")
        self._entry = entry

    @property
    def available(self) -> bool:
        """Greyed out (unavailable) while Custom refresh is off, so the default cannot be changed by accident."""
        return self._data.custom_refresh

    @property
    def native_value(self) -> float:
        """Return the saved interval."""
        return self._data.refresh_seconds

    async def async_set_native_value(self, value: float) -> None:
        """Save and apply a new interval."""
        save_refresh(self.hass, self._entry, seconds=int(value))
