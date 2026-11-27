"""Entities for the Kramer SP-11D digital video processor, built from the control table in sp11d_defs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_FORCE_LOCK
from .entity import KramerConfigEntry, KramerEntity, DoublePressButton
from .sp11d_defs import (
    CONTROLS,
    PRESET_COUNT,
    PRESET_FN,
    STEP_BUTTONS,
    Sp11dControl,
    label,
)
from .vp729_defs import KIND_NUMBER, KIND_SELECT, KIND_SENSOR, KIND_SWITCH

RESET_WARNING = (
    "This action will reset every parameter on the device and clears all stored presets. "
    "This cannot be undone. Are you sure?"
)
STORE_WARNING = (
    "This will overwrite whatever is stored in preset {preset} with the device's current settings. "
    "This cannot be undone. Are you sure?"
)
PANEL_LOCK_FORCED_REASON = 'Unavailable because "Panel lock (forced)" is on. Turn that off first.'


class Sp11dEntity(KramerEntity):
    """Base for the entities that map to one parameter of the unit."""

    def __init__(self, entry: KramerConfigEntry, control: Sp11dControl) -> None:
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
        """Return whether the unit has given a value for this parameter."""
        return self._client.function_available(self._control.fn)


class Sp11dSelect(Sp11dEntity, SelectEntity):
    """A setting with named choices."""

    def __init__(self, entry: KramerConfigEntry, control: Sp11dControl) -> None:
        """Initialise."""
        super().__init__(entry, control)
        assert control.options is not None
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


class Sp11dPresetSelect(KramerEntity, SelectEntity):
    """The preset in use (1 to 16). Choosing one recalls it."""

    _attr_name = "Preset"
    _attr_icon = "mdi:numeric"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "preset")
        self._attr_options = [f"Preset {n}" for n in range(1, PRESET_COUNT + 1)]

    @property
    def available(self) -> bool:
        """Return whether the unit has said which preset is in use."""
        return self._client.function_available(PRESET_FN)

    @property
    def current_option(self) -> str | None:
        """Return the preset in use."""
        preset = self._client.preset
        return None if preset is None else f"Preset {preset}"

    async def async_select_option(self, option: str) -> None:
        """Recall the chosen preset."""
        await self._call(self._client.async_recall(int(option.removeprefix("Preset "))))


class Sp11dNumber(Sp11dEntity, NumberEntity):
    """A setting with a numeric range."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, entry: KramerConfigEntry, control: Sp11dControl) -> None:
        """Initialise."""
        super().__init__(entry, control)
        self._attr_native_min_value = control.minimum
        self._attr_native_max_value = control.maximum

    @property
    def native_value(self) -> float | None:
        """Return the value the unit reports."""
        return self._client.value(self._control.fn)

    async def async_set_native_value(self, value: float) -> None:
        """Send the value to the unit (its echo confirms it)."""
        wanted = int(min(max(int(value), self._control.minimum), self._control.maximum))
        await self._call(self._client.async_set(self._control.fn, wanted))


class Sp11dSwitch(Sp11dEntity, SwitchEntity):
    """An on/off setting."""

    @property
    def is_on(self) -> bool | None:
        """Return whether the unit reports it on."""
        value = self._client.value(self._control.fn)
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn it on."""
        await self._call(self._client.async_set(self._control.fn, 1))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn it off."""
        await self._call(self._client.async_set(self._control.fn, 0))


class Sp11dSensor(Sp11dEntity, SensorEntity):
    """A value the unit reports and cannot be set."""

    @property
    def native_value(self) -> str | None:
        """Return the reading, as the name the unit gives it."""
        return label(self._control, self._client.value(self._control.fn))


