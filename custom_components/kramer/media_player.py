"""Media player: one source selector for the whole matrix."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SOURCE_MIXED
from .entity import (
    KramerConfigEntry,
    KramerEntity,
    input_to_name,
    link_icon,
    name_to_input,
    source_options_for_status,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the media player."""
    async_add_entities([KramerMediaPlayer(entry)])


class KramerMediaPlayer(KramerEntity, MediaPlayerEntity):
    """Selecting a source sends that input to every output."""

    _attr_name = None
    _attr_supported_features = MediaPlayerEntityFeature.SELECT_SOURCE

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "media_player")
        self._attr_unique_id = entry.entry_id

    @property
    def state(self) -> MediaPlayerState:
        """Return on while the unit is answering."""
        return MediaPlayerState.ON

    @property
    def source_list(self) -> list[str]:
        """Return the selectable sources."""
        return source_options_for_status(self._data)

    @property
    def source(self) -> str:
        """Return the common source, Off, or Mixed."""
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

    async def async_select_source(self, source: str) -> None:
        """Send a source to all outputs, for video and (in breakaway mode) audio."""
        if source == SOURCE_MIXED:
            return  # Mixed is only a status: choosing it changes nothing
        await self._call(self._client.async_set_all_layers(0, name_to_input(self._data, source)))
