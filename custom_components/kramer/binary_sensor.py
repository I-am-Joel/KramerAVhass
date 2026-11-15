"""Connection status."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KramerConfigEntry, KramerEntity, audio_input_to_name, base_output_name, input_to_name, link_icon
from .protocol import MODE_AFV


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the connection sensor, and an In step sensor per output on models with separate audio."""
    data = entry.runtime_data
    entities: list[KramerEntity] = [KramerConnectionSensor(entry)]
    if data.has_audio:
        entities += [KramerInStepSensor(entry, number, name) for number, name in enumerate(data.output_names, start=1)]
    async_add_entities(entities)


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


class KramerInStepSensor(KramerEntity, BinarySensorEntity):
    """On while an output's audio comes from the same input as its video (read from the unit)."""

    def __init__(self, entry: KramerConfigEntry, output: int, name: str) -> None:
        """Initialise."""
        super().__init__(entry, f"in_step_{output}")
        self._output = output
        self._attr_name = f"{base_output_name(name)} In step"

    @property
    def available(self) -> bool:
        """Return whether the unit has reported both routes."""
        return super().available and self._client.in_step(self._output) is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether audio and video are in step."""
        return self._client.in_step(self._output)

    @property
    def icon(self) -> str | None:
        """Show a linked or broken chain."""
        return link_icon(self._client.in_step(self._output))

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | None]:
        """Show the two inputs and whether the unit is in Audio follow video."""
        mode = self._client.mode
        return {
            "video_input": input_to_name(self._data, self._client.routes.get(self._output)),
            "audio_input": audio_input_to_name(self._data, self._client.audio_routes.get(self._output)),
            "audio_follow_video": None if mode is None else mode == MODE_AFV,
        }
