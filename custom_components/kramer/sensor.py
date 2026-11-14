"""Read-only sensors, each created only when the unit supports it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SOURCE_MIXED
from .entity import KramerConfigEntry, KramerEntity, input_to_name


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensors that make sense for this model."""
    data = entry.runtime_data
    client = data.client
    entities: list[KramerEntity] = [KramerLastReplySensor(entry), KramerLastErrorSensor(entry)]
    if client.outputs > 1:
        entities.append(KramerRoutingSensor(entry))
        if data.supports_off:
            entities.append(KramerConnectedOutputsSensor(entry))
            entities.append(KramerDisconnectedOutputsSensor(entry))
    if data.preset_slots > 0:
        entities.append(KramerStoredPresetsSensor(entry))
    async_add_entities(entities)

    # The identity questions are asked once, just after the first connection,
    # so these sensors are added when (and only if) the unit has answered.
    added = False

    @callback
    def _add_identity() -> None:
        nonlocal added
        if added or not client.identity_checked:
            return
        added = True
        remove()
        new: list[KramerEntity] = []
        if client.identity.get("name_raw"):
            new.append(KramerIdentitySensor(entry))
        if client.identity.get("firmware"):
            new.append(KramerFirmwareSensor(entry))
        if new:
            async_add_entities(new)

    remove = client.add_listener(_add_identity)
    entry.async_on_unload(remove)
    _add_identity()


class KramerIdentitySensor(KramerEntity, SensorEntity):
    """The model name as the unit reports it."""

    _attr_translation_key = "identity"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "identity")

    @property
    def native_value(self) -> str | None:
        """Return prefix, number and suffix as the unit sent them."""
        info = self._client.identity
        return f"{info.get('number') or ''}{info.get('suffix') or ''}" or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the pieces and the raw numbers (so a doubtful decode can be checked)."""
        info = self._client.identity
        return {key: info.get(key) for key in ("number", "suffix", "name_raw")}


class KramerFirmwareSensor(KramerEntity, SensorEntity):
    """The software version the unit reports."""

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "firmware")

    @property
    def native_value(self) -> str | None:
        """Return the video software version."""
        value = self._client.identity.get("firmware")
        return str(value) if value else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the audio version and the raw numbers."""
        info = self._client.identity
        return {"audio_firmware": info.get("audio_firmware"), "firmware_raw": info.get("firmware_raw")}


class _DiagnosticSensor(KramerEntity, SensorEntity):
    """A sensor that is still readable while the unit is not answering."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Stay available so the last problem can still be read."""
        return True


class KramerLastReplySensor(_DiagnosticSensor):
    """When the unit last said anything."""

    _attr_translation_key = "last_reply"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "last_reply")

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the unit's last message."""
        return self._client.last_reply


class KramerLastErrorSensor(_DiagnosticSensor):
    """The most recent problem talking to the unit."""

    _attr_translation_key = "last_error"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "last_error")

    @property
    def native_value(self) -> str | None:
        """Return the latest error, if any."""
        return self._client.last_error

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when it happened."""
        when = self._client.last_error_time
        return {"time": when.isoformat() if when else None}


class _RoutingBase(KramerEntity, SensorEntity):
    """Base for sensors summarising the routes."""

    @property
    def available(self) -> bool:
        """Return whether routes are known."""
        return super().available and len(self._client.routes) == self._client.outputs


class KramerRoutingSensor(_RoutingBase):
    """One-line summary of where the outputs are fed from."""

    _attr_translation_key = "routing_summary"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "routing_summary")

    @property
    def native_value(self) -> str | None:
        """Return a short summary."""
        common = self._client.common_input
        if common is None:
            return SOURCE_MIXED
        return f"All from {input_to_name(self._data, common)}" if common else "All off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return every output with its source."""
        return {
            name: input_to_name(self._data, self._client.routes.get(index))
            for index, name in enumerate(self._data.output_names, start=1)
        }


class KramerConnectedOutputsSensor(_RoutingBase):
    """How many outputs are fed from an input."""

    _attr_translation_key = "connected_outputs"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "connected_outputs")

    @property
    def native_value(self) -> int | None:
        """Return the count."""
        return self._client.connected_outputs


class KramerDisconnectedOutputsSensor(_RoutingBase):
    """How many outputs are switched off."""

    _attr_translation_key = "disconnected_outputs"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "disconnected_outputs")

    @property
    def native_value(self) -> int | None:
        """Return the count."""
        connected = self._client.connected_outputs
        return None if connected is None else self._client.outputs - connected


class KramerStoredPresetsSensor(KramerEntity, SensorEntity):
    """How many presets are stored on the unit, and which."""

    _attr_translation_key = "stored_presets"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "stored_presets")

    @property
    def available(self) -> bool:
        """Return whether the slots have been checked."""
        return super().available and bool(self._client.preset_defined)

    @property
    def native_value(self) -> int | None:
        """Return the number of occupied slots."""
        if not self._client.preset_defined:
            return None
        return sum(1 for occupied in self._client.preset_defined.values() if occupied)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the occupied slots with their HASS descriptions (held in Home Assistant, not on the unit)."""
        occupied = {
            f"Preset {slot:02d}": self._data.preset_descriptions.get(slot, "(no HASS description)")
            for slot, filled in sorted(self._client.preset_defined.items())
            if filled
        }
        return {"occupied_presets": occupied, "presets_on_unit": self._client.preset_slots}
