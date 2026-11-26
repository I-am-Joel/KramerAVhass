"""Read-only sensors, each created only when the unit supports it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import MATCH_ALL, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SOURCE_MIXED
from .entity import (
    KramerConfigEntry,
    KramerEntity,
    Sp11dData,
    Vp729Data,
    audio_input_to_name,
    base_output_name,
    input_to_name,
)
from .protocol import MODE_AFV, MODE_BREAKAWAY
from .sp11d_entity import build as sp11d_build
from .vp729_defs import KIND_SENSOR
from .vp729_entity import Vp729StatusSensor
from .vp729_entity import build as vp729_build


async def async_setup_entry(
    hass: HomeAssistant, entry: KramerConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensors that make sense for this model."""
    data = entry.runtime_data
    if isinstance(data, Vp729Data):
        async_add_entities(
            [
                Vp729StatusSensor(entry),
                KramerLastReplySensor(entry),
                KramerLastErrorSensor(entry),
                *vp729_build(entry, KIND_SENSOR),
            ]
        )
        return
    if isinstance(data, Sp11dData):
        async_add_entities(
            [KramerLastReplySensor(entry), KramerLastErrorSensor(entry), *sp11d_build(entry, KIND_SENSOR)]
        )
        return
    client = data.client
    entities: list[KramerEntity] = [
        KramerStatusSensor(entry),
        KramerLastReplySensor(entry),
        KramerLastErrorSensor(entry),
    ]
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
    def native_value(self) -> str:
        """Return the latest error, or None if there has not been one."""
        return self._client.last_error or "None"

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
        common = self._client.common_all
        if common is None:
            return SOURCE_MIXED
        return f"All from {input_to_name(self._data, common)}" if common else "All off"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return every output with its source."""
        client = self._client
        if not self._data.has_audio or not client.audio_routes:
            return {
                base_output_name(name): input_to_name(self._data, client.routes.get(index))
                for index, name in enumerate(self._data.output_names, start=1)
            }
        return {
            base_output_name(name): (
                f"Video: {input_to_name(self._data, client.routes.get(index))}; "
                f"Audio: {audio_input_to_name(self._data, client.audio_routes.get(index))}"
            )
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
        """Return the number of filled slots."""
        if not self._client.preset_defined:
            return None
        return sum(1 for filled in self._client.preset_defined.values() if filled)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the filled slots with their HASS descriptions (held in Home Assistant, not on the unit)."""
        filled_presets = {
            f"Preset {slot:02d}": self._data.preset_descriptions.get(slot, "(no HASS description)")
            for slot, filled in sorted(self._client.preset_defined.items())
            if filled
        }
        return {"filled_presets": filled_presets, "presets_on_unit": self._client.preset_slots}


STATUS_SCHEMA_VERSION = 1


class KramerStatusSensor(KramerEntity, SensorEntity):
    """Everything about the unit in one place, for dashboard cards and automations.

    Contract (schema_version 1): attributes are only ever added, never renamed,
    removed or changed in type. A breaking change raises schema_version.
    Attributes are not recorded in history, so route changes do not grow the database.

    Unlike the other entities this one stays available while the unit is off. Its
    state is then "offline" and it keeps describing the unit's layout (model, number of
    inputs and outputs, features and names, all of which come from this integration's
    own setup), so a dashboard card can still draw the unit. What only the unit knows
    (routes, mode, lock and which presets are filled) is cleared until it answers again.
    """

    _attr_translation_key = "status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "status")
        self._title = entry.title
        self._timer: Any = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to changes, combining bursts (such as a preset recall) into one update."""
        self.async_on_remove(self._client.add_listener(self._schedule_write))
        self.async_on_remove(self._cancel_timer)

    @callback
    def _schedule_write(self) -> None:
        if self._timer is None:
            self._timer = self.hass.loop.call_later(0.1, self._flush)

    @callback
    def _flush(self) -> None:
        self._timer = None
        self.async_write_ha_state()

    @callback
    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @property
    def available(self) -> bool:
        """Stay available while the unit is off, so the layout can still be read."""
        return True

    @property
    def native_value(self) -> str:
        """Return online while the unit answers, otherwise offline."""
        return "online" if self._client.available else "offline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the unit's whole picture."""
        data, client = self._data, self._client
        online = client.available
        outputs = range(1, client.outputs + 1)
        mode = (
            {MODE_AFV: "afv", MODE_BREAKAWAY: "breakaway"}.get(client.mode)
            if data.supports_modes and online
            else None
        )
        has_audio = data.has_audio
        # Only the unit knows these, so they are cleared while it is off.
        filled = sorted(slot for slot, is_filled in client.preset_defined.items() if is_filled) if online else []
        video_routes = [client.routes.get(o) if online else None for o in outputs]
        audio_routes = [client.audio_routes.get(o) if online else None for o in outputs] if has_audio else []
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "name": self._title,
            "model": data.model_name,
            "protocol": data.protocol,
            "inputs": client.inputs,
            "outputs": client.outputs,
            "features": {
                "modes": data.supports_modes,
                "audio": has_audio,
                "off": data.supports_off,
                "lock": data.supports_lock and client.lock_supported is not False,
                "presets": data.preset_slots,
            },
            "mode": mode,
            "input_names": list(data.input_names),
            "audio_input_names": list(data.audio_input_names) if has_audio else [],
            "output_names": list(data.output_names),
            "audio_output_names": list(data.audio_output_names) if has_audio else [],
            "video_routes": video_routes,
            "audio_routes": audio_routes,
            "presets": {
                "slots": data.preset_slots,
                "filled": filled,
                "descriptions": {str(slot): text for slot, text in sorted(data.preset_descriptions.items()) if text},
            },
            "firmware": client.identity.get("firmware"),
            "last_error": {
                "message": client.last_error,
                "time": client.last_error_time.isoformat() if client.last_error_time else None,
            },
            "locked": client.locked if online else None,
            "audio_levels": {
                "available": list(data.audio_levels),
                "outputs": {str(output): level for output, level in sorted(client.levels.items())} if online else {},
            },
        }
