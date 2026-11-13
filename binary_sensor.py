"""Connection status."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KramerConfigEntry, KramerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the connection sensor."""
    async_add_entities([KramerConnectionSensor(entry)])


class KramerConnectionSensor(KramerEntity, BinarySensorEntity):
    """On while the unit is answering."""

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "connection")

    @property
    def available(self) -> bool:
        """Always readable, because it reports the connection itself."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the unit is answering."""
        return self._client.available
