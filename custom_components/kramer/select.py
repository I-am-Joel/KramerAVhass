"""Selects: one per output, plus All outputs and Mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import (
    KramerConfigEntry,
    KramerEntity,
    input_to_name,
    name_to_input,
    source_options,
)
from .protocol import MODE_AFV, MODE_BREAKAWAY

MODE_LABELS = {
    MODE_AFV: "Audio follow video",
    MODE_BREAKAWAY: "Breakaway",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the selects."""
    data = entry.runtime_data
    entities: list[SelectEntity] = [KramerAllOutputs(entry)]
    entities += [KramerOutputSelect(entry, number, name) for number, name in enumerate(data.output_names, start=1)]
    if data.supports_modes:
        entities.append(KramerModeSelect(entry))
    async_add_entities(entities)


class KramerAllOutputs(KramerEntity, SelectEntity):
    """Send one input to every output."""

    _attr_translation_key = "all_outputs"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "all_outputs")
        self._attr_options = source_options(self._data)

    @property
    def current_option(self) -> str | None:
        """Return the common input, or nothing while outputs differ."""
        return input_to_name(self._data, self._client.common_input)

    async def async_select_option(self, option: str) -> None:
        """Route to all outputs."""
        await self._call(self._client.async_set_route(0, name_to_input(self._data, option)))


class KramerOutputSelect(KramerEntity, SelectEntity):
    """Choose the input for one output."""

    def __init__(self, entry: KramerConfigEntry, output: int, name: str) -> None:
        """Initialise."""
        super().__init__(entry, f"output_{output}")
        self._output = output
        self._attr_name = name
        self._attr_options = source_options(self._data)

    @property
    def current_option(self) -> str | None:
        """Return the input currently routed here."""
        return input_to_name(self._data, self._client.routes.get(self._output))

    async def async_select_option(self, option: str) -> None:
        """Route to this output."""
        await self._call(self._client.async_set_route(self._output, name_to_input(self._data, option)))


class KramerModeSelect(KramerEntity, SelectEntity):
    """Audio-follow-video, video-only or audio-only switching."""

    _attr_translation_key = "mode"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "mode")
        self._attr_options = list(MODE_LABELS.values())

    @property
    def current_option(self) -> str | None:
        """Return the current mode."""
        mode = self._client.mode
        return MODE_LABELS.get(mode) if mode is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Warn that audio and video can be split."""
        return {
            "note": "Breakaway lets audio and video follow different inputs. "
            "Set back to Audio follow video to keep them together."
        }

    async def async_select_option(self, option: str) -> None:
        """Change mode."""
        mode = next(k for k, v in MODE_LABELS.items() if v == option)
        await self._call(self._client.async_set_mode(mode))