class Sp11dIdentitySensor(KramerEntity, SensorEntity):
    """What the unit says about itself when asked (read once per connection)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: KramerConfigEntry, key: str, name: str, attribute: str) -> None:
        """Initialise."""
        super().__init__(entry, key)
        self._attr_name = name
        self._attribute = attribute

    @property
    def available(self) -> bool:
        """Available once the unit has answered the identify request."""
        return self._client.available and getattr(self._client, self._attribute) is not None

    @property
    def native_value(self) -> str | None:
        """Return the text."""
        return getattr(self._client, self._attribute)


class Sp11dStepButton(KramerEntity, ButtonEntity):
    """Moves SCH or Delay by one step, like the + and - buttons on the front panel."""

    def __init__(self, entry: KramerConfigEntry, key: str, name: str, fn: int, change: int) -> None:
        """Initialise."""
        super().__init__(entry, key)
        self._attr_name = name
        self._attr_icon = "mdi:plus-box" if change > 0 else "mdi:minus-box"
        self._fn = fn
        self._change = change
        self._control = next(c for c in CONTROLS if c.fn == fn)

    @property
    def available(self) -> bool:
        """Needs the present value."""
        return self._client.function_available(self._fn)

    async def async_press(self) -> None:
        """Move one step, unless already at the end of the range."""
        current = self._client.value(self._fn)
        if current is None:
            raise HomeAssistantError("The device has not reported the present value yet")
        wanted = current + self._change
        if not self._control.minimum <= wanted <= self._control.maximum:
            raise HomeAssistantError(f"{self._control.name} is already at its limit ({current})")
        await self._call(self._client.async_set(self._fn, wanted))


class Sp11dStoreButton(DoublePressButton):
    """Stores the present settings in one chosen preset, as STORE, the preset number, STORE does on the front panel."""

    _attr_icon = "mdi:content-save"

    def __init__(self, entry: KramerConfigEntry, preset: int) -> None:
        """Initialise."""
        super().__init__(entry, f"store_preset_{preset}")
        self._attr_name = f"Double press to store to preset {preset}"
        self._preset = preset
        self._warning = STORE_WARNING.format(preset=preset)

    async def _act(self) -> None:
        await self._call(self._client.async_store(self._preset))


class Sp11dResetButton(DoublePressButton):
    """Resets every parameter in every preset to 0. Disabled until switched on."""

    _attr_name = "Double press to reset all defaults"
    _attr_icon = "mdi:restore-alert"
    _attr_entity_registry_enabled_default = False
    _warning = RESET_WARNING

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "reset_defaults")

    async def _act(self) -> None:
        await self._call(self._client.async_reset_defaults())


class Sp11dLastStoreSensor(KramerEntity, SensorEntity):
    """The preset the device last acknowledged storing to (since Home Assistant started), with the time."""

    _attr_name = "Double pressed preset (last recorded action)"
    _attr_icon = "mdi:content-save-check"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "last_stored_preset")

    @property
    def available(self) -> bool:
        """Always readable: it only reports what was recorded."""
        return True

    @property
    def native_value(self) -> str | None:
        """Return for example "Preset 16"."""
        stored = self._client.last_store
        return None if stored is None else f"Preset {stored[0]}"

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return when it was stored."""
        stored = self._client.last_store
        return {"time": None if stored is None else stored[1].isoformat()}


class Sp11dForcedLockSwitch(KramerEntity, SwitchEntity):
    """Keeps the front panel locked whenever the integration is connected (saved in the device's settings)."""

    _attr_name = "Panel lock (forced)"
    _attr_icon = "mdi:lock-alert"

    def __init__(self, entry: KramerConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry, "panel_lock_forced")
        self._entry = entry

    @property
    def available(self) -> bool:
        """Always usable: this is a Home Assistant setting, so it works even if the device is off."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the panel is kept locked."""
        return self._client.force_lock

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Say why "Panel lock" cannot be used while this is on."""
        return {"panel_lock": PANEL_LOCK_FORCED_REASON} if self._client.force_lock else {}

    def _save(self, forced: bool) -> None:
        self.hass.config_entries.async_update_entry(self._entry, data={**self._entry.data, CONF_FORCE_LOCK: forced})
        self._client.set_force_lock(forced)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the panel now and keep it locked."""
        self._save(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop keeping the panel locked (it is left as it is)."""
        self._save(False)


_BY_KIND = {
    KIND_SELECT: Sp11dSelect,
    KIND_NUMBER: Sp11dNumber,
    KIND_SWITCH: Sp11dSwitch,
    KIND_SENSOR: Sp11dSensor,
}


def build(entry: KramerConfigEntry, kind: str) -> list[KramerEntity]:
    """Return the entities of one kind ("button" for the buttons)."""
    if kind == "button":
        entities: list[KramerEntity] = [
            Sp11dStepButton(entry, key, name, fn, change) for key, name, fn, change in STEP_BUTTONS
        ]
        entities += [Sp11dStoreButton(entry, n) for n in range(1, PRESET_COUNT + 1)]
        entities.append(Sp11dResetButton(entry))
        return entities
    entities = [_BY_KIND[c.kind](entry, c) for c in CONTROLS if c.kind == kind]
    if kind == KIND_SWITCH:
        entities.append(Sp11dForcedLockSwitch(entry))
    if kind == KIND_SELECT:
        entities.append(Sp11dPresetSelect(entry))
    if kind == KIND_SENSOR:
        entities.append(Sp11dIdentitySensor(entry, "firmware", "Firmware version", "firmware"))
        entities.append(Sp11dIdentitySensor(entry, "model_code", "Model code", "model_code"))
        entities.append(Sp11dLastStoreSensor(entry))
    return entities
