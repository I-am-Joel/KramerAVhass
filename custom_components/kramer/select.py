"""Selects: one per output, plus All outputs and Mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SOURCE_MIXED
from .entity import (
    KramerConfigEntry,
    KramerEntity,
    audio_input_to_name,
    audio_name_to_input,
    audio_source_options,
    input_to_name,
    link_icon,
    name_to_input,
    reject_mixed,
    source_options,
    source_options_with_mixed,
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
    entities: list[SelectEntity] = []
    if len(data.output_names) > 1:
        entities.append(KramerAllOutputs(entry))
    for number, name in enumerate(data.output_names, start=1):
        entities.append(KramerOutputSelect(entry, number, name))
        if data.has_audio:
            entities.append(KramerAudioOutputSelect(entry, number, name))
    if data.supports_modes:
        entities.append(KramerModeSelect(entry))
    async_add_entities(entities)


class KramerAllOutputs(KramerEntity, SelectEntity):
    """Send one input to every output (video and, in breakaway mode, audio too)."""

    _attr_translation_key = "all_outputs"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "all_outputs")
        self._attr_options = source_options_with_mixed(self._data)

    @property
    def current_option(self) -> str:
        """Return the common input, or Mixed while outputs differ."""
        return input_to_name(self._data, self._client.common_all) or SOURCE_MIXED

    @property
    def icon(self) -> str | None:
        """Show a linked chain while every output has audio and video in step."""
        if not self._data.has_audio:
            return None
        steps = [self._client.in_step(output) for output in range(1, self._client.outputs + 1)]
        if None in steps:
            return None
        return link_icon(all(steps))

    async def async_select_option(self, option: str) -> None:
        """Route to all outputs."""
        if option == SOURCE_MIXED:
            reject_mixed()
        await self._call(self._client.async_set_all_layers(0, name_to_input(self._data, option)))


class _OutputLayerSelect(KramerEntity, SelectEntity):
    """Shared parts of the Video and Audio selects for one output."""

    def __init__(self, entry: KramerConfigEntry, key: str, output: int, name: str) -> None:
        """Initialise."""
        super().__init__(entry, key)
        self._output = output
        self._attr_name = name

    @property
    def icon(self) -> str | None:
        """Show a linked or broken chain for audio and video being in step."""
        if not self._data.has_audio:
            return None
        return link_icon(self._client.in_step(self._output))

    @property
    def extra_state_attributes(self) -> dict[str, bool | None] | None:
        """Say whether the unit is in Audio follow video, and whether this output is in step."""
        if not self._data.has_audio:
            return None
        mode = self._client.mode
        return {
            "audio_follow_video": None if mode is None else mode == MODE_AFV,
            "in_step": self._client.in_step(self._output),
        }


class KramerOutputSelect(_OutputLayerSelect):
    """Choose the video input for one output."""

    def __init__(self, entry: KramerConfigEntry, output: int, name: str) -> None:
        """Initialise."""
        data = entry.runtime_data
        super().__init__(entry, f"output_{output}", output, f"{name} Video" if data.has_audio else name)
        self._attr_options = source_options(self._data)

    @property
    def current_option(self) -> str | None:
        """Return the input currently routed here."""
        return input_to_name(self._data, self._client.routes.get(self._output))

    async def async_select_option(self, option: str) -> None:
        """Route video to this output (in Audio follow video the unit moves audio too)."""
        await self._call(self._client.async_set_route(self._output, name_to_input(self._data, option)))


class KramerAudioOutputSelect(_OutputLayerSelect):
    """Choose the audio input for one output (breakaway mode only)."""

    def __init__(self, entry: KramerConfigEntry, output: int, name: str) -> None:
        """Initialise."""
        super().__init__(entry, f"audio_output_{output}", output, f"{name} Audio")
        self._attr_options = audio_source_options(self._data)

    @property
    def available(self) -> bool:
        """Return whether the unit answers and has reported its audio routes."""
        return super().available and len(self._client.audio_routes) == self._client.outputs

    @property
    def current_option(self) -> str | None:
        """Return the audio input currently routed here."""
        return audio_input_to_name(self._data, self._client.audio_routes.get(self._output))

    async def async_select_option(self, option: str) -> None:
        """Route audio to this output, only in breakaway mode."""
        if self._client.mode == MODE_AFV:
            raise ServiceValidationError(
                "Switch the mode to Breakaway first. In Audio follow video the unit moves audio "
                "together with video, so audio cannot be changed on its own."
            )
        await self._call(self._client.async_set_audio_route(self._output, audio_name_to_input(self._data, option)))


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
            "note": "Breakaway lets audio and video follow different inputs. Choosing Audio follow video "
            "makes every audio output follow its video input at once, and separate audio routes are lost."
        }

    async def async_select_option(self, option: str) -> None:
        """Change mode."""
        mode = next(k for k, v in MODE_LABELS.items() if v == option)
        await self._call(self._client.async_set_mode(mode))
