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
    name_to_input,
    reject_mixed,
    source_options_with_mixed,
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
        return source_options_with_mixed(self._data)

    @property
    def source(self) -> str:
        """Return the common source, Off, or Mixed."""
        return input_to_name(self._data, self._client.common_input) or SOURCE_MIXED

    async def async_select_source(self, source: str) -> None:
        """Send a source to all outputs."""
        if source == SOURCE_MIXED:
            reject_mixed()
        await self._call(self._client.async_set_route(0, name_to_input(self._data, source)))
