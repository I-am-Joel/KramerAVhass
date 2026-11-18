"""Shared pieces for Kramer entities."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    CONF_CUSTOM_REFRESH,
    CONF_REFRESH_SECONDS,
    DEFAULT_REFRESH,
    DOMAIN,
    SOURCE_MIXED,
    SOURCE_OFF,
)
from .models import PROTOCOL_2000, PROTOCOL_ASCII, model_label
from .protocol import KramerClient, KramerError
from .vp729_protocol import Vp729Client


@dataclass
class KramerData:
    """Everything the platforms need, stored on the config entry."""

    client: KramerClient
    model_name: str
    input_names: list[str]
    output_names: list[str]
    supports_modes: bool
    supports_off: bool = True
    supports_lock: bool = True
    preset_slots: int = 0
    preset_descriptions: dict[int, str] = field(default_factory=dict)
    audio_input_names: list[str] = field(default_factory=list)
    audio_output_names: list[str] = field(default_factory=list)
    custom_refresh: bool = False
    refresh_seconds: int = 60
    applied_options: dict = field(default_factory=dict)
    protocol: str = PROTOCOL_2000

    @property
    def breakaway(self) -> bool:
        """Return whether this model can route audio and video separately."""
        return self.supports_modes

    @property
    def has_audio(self) -> bool:
        """Return whether separate audio routing is available (the unit has not shown it lacks it)."""
        return self.supports_modes and self.client.audio_supported is not False


@dataclass
class Vp729Data:
    """Everything the platforms need for a VP-729, stored on the config entry.

    The refresh settings and the client's availability, listener and last-error
    members match KramerData, so the shared refresh-interval entities, the
    connection sensor and the diagnostic sensors work for both kinds of unit.
    """

    client: Vp729Client
    model_name: str
    custom_refresh: bool = False
    refresh_seconds: int = 60
    applied_options: dict = field(default_factory=dict)
    protocol: str = PROTOCOL_ASCII


type KramerConfigEntry = ConfigEntry[KramerData | Vp729Data]


def source_options(data: KramerData) -> list[str]:
    """Return the selectable sources: every input name plus Off."""
    if data.supports_off:
        return [*data.input_names, SOURCE_OFF]
    return list(data.input_names)


def audio_source_options(data: KramerData) -> list[str]:
    """Return the selectable audio sources: every audio input name plus Off."""
    if data.supports_off:
        return [*data.audio_input_names, SOURCE_OFF]
    return list(data.audio_input_names)


def audio_input_to_name(data: KramerData, number: int | None) -> str | None:
    """Convert an input number to its audio name (0 = Off)."""
    if number is None:
        return None
    if number == 0:
        return SOURCE_OFF
    if 1 <= number <= len(data.audio_input_names):
        return data.audio_input_names[number - 1]
    return None


def audio_name_to_input(data: KramerData, name: str) -> int:
    """Convert an audio source name to an input number (Off = 0)."""
    if name == SOURCE_OFF:
        return 0
    return data.audio_input_names.index(name) + 1


LINK_ICON = "mdi:link"
UNLINK_ICON = "mdi:link-off"


def link_icon(in_step: bool | None) -> str | None:
    """Return the chain icon for video and audio being in step (or not)."""
    if in_step is None:
        return None
    return LINK_ICON if in_step else UNLINK_ICON


def source_options_with_mixed(data: KramerData) -> list[str]:
    """Return sources plus Mixed, which is shown as a status and cannot be chosen."""
    return [*source_options(data), SOURCE_MIXED]


def reject_mixed() -> None:
    """Raise the error shown when someone chooses Mixed."""
    raise ServiceValidationError("Mixed is only a status. Choose an input or Off instead.")


def base_output_name(name: str) -> str:
    """Return an output's name without a trailing " Video", for things that cover both layers."""
    return name.removesuffix(" Video")


def input_to_name(data: KramerData, number: int | None) -> str | None:
    """Convert an input number to its name (0 = Off)."""
    if number is None:
        return None
    if number == 0:
        return SOURCE_OFF
    if 1 <= number <= len(data.input_names):
        return data.input_names[number - 1]
    return None


def name_to_input(data: KramerData, name: str) -> int:
    """Convert a source name to an input number (Off = 0)."""
    if name == SOURCE_OFF:
        return 0
    return data.input_names.index(name) + 1


class KramerEntity(Entity):
    """Base class: push-updated, one device per unit."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: KramerConfigEntry, key: str) -> None:
        """Initialise the entity."""
        self._data = entry.runtime_data
        self._client = entry.runtime_data.client
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Kramer",
            model=model_label(self._data.model_name, self._data.protocol),
            name=entry.title,
        )

    @property
    def available(self) -> bool:
        """Return whether the unit is answering."""
        return self._client.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes."""
        self.async_on_remove(self._client.add_listener(self.async_write_ha_state))

    async def _call(self, coro: Awaitable[None]) -> None:
        """Run a client command, turning failures into Home Assistant errors."""
        try:
            await coro
        except KramerError as err:
            raise HomeAssistantError(f"Kramer command failed: {err}") from err


def save_refresh(
    hass: HomeAssistant, entry: KramerConfigEntry, *, custom: bool | None = None, seconds: int | None = None
) -> None:
    """Remember the refresh settings in the entry's options and apply them to the running unit."""
    data = entry.runtime_data
    if custom is not None:
        data.custom_refresh = custom
    if seconds is not None:
        data.refresh_seconds = int(seconds)
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_CUSTOM_REFRESH: data.custom_refresh, CONF_REFRESH_SECONDS: data.refresh_seconds},
    )
    data.client.set_refresh_interval(data.refresh_seconds if data.custom_refresh else DEFAULT_REFRESH)
