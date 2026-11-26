"""The controls of a Kramer SP-11D digital video processor, as found by probing a real unit.

Every setting is one numbered parameter. Parameters 0 to 18 are stored in each of the
16 presets; 20 to 26 are global and are not changed by recalling a preset.
"""

from __future__ import annotations

from dataclasses import dataclass

from .vp729_defs import KIND_NUMBER, KIND_SELECT, KIND_SENSOR, KIND_SWITCH

PRESET_COUNT = 16
PRESET_FN = 27  # the preset in use (read only; changed by recalling one)
FREEZE_FN = 26
OUTPUT_STANDARD_FN = 21
SCH_FN = 24
DELAY_FN = 25
DETECTED_STANDARD_FN = 19

# Parameters read at start-up and at every refresh (28, GENLOCK_STAT, always read the same
# value on the unit tested, with no genlock source, so it is not used).
READ_FNS = tuple(range(28))

INPUT_FORMATS = {0: "CV (composite)", 1: "YC (S-video)", 2: "YUV", 3: "RGB/S", 4: "SDI"}
STANDARDS = {0: "PAL B", 1: "PAL N", 2: "PAL M", 3: "NTSC 3", 4: "NTSC 4", 5: "SECAM"}
INPUT_STANDARDS = {**STANDARDS, 6: "Auto"}
COLOUR_SPACES = {0: "Off", 1: "YUV", 2: "RGB"}
OUTPUT_FORMATS = {0: "YUV", 1: "RGB", 2: "RGBS"}


@dataclass(frozen=True)
class Sp11dControl:
    """One parameter of the unit."""

    fn: int
    key: str
    name: str
    kind: str
    options: dict[int, str] | None = None
    minimum: int = 0
    maximum: int = 0
    icon: str | None = None
    diagnostic: bool = False


def _number(fn: int, key: str, name: str, low: int, high: int, icon: str | None = None) -> Sp11dControl:
    return Sp11dControl(fn, key, name, KIND_NUMBER, minimum=low, maximum=high, icon=icon)


def _select(fn: int, key: str, name: str, options: dict[int, str], icon: str | None = None) -> Sp11dControl:
    return Sp11dControl(fn, key, name, KIND_SELECT, options=options, icon=icon)


def _switch(fn: int, key: str, name: str, icon: str | None = None) -> Sp11dControl:
    return Sp11dControl(fn, key, name, KIND_SWITCH, options={0: "Off", 1: "On"}, icon=icon)


CONTROLS: tuple[Sp11dControl, ...] = (
    _select(0, "input_format", "Input format", INPUT_FORMATS, "mdi:video-input-component"),
    _select(1, "input_standard", "Input standard", INPUT_STANDARDS, "mdi:television-classic"),
    _select(18, "colour_space", "Colour space", COLOUR_SPACES, "mdi:palette"),
    _select(20, "output_format", "Output format", OUTPUT_FORMATS, "mdi:video-output"),
    _select(21, "output_standard", "Output standard", STANDARDS, "mdi:television-classic"),
    _number(2, "splitter_position", "Splitter position", -100, 100, "mdi:view-split-vertical"),
    _number(3, "sharpness_h", "Sharpness H", 0, 15, "mdi:image-filter-center-focus"),
    _number(4, "sharpness_v", "Sharpness V", 0, 15, "mdi:image-filter-center-focus"),
    _number(5, "contrast", "Contrast", -100, 100, "mdi:contrast-box"),
    _number(6, "brightness", "Brightness", -100, 100, "mdi:brightness-6"),
    _number(7, "video_gain", "Video gain", -100, 100, "mdi:signal"),
    _number(8, "shift_h", "Shift H", -16, 15, "mdi:arrow-left-right"),
    _number(9, "shift_v", "Shift V", -1, 1, "mdi:arrow-up-down"),
    _number(10, "y", "Y", -100, 100, "mdi:palette-swatch"),
    _number(11, "u", "U", -100, 100, "mdi:palette-swatch"),
    _number(12, "v", "V", -100, 100, "mdi:palette-swatch"),
    _number(13, "green", "Green", -100, 100, "mdi:palette-swatch"),
    _number(14, "blue", "Blue", -100, 100, "mdi:palette-swatch"),
    _number(15, "red", "Red", -100, 100, "mdi:palette-swatch"),
    _number(16, "colour", "Colour", -100, 100, "mdi:palette"),
    _number(17, "hue", "Hue", -100, 100, "mdi:palette"),
    _number(SCH_FN, "sch", "SCH", -100, 100, "mdi:sine-wave"),
    _number(DELAY_FN, "delay", "Delay", -32, 31, "mdi:timer-outline"),
    _switch(22, "genlock", "Genlock", "mdi:sync"),
    _switch(23, "panel_lock", "Panel lock", "mdi:lock"),
    _switch(FREEZE_FN, "freeze", "Freeze", "mdi:snowflake"),
    Sp11dControl(
        DETECTED_STANDARD_FN,
        "detected_input_standard",
        "Detected input standard",
        KIND_SENSOR,
        options=STANDARDS,
        icon="mdi:television-classic",
    ),
)

BY_FN = {c.fn: c for c in CONTROLS}

# Step buttons, mirroring the + and - buttons on the front panel: (key, name, fn, change).
STEP_BUTTONS = (
    ("sch_up", "SCH +1", SCH_FN, 1),
    ("sch_down", "SCH -1", SCH_FN, -1),
    ("delay_up", "Delay +1", DELAY_FN, 1),
    ("delay_down", "Delay -1", DELAY_FN, -1),
)

def label(control: Sp11dControl, value: int | None) -> str | None:
    """Return the name of a value of a setting with named choices (None if it is not one of them)."""
    if value is None or control.options is None:
        return None
    return control.options.get(value)


def firmware_text(high: int, low: int) -> str:
    """Return the software version the unit reports, for example 2.0."""
    return f"{high}.{low}"


def model_code_text(high: int, low: int) -> str:
    """Return the machine name code the unit reports, as it gives it, for example 1.2."""
    return f"{high}.{low}"
