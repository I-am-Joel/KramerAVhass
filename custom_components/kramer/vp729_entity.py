"""Entities for the Kramer VP-729, built from the control table in vp729_defs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import MATCH_ALL, EntityCategory
from homeassistant.core import callback

from .entity import KramerConfigEntry, KramerEntity
from .vp729_defs import (
    CONTROLS,
    KIND_BUTTON,
    KIND_NUMBER,
    KIND_SELECT,
    KIND_SENSOR,
    KIND_SWITCH,
    PROFILE_COUNT,
    PROFILE_RECALL_FN,
    PROFILE_SAVE_FN,
    PROFILE_SETTLE_SECONDS,
    VOLUME_BUTTONS,
    Control,
    label,
)

STATUS_SCHEMA_VERSION = 1


class Vp729Entity(KramerEntity):
    """Base for the entities that map to one function of the unit."""

    def __init__(self, entry: KramerConfigEntry, control: Control) -> None:
        """Initialise from a control definition."""
        super().__init__(entry, control.key)
        self._control = control
        self._attr_name = control.name
        if control.icon:
            self._attr_icon = control.icon
        if control.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Return whether the unit has given a value for this function in its current state."""
        return self._client.function_available(self._control.fn)


class Vp729Select(Vp729Entity, SelectEntity):
    """A setting with named choices."""

    def __init__(self, entry: KramerConfigEntry, control: Control) -> None:
        """Initialise."""
        super().__init__(entry, control)
        self._attr_options = list(control.options.values())
        self._by_name = {name: number for number, name in control.options.items()}

    @property
    def current_option(self) -> str | None:
        """Return the current choice, or None if the unit reports something unlisted."""
        name = label(self._control, self._client.value(self._control.fn))
        return name if name in self._by_name else None

    async def async_select_option(self, option: str) -> None:
        """Send the choice to the unit."""
        await self._call(self._client.async_set(self._control.fn, self._by_name[option]))


class Vp729Number(Vp729Entity, NumberEntity):
    """A setting with a numeric range."""

    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, entry: KramerConfigEntry, control: Control) -> None:
        """Initialise."""
        super().__init__(entry, control)
        self._attr_native_min_value = control.minimum
        self._attr_native_max_value = control.maximum

    @property
    def native_value(self) -> float | None:
        """Return the value the unit reports."""
        value = self._client.value(self._control.fn)
        return value if isinstance(value, int) else None

    async def async_set_native_value(self, value: float) -> None:
        """Send the value to the unit (it is read back to confirm)."""
        await self._call(self._client.async_set(self._control.fn, int(value)))


class Vp729Switch(Vp729Entity, SwitchEntity):
    """An on/off setting."""

    @property
    def is_on(self) -> bool | None:
        """Return whether the unit reports it on."""
        value = self._client.value(self._control.fn)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn it on."""
        await self._call(self._client.async_set(self._control.fn, 1, settle=self._control.settle))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn it off."""
        await self._call(self._client.async_set(self._control.fn, 0))


class Vp729Sensor(Vp729Entity, SensorEntity):
    """A value the unit reports and cannot be set."""

    @property
    def native_value(self) -> str | int | None:
        """Return the reading, as text where the unit gives it a name."""
        value = self._client.value(self._control.fn)
        if self._control.options is not None:
            return label(self._control, value)
        return value


class Vp729Button(KramerEntity, ButtonEntity):
    """A key press or other action the unit takes from a command with no state to read."""

    def __init__(
        self,
        entry: KramerConfigEntry,
        key: str,
        name: str,
        fn: int,
        value: int | None,
        icon: str | None,
        settle: float = 0.0,
    ) -> None:
        """Initialise."""
        super().__init__(entry, key)
        self._attr_name = name
        if icon:
            self._attr_icon = icon
        self._fn = fn
        self._value = value
        self._settle = settle

    async def async_press(self) -> None:
        """Send the key press."""
        await self._call(self._client.async_press(self._fn, self._value, settle=self._settle))


class Vp729StatusSensor(KramerEntity, SensorEntity):
    """Online or offline, with a picture of the unit's settings for dashboards and automations.

    Contract (schema_version 1): attributes are only ever added, never renamed,
    removed or changed in type. Attributes are not recorded in history.
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
        """Subscribe to changes, combining bursts into one update."""
        self.async_on_remove(self._client.add_listener(self._schedule_write))
        self.async_on_remove(self._cancel_timer)

    @callback
    def _schedule_write(self) -> None:
        if self._timer is None:
            self._timer = self.hass.loop.call_later(0.2, self._flush)

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
        """Stay available while the unit is off."""
        return True

    @property
    def native_value(self) -> str:
        """Return online while the unit answers, otherwise offline."""
        return "online" if self._client.available else "offline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the settings by name."""
        client = self._client
        online = client.available
        settings: dict[str, str | int | None] = {}
        if online:
            for control in CONTROLS:
                if control.readable and client.function_available(control.fn) and not control.diagnostic:
                    settings[control.key] = label(control, client.value(control.fn)) if control.options else client.value(control.fn)
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "name": self._title,
            "model": self._data.model_name,
            "device_type": "presentation_scaler",
            "firmware": client.firmware,
            "settings": settings,
            "last_error": {
                "message": client.last_error,
                "time": client.last_error_time.isoformat() if client.last_error_time else None,
            },
        }


_BY_KIND = {
    KIND_SELECT: Vp729Select,
    KIND_NUMBER: Vp729Number,
    KIND_SWITCH: Vp729Switch,
    KIND_SENSOR: Vp729Sensor,
}


def build(entry: KramerConfigEntry, kind: str) -> list[KramerEntity]:
    """Return the entities of one kind (a read-only value is a sensor)."""
    if kind == KIND_BUTTON:
        entities: list[KramerEntity] = [
            Vp729Button(entry, c.key, c.name, c.fn, c.press_value, c.icon) for c in CONTROLS if c.kind == KIND_BUTTON
        ]
        entities += [Vp729Button(entry, key, name, 48, value, icon) for key, name, value, icon in VOLUME_BUTTONS]
        for slot in range(PROFILE_COUNT):
            entities.append(
                Vp729Button(entry, f"save_profile_{slot + 1}", f"Save profile {slot + 1}", PROFILE_SAVE_FN, slot, "mdi:content-save")
            )
            entities.append(
                Vp729Button(
                    entry,
                    f"recall_profile_{slot + 1}",
                    f"Recall profile {slot + 1}",
                    PROFILE_RECALL_FN,
                    slot,
                    "mdi:backup-restore",
                    PROFILE_SETTLE_SECONDS,
                )
            )
        return entities
    return [_BY_KIND[c.kind](entry, c) for c in CONTROLS if c.kind == kind]
