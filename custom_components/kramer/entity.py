"""Shared pieces for Kramer entities."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SOURCE_MIXED, SOURCE_OFF
from .protocol import KramerClient, KramerError


@dataclass
class KramerData:
    """Everything the platforms need, stored on the config entry."""

    client: KramerClient
    model_name: str
    input_names: list[str]
    output_names: list[str]
    supports_modes: bool


type KramerConfigEntry = ConfigEntry[KramerData]


def source_options(data: KramerData) -> list[str]:
    """Return the selectable sources: every input name plus Off."""
    return [*data.input_names, SOURCE_OFF]


def source_options_with_mixed(data: KramerData) -> list[str]:
    """Return sources plus Mixed, which is shown as a status and cannot be chosen."""
    return [*source_options(data), SOURCE_MIXED]


def reject_mixed() -> None:
    """Raise the error shown when someone chooses Mixed."""
    raise ServiceValidationError("Mixed is only a status. Choose an input or Off instead.")


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
            model=self._data.model_name,
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
