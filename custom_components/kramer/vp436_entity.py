"""Entities for the Kramer VP-436 / VP-437XL, built from the control table in vp436_defs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import MATCH_ALL, EntityCategory
from homeassistant.core import callback

from .entity import DoublePressButton, KramerConfigEntry, KramerEntity
from .vp436_defs import (
    CONTROLS,
    KEY_BUTTONS,
    Vp436Control,
    label,
)
from .vp729_defs import KIND_NUMBER, KIND_SELECT, KIND_SWITCH

STATUS_SCHEMA_VERSION = 1
FACTORY_RESET_WARNING = (
    "This will restore every setting on the device to its factory default. This cannot be undone. Are you sure?"
)


class Vp436Entity(KramerEntity):
    """Base for the entities that map to one setting of the device."""

    def __init__(self, entry: KramerConfigEntry, control: Vp436Control) -> None:
        """Initialise from a control definition."""
        super().__init__(entry, control.key)
        self._control = control
        self._attr_name = control.name
        if control.icon:
            self._attr_icon = control.icon

    @property
    def available(self) -> bool:
        """Return whether the device has given a value for this setting and takes it in its present state."""
        return self._client.function_available(self._control)


class Vp436Select(Vp436Entity, SelectEntity):
    """A setting with named choices."""

    def __init__(self, entry: KramerConfigEntry, control: Vp436Control) -> None:
        """Initialise."""
        super().__init__(entry, control)
        assert control.options is not None
        self._attr_options = list(control.options.values())
        self._by_name = {name: number for number, name in control.options.items()}

    @property
    def current_option(self) -> str | None:
        """Return the current choice, or None if the device reports something unlisted."""
        name = label(self._control, self._client.value(self._control.key))
        return name if name in self._by_name else None

    async def async_select_option(self, option: str) -> None:
        """Send the choice to the device."""
        await self._call(self._client.async_set(self._control, self._by_name[option]))


class Vp436Number(Vp436Entity, NumberEntity):
    """A setting with a numeric range."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, entry: KramerConfigEntry, control: Vp436Control) -> None:
        """Initialise."""
        super().__init__(entry, control)
        self._attr_native_min_value = control.minimum
        self._attr_native_max_value = control.maximum

    @property
    def native_value(self) -> float | None:
        """Return the value the device reports."""
        return self._client.value(self._control.key)

    async def async_set_native_value(self, value: float) -> None:
        """Send the value to the device (its echo confirms it)."""
        wanted = int(min(max(int(value), self._control.minimum), self._control.maximum))
        await self._call(self._client.async_set(self._control, wanted))


class Vp436Switch(Vp436Entity, SwitchEntity):
    """An on/off setting."""

    @property
    def is_on(self) -> bool | None:
        """Return whether the device reports it on."""
        value = self._client.value(self._control.key)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn it on."""
        await self._call(self._client.async_set(self._control, 1))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn it off."""
        await self._call(self._client.async_set(self._control, 0))


class Vp436KeyButton(KramerEntity, ButtonEntity):
    """A key press, the same as the key on the remote control. The device ignores keys in standby."""

    def __init__(self, entry: KramerConfigEntry, key: str, name: str, number: int, icon: str) -> None:
        """Initialise."""
        super().__init__(entry, key)
        self._attr_name = name
        self._attr_icon = icon
        self._number = number

    @property
    def available(self) -> bool:
        """Return whether the device is awake and answering."""
        return self._client.keys_available()

    async def async_press(self) -> None:
        """Send the key press."""
        await self._call(self._client.async_press(self._number))


class Vp436RebootButton(KramerEntity, ButtonEntity):
    """Restarts the device (it is silent for about 5 seconds)."""

    _attr_name = "Reboot"
    _attr_icon = "mdi:restart"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "reboot")

    @property
    def available(self) -> bool:
        """Return whether the device is awake and answering."""
        return self._client.keys_available()

    async def async_press(self) -> None:
        """Restart the device."""
        await self._call(self._client.async_reboot())


class Vp436FactoryResetButton(DoublePressButton):
    """Restores every setting to its factory default. Disabled until switched on; needs two presses."""

    _attr_name = "Double press to factory reset"
    _attr_icon = "mdi:restore-alert"
    _attr_entity_registry_enabled_default = False
    _warning = FACTORY_RESET_WARNING

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "factory_reset")

    @property
    def available(self) -> bool:
        """Return whether the device is awake and answering."""
        return self._client.keys_available()

    async def _act(self) -> None:
        await self._call(self._client.async_factory_reset())


class Vp436FirmwareSensor(KramerEntity, SensorEntity):
    """The firmware version, read from the device's start-up banner (once per connection)."""

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "firmware")

    @property
    def available(self) -> bool:
        """Available once the device has given its version."""
        return self._client.available and self._client.firmware is not None

    @property
    def native_value(self) -> str | None:
        """Return for example V6.24."""
        return self._client.firmware


class Vp436StatusSensor(KramerEntity, SensorEntity):
    """Online or offline, with a picture of the device's settings for dashboards and automations.

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
        """Stay available while the device is off."""
        return True

    @property
    def native_value(self) -> str:
        """Return online while the device answers, otherwise offline."""
        return "online" if self._client.available else "offline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the settings by name."""
        client = self._client
        settings: dict[str, str | int | None] = {}
        if client.available:
            for control in CONTROLS:
                if client.function_available(control):
                    value = client.value(control.key)
                    settings[control.key] = label(control, value) if control.options else value
            if client.signal_detected is not None:
                settings["signal_detected"] = client.signal_detected
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "name": self._title,
            "model": self._data.model_name,
            "protocol": self._data.protocol,
            "standby": client.standby,
            "device_type": "presentation_scaler",
            "firmware": client.firmware,
            "settings": settings,
            "last_error": {
                "message": client.last_error,
                "time": client.last_error_time.isoformat() if client.last_error_time else None,
            },
        }


_BY_KIND = {
    KIND_SELECT: Vp436Select,
    KIND_NUMBER: Vp436Number,
    KIND_SWITCH: Vp436Switch,
}


def build(entry: KramerConfigEntry, kind: str) -> list[KramerEntity]:
    """Return the entities of one kind. A setting only some firmware has is created only if the device gave it."""
    client = entry.runtime_data.client
    if kind == "button":
        entities: list[KramerEntity] = [Vp436KeyButton(entry, *item) for item in KEY_BUTTONS]
        entities.append(Vp436RebootButton(entry))
        entities.append(Vp436FactoryResetButton(entry))
        return entities
    return [
        _BY_KIND[c.kind](entry, c)
        for c in CONTROLS
        if c.kind == kind and (not c.optional or client.supports(c.key))
    ]
