"""The controls of a Kramer VP-436 / VP-437XL presentation switcher/scaler, as found by probing a real VP-436.

Every command is "Y <type> <function> [value]". Types: 0 key press, 1 set / 2 get a level or option,
3 set / 4 get the input, picture size or output resolution, 6 set / 7 get power or an on/off switch.
Nothing here imports Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass

from .vp729_defs import KIND_NUMBER, KIND_SELECT, KIND_SWITCH

KEY_TYPE = 0
SET_LEVEL, GET_LEVEL = 1, 2  # levels and options
SET_MODE, GET_MODE = 3, 4  # input, picture size, output resolution
SET_SWITCH, GET_SWITCH = 6, 7  # power and the on/off switches
SET_TYPES = (SET_LEVEL, SET_MODE, SET_SWITCH)
GET_FOR_SET = {SET_LEVEL: GET_LEVEL, SET_MODE: GET_MODE, SET_SWITCH: GET_SWITCH}

POWER_KEY = "power"
INPUT_KEY = "input"
RESOLUTION_KEY = "resolution"
SIGNAL_FN = 53  # read only (type 2): 0 while a signal is present on the input in use
FACTORY_RESET = (SET_MODE, 23, 1)  # about 8 seconds, then every setting is back to its default
REBOOT = (SET_SWITCH, 0, 2)  # about 5 seconds

RESOLUTION_SETTLE_SECONDS = 2.6  # the output takes about 2.3 seconds to settle after a resolution change
POWER_ON_SETTLE_SECONDS = 3.0
REBOOT_SETTLE_SECONDS = 6.0
FACTORY_RESET_SETTLE_SECONDS = 9.0

INPUTS = {
    1: "Composite (AV)",
    2: "S-video (YC)",
    3: "Component 1",
    4: "Component 2",
    5: "VGA",
    6: "HDMI 1",
    7: "HDMI 2",
}
SIZES = {
    0: "Full",
    1: "Panscan",
    2: "Overscan",
    3: "Underscan 1 (6%)",
    4: "Letterbox",
    5: "Underscan 2 (9%)",
}
RESOLUTIONS = {
    0: "Native",
    1: "VGA 640x480",
    2: "SVGA 800x600",
    3: "XGA 1024x768",
    4: "SXGA 1280x1024",
    5: "UXGA 1600x1200",
    6: "480i",
    7: "480p",
    8: "720p at 60 Hz",
    9: "1080i at 60 Hz",
    10: "1080p at 60 Hz",
    11: "576i",
    12: "576p",
    13: "720p at 50 Hz",
    14: "1080i at 50 Hz",
    15: "1080p at 50 Hz",
    16: "WXGA 1366x768",
    17: "WSXGA 1680x1050",
    18: "WUXGA 1920x1200",
    19: "1280x800",
    20: "1440x900 (WXGA+)",
    21: "SXGA+ 1400x1050",
}
NOISE_REDUCTION = {0: "Off", 1: "Low", 2: "Mid", 3: "High"}
AUDIO_DELAY = {0: "Off", 1: "40 ms", 2: "110 ms", 3: "150 ms"}
HDMI_AUDIO = {0: "Automatic", 1: "Embedded", 2: "Analog"}
ON_OFF = {0: "Off", 1: "On"}


@dataclass(frozen=True)
class Vp436Control:
    """One setting of the device."""

    key: str
    name: str
    kind: str
    set_type: int
    fn: int
    options: dict[int, str] | None = None
    minimum: int = 0
    maximum: int = 0
    icon: str | None = None
    standby_blocked: bool = False  # the device ignores it (or clears it) while in standby
    optional: bool = False  # only some firmware has it; shown only if the device answers
    per_input: bool = False  # kept separately for each kind of input, so read again after an input change

    @property
    def get_type(self) -> int:
        """Return the command type that reads this setting."""
        return GET_FOR_SET[self.set_type]


def _select(key: str, name: str, set_type: int, fn: int, options: dict[int, str], icon: str, **kw: bool) -> Vp436Control:
    return Vp436Control(key, name, KIND_SELECT, set_type, fn, options=options, icon=icon, **kw)


def _number(key: str, name: str, fn: int, low: int, high: int, icon: str, **kw: bool) -> Vp436Control:
    return Vp436Control(key, name, KIND_NUMBER, SET_LEVEL, fn, minimum=low, maximum=high, icon=icon, **kw)


def _switch(key: str, name: str, set_type: int, fn: int, icon: str, **kw: bool) -> Vp436Control:
    return Vp436Control(key, name, KIND_SWITCH, set_type, fn, options=ON_OFF, icon=icon, **kw)


CONTROLS: tuple[Vp436Control, ...] = (
    _select(INPUT_KEY, "Input", SET_MODE, 0, INPUTS, "mdi:video-input-hdmi", standby_blocked=True),
    _select(RESOLUTION_KEY, "Output resolution", SET_MODE, 21, RESOLUTIONS, "mdi:monitor-screenshot"),
    _select("size", "Picture size", SET_MODE, 1, SIZES, "mdi:aspect-ratio"),
    _select("noise_reduction", "Noise reduction", SET_LEVEL, 50, NOISE_REDUCTION, "mdi:blur"),
    _select("audio_delay", "Audio delay", SET_LEVEL, 51, AUDIO_DELAY, "mdi:timer-outline"),
    _select("hdmi_audio", "HDMI audio source", SET_LEVEL, 52, HDMI_AUDIO, "mdi:volume-source"),
    _switch(POWER_KEY, "Power (standby)", SET_SWITCH, 0, "mdi:power"),
    _switch("freeze", "Freeze", SET_SWITCH, 1, "mdi:snowflake", standby_blocked=True),
    _switch("blank", "Blank", SET_SWITCH, 2, "mdi:monitor-off"),
    _switch("mute", "Mute", SET_SWITCH, 3, "mdi:volume-off"),
    _switch("panel_lock", "Panel lock", SET_SWITCH, 4, "mdi:lock", standby_blocked=True),
    _switch("auto_sync_off", "Auto sync off", SET_LEVEL, 84, "mdi:sync-off"),
    _switch("input_hdcp", "Input HDCP", SET_LEVEL, 160, "mdi:shield-key", optional=True),
    _switch("output_hdcp", "Output HDCP", SET_LEVEL, 161, "mdi:shield-key", optional=True),
    _number("colour_red", "Colour red", 4, 0, 100, "mdi:palette", per_input=True),
    _number("colour_green", "Colour green", 5, 0, 100, "mdi:palette", per_input=True),
    _number("colour_blue", "Colour blue", 6, 0, 100, "mdi:palette", per_input=True),
    _number("brightness", "Brightness", 16, 0, 100, "mdi:brightness-6", per_input=True),
    _number("contrast", "Contrast", 17, 0, 100, "mdi:contrast-box", per_input=True),
    _number("hue", "Hue", 25, 0, 100, "mdi:palette-swatch", per_input=True),
    _number("sharpness", "Sharpness", 26, 0, 100, "mdi:image-filter-center-focus", per_input=True),
    _number("saturation", "Saturation", 29, 0, 100, "mdi:palette", per_input=True),
    _number("output_volume", "Output volume", 33, 0, 100, "mdi:volume-high"),
    _number("input_volume", "Input volume", 34, 0, 100, "mdi:volume-medium", per_input=True),
    _number("osd_h", "OSD horizontal position", 41, 0, 100, "mdi:arrow-left-right"),
    _number("osd_v", "OSD vertical position", 42, 0, 100, "mdi:arrow-up-down"),
    _number("osd_timeout", "OSD timeout", 43, 5, 100, "mdi:timer-sand"),
    _number("osd_background", "OSD background", 44, 0, 8, "mdi:layers-outline"),
)

BY_KEY = {c.key: c for c in CONTROLS}
BY_TYPE_FN: dict[tuple[int, int], Vp436Control] = {}
for _control in CONTROLS:
    BY_TYPE_FN[(_control.set_type, _control.fn)] = _control
    BY_TYPE_FN[(_control.get_type, _control.fn)] = _control

# Settings kept for each kind of input: when the input changes the device shows the new input's values.
PER_INPUT_KEYS = tuple(c.key for c in CONTROLS if c.per_input)

# Key presses: (key, name, key number, icon). The first three do something useful on their own; the rest
# are the remote control's keys.
KEY_BUTTONS = (
    ("auto_adjust", "Auto adjust", 33, "mdi:auto-fix"),
    ("volume_up", "Volume up", 37, "mdi:volume-plus"),
    ("volume_down", "Volume down", 35, "mdi:volume-minus"),
    ("menu", "Menu", 20, "mdi:menu"),
    ("up", "Up", 15, "mdi:arrow-up-bold"),
    ("down", "Down", 21, "mdi:arrow-down-bold"),
    ("left", "Left", 17, "mdi:arrow-left-bold"),
    ("right", "Right", 19, "mdi:arrow-right-bold"),
    ("ok", "OK", 18, "mdi:check-circle-outline"),
    ("exit", "Exit", 22, "mdi:exit-to-app"),
    ("info", "Info", 14, "mdi:information-outline"),
)
VOLUME_KEYS = (37, 35)


def label(control: Vp436Control, value: int | None) -> str | None:
    """Return the name of a value of a setting with named choices (None if it is not one of them)."""
    if value is None or control.options is None:
        return None
    return control.options.get(value)
