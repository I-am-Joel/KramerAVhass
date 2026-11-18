"""Control table for the Kramer VP-729 presentation scaler/switcher.

Function numbers, ranges and option names come from the VP-729 communication
protocol in the Kramer manual. Nothing here imports Home Assistant.

`depends_on` lists the functions whose value decides whether a control can be
read or set (for example H-zoom needs the aspect ratio set to Custom). When
one of those changes, the dependent controls are read again straight away.
"""

from __future__ import annotations

from dataclasses import dataclass

KIND_SELECT = "select"
KIND_NUMBER = "number"
KIND_SWITCH = "switch"
KIND_BUTTON = "button"
KIND_SENSOR = "sensor"

POWER_FN = 91
RESOLUTION_FN = 25
# Power on: acknowledged at once, then silent for about 8 seconds.
POWER_ON_SETTLE_SECONDS = 10.0

# Errors from the unit that mean "not available at the moment / on this input".
UNAVAILABLE_ERRORS = (2, 3, 7)


@dataclass(frozen=True)
class Control:
    """One function of the unit."""

    fn: int
    key: str
    name: str
    kind: str
    options: dict[int, str] | None = None
    minimum: int | None = None
    maximum: int | None = None
    writable: bool = True
    depends_on: tuple[int, ...] = ()
    press_value: int | None = None  # buttons: value sent with the key press (None = none)
    settle: float = 0.0  # seconds the unit stays silent after a set
    diagnostic: bool = False
    icon: str | None = None
    unit: str | None = None
    step: int = 1
    note: str = ""

    @property
    def readable(self) -> bool:
        """Return whether the unit can be asked for this value."""
        return self.kind != KIND_BUTTON


def _on_off(fn: int, key: str, name: str, **kw) -> Control:
    return Control(fn, key, name, KIND_SWITCH, options={0: "Off", 1: "On"}, **kw)


_LEVEL4 = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}
_LEVEL3 = {0: "Off", 1: "Low", 2: "High"}

OUTPUT_RESOLUTIONS = {
    0: "Native HDMI",
    1: "640x480@60Hz",
    2: "640x480@75Hz",
    3: "800x600@50Hz",
    4: "800x600@60Hz",
    5: "800x600@75Hz",
    6: "1024x768@50Hz",
    7: "1024x768@60Hz",
    8: "1024x768@75Hz",
    9: "1280x768@50Hz",
    10: "1280x768@60Hz",
    11: "1280x720@60Hz",
    12: "1280x800@60Hz",
    13: "1280x1024@50Hz",
    14: "1280x1024@60Hz",
    15: "1280x1024@75Hz",
    16: "1366x768@50Hz",
    17: "1366x768@60Hz",
    18: "1400x1050@50Hz",
    19: "1400x1050@60Hz",
    20: "1600x1200@50Hz",
    21: "1600x1200@60Hz",
    22: "1680x1050@60Hz",
    23: "1920x1080@60Hz",
    24: "1920x1200@60Hz",
    25: "480p@60Hz",
    26: "576p@60Hz",
    27: "720p@50Hz",
    28: "720p@60Hz",
    29: "1080i@50Hz",
    30: "1080i@60Hz",
    31: "1080p@50Hz",
    32: "1080p@60Hz",
    33: "480p@59.94Hz",
    34: "720p@59.94Hz",
    35: "1080i@59.94Hz",
    36: "1080p@23.98Hz",
    37: "1080p@29.97Hz",
    38: "1080p@59.94Hz",
    96: "Custom 1",
    97: "Custom 2",
    98: "Custom 3",
    99: "Custom 4",
}

INPUT_STATUS = {
    0: "640x480 60",
    1: "640x480 67 Mac13",
    2: "640x480 72",
    3: "640x480 75",
    4: "640x480 85",
    5: "720x400 70",
    6: "720x400 85",
    7: "800x600 56",
    8: "800x600 60",
    9: "800x600 72",
    10: "800x600 75",
    11: "800x600 85",
    12: "832x624 75 Mac16",
    13: "1024x768 60",
    14: "1024x768 70",
    15: "1024x768 75",
    16: "1024x768 75 Mac19",
    17: "1024x768 85",
    18: "1024x800 84 Sun",
    19: "1152x864 75",
    20: "1152x870 75 Mac21",
    21: "1152x900 66 Sun",
    22: "1152x900 76 Sun",
    23: "1280x960 60",
    24: "1280x960 85",
    25: "1280x768 60",
    26: "1280x1024 60",
    27: "1280x1024 75",
    28: "1280x1024 76 Sun",
    29: "1280x1024 85",
    30: "1400x1050 60",
    31: "1400x1050 75",
    32: "1600x1200 60",
    33: "1680x1050 60",
    34: "1080i 60",
    35: "1080i 50",
    36: "1080p 60",
    37: "1080p 50",
    38: "720p 60",
    39: "720p 50",
    40: "480i",
    41: "480p",
    42: "576i",
    43: "576p",
    44: "1280x800 60 (reduced blanking)",
    45: "1920x1200 60",
    46: "1920x1080 60",
    47: "1280x720 60",
    48: "1080p 24",
    49: "1280x800 60",
    50: "1440x900 60",
    51: "1440x900 60 (reduced blanking)",
    52: "1280x768 60 (reduced blanking)",
    53: "1680x1050 60 (reduced blanking)",
    54: "1366x768 60",
    55: "1366x768 60 (reduced blanking)",
    94: "Custom 1",
    95: "Custom 2",
    96: "Custom 3",
    97: "Custom 4",
    98: "No input detected",
    99: "Other",
    101: "NTSC",
    102: "PAL",
    103: "PAL-M",
    104: "PAL-N",
    105: "NTSC 4.43",
    106: "SECAM",
    107: "PAL-60",
}

_SOURCES = {
    0: "Input 1",
    1: "Input 2",
    2: "Input 3",
    3: "Input 4",
    4: "VGA 1",
    5: "VGA 2",
    6: "HDMI 1",
    7: "HDMI 2",
}
_INPUT_TYPES = {0: "Component", 1: "YC", 2: "Video"}
_POLARITY = {0: "Negative", 1: "Positive"}
_PROFILES = range(1, 9)

CONTROLS: tuple[Control, ...] = (
    # --- Input ---------------------------------------------------------------
    Control(0, "input_source", "Input source", KIND_SELECT, {**_SOURCES, 8: "USB"}, icon="mdi:video-input-hdmi"),
    Control(1, "input_1_type", "Input 1 source type", KIND_SELECT, _INPUT_TYPES),
    Control(2, "input_2_type", "Input 2 source type", KIND_SELECT, _INPUT_TYPES),
    Control(3, "input_3_type", "Input 3 source type", KIND_SELECT, _INPUT_TYPES),
    Control(4, "input_4_type", "Input 4 source type", KIND_SELECT, _INPUT_TYPES),
    Control(5, "input_colour_format", "Input colour format", KIND_SELECT, {0: "Auto", 1: "RGB", 2: "YUV"}, depends_on=(0,)),
    Control(
        6,
        "input_video_standard",
        "Input video standard",
        KIND_SELECT,
        {0: "Auto", 1: "NTSC", 2: "PAL", 3: "PAL-M", 4: "PAL-N", 5: "NTSC 4.43", 6: "SECAM", 7: "PAL-60"},
        depends_on=(0,),
    ),
    Control(7, "input_h_position", "Input H-position", KIND_NUMBER, minimum=1, maximum=50, depends_on=(0,)),
    Control(8, "input_v_position", "Input V-position", KIND_NUMBER, minimum=2, maximum=50, depends_on=(0,)),
    Control(9, "input_frequency", "Input frequency", KIND_NUMBER, minimum=0, maximum=50, depends_on=(0,)),
    Control(10, "input_phase", "Input phase", KIND_NUMBER, minimum=0, maximum=31, depends_on=(0,)),
    Control(11, "auto_image", "Auto image", KIND_BUTTON, icon="mdi:auto-fix"),
    # --- Picture -------------------------------------------------------------
    Control(12, "brightness", "Brightness", KIND_NUMBER, minimum=0, maximum=100),
    Control(13, "contrast", "Contrast", KIND_NUMBER, minimum=0, maximum=100),
    Control(14, "colour", "Colour", KIND_NUMBER, minimum=0, maximum=100),
    Control(15, "hue", "Hue", KIND_NUMBER, minimum=0, maximum=360),
    Control(16, "sharpness", "Sharpness", KIND_NUMBER, minimum=0, maximum=100),
    Control(17, "gamma", "Output gamma", KIND_SELECT, {0: "Gamma 1", 1: "Gamma 2", 2: "Gamma 3", 3: "Gamma 4", 4: "Gamma 5"}),
    Control(18, "film_mode", "Film mode", KIND_SELECT, {0: "Auto", 1: "Video", 2: "Film"}),
    Control(19, "temporal_nr", "Temporal noise reduction", KIND_SELECT, _LEVEL4),
    Control(20, "mosquito_nr", "Mosquito noise reduction", KIND_SELECT, _LEVEL4),
    _on_off(21, "block_nr", "Block noise reduction"),
    Control(22, "detail_enhancement", "Detail enhancement", KIND_SELECT, _LEVEL4),
    Control(23, "luma_transition", "Luma transition enhancement", KIND_SELECT, _LEVEL3),
    Control(24, "chroma_transition", "Chroma transition enhancement", KIND_SELECT, _LEVEL3),
    # --- Output --------------------------------------------------------------
    Control(25, "output_resolution", "Output resolution", KIND_SELECT, OUTPUT_RESOLUTIONS, icon="mdi:monitor"),
    Control(26, "hdmi_type", "Output HDMI type", KIND_SELECT, {0: "Auto", 1: "HDMI", 2: "DVI"}),
    Control(
        27,
        "aspect_ratio",
        "Aspect ratio",
        KIND_SELECT,
        {0: "Best fit", 1: "Letterbox", 2: "Follow output", 3: "Virtual wide", 4: "Follow input", 5: "Custom"},
        icon="mdi:aspect-ratio",
    ),
    # --- Pan and zoom ----------------------------------------------------------
    Control(28, "h_pan", "H-pan", KIND_NUMBER, minimum=-16, maximum=16, depends_on=(27, 30)),
    Control(29, "v_pan", "V-pan", KIND_NUMBER, minimum=-16, maximum=16, depends_on=(27, 31)),
    Control(30, "h_zoom", "H-zoom", KIND_NUMBER, minimum=-8, maximum=8, depends_on=(27,)),
    Control(31, "v_zoom", "V-zoom", KIND_NUMBER, minimum=-8, maximum=8, depends_on=(27,)),
    Control(
        32,
        "zoom",
        "Zoom",
        KIND_SELECT,
        {
            0: "100%",
            1: "150%",
            2: "200%",
            3: "225%",
            4: "250%",
            5: "275%",
            6: "300%",
            7: "325%",
            8: "350%",
            9: "375%",
            10: "400%",
            11: "Custom",
        },
        depends_on=(27, 36),
        icon="mdi:magnify-plus-outline",
    ),
    Control(33, "custom_zoom", "Custom zoom", KIND_NUMBER, minimum=0, maximum=32, depends_on=(27, 32)),
    Control(34, "zoom_h_pan", "Zoom H-pan", KIND_NUMBER, minimum=0, maximum=32, depends_on=(27, 32, 33)),
    Control(35, "zoom_v_pan", "Zoom V-pan", KIND_NUMBER, minimum=0, maximum=32, depends_on=(27, 32, 33)),
    # --- Picture in picture ----------------------------------------------------
    _on_off(36, "pip", "Picture in picture", icon="mdi:picture-in-picture-bottom-right"),
    Control(37, "pip_type", "PIP type", KIND_SELECT, {0: "Picture-in-picture", 1: "Picture + picture", 2: "Split"}, depends_on=(36,)),
    Control(38, "pip_source", "PIP source", KIND_SELECT, _SOURCES, icon="mdi:video-input-component"),
    Control(39, "pip_size", "PIP size", KIND_SELECT, {0: "1/25", 1: "1/16", 2: "1/9", 3: "1/4", 4: "Custom"}, depends_on=(36,)),
    Control(40, "pip_h_position", "PIP H-position", KIND_NUMBER, minimum=0, maximum=128, depends_on=(36, 39)),
    Control(41, "pip_v_position", "PIP V-position", KIND_NUMBER, minimum=0, maximum=128, depends_on=(36, 39)),
    Control(42, "pip_h_size", "PIP H-size", KIND_NUMBER, minimum=1, maximum=255, depends_on=(36, 39)),
    Control(43, "pip_v_size", "PIP V-size", KIND_NUMBER, minimum=1, maximum=255, depends_on=(36, 39)),
    _on_off(44, "pip_frame", "PIP frame", depends_on=(36,)),
    Control(45, "pip_frame_colour", "PIP frame colour", KIND_SELECT, {0: "Red", 1: "Green", 2: "Blue"}, depends_on=(36,)),
    # --- Audio -----------------------------------------------------------------
    Control(46, "audio_input_type", "Audio input type", KIND_SELECT, {0: "Analogue", 1: "S/PDIF"}),
    Control(47, "audio_input_volume", "Audio input volume", KIND_NUMBER, minimum=-22, maximum=22),
    Control(48, "audio_output_volume", "Audio output volume", KIND_NUMBER, minimum=-100, maximum=24),
    Control(49, "bass", "Bass", KIND_NUMBER, minimum=-36, maximum=36),
    Control(50, "treble", "Treble", KIND_NUMBER, minimum=-36, maximum=36),
    Control(51, "balance", "Balance", KIND_NUMBER, minimum=-10, maximum=10),
    _on_off(52, "loudness", "Loudness"),
    Control(53, "audio_delay", "Audio delay mode", KIND_SELECT, {0: "Dynamic", 1: "User defined"}),
    Control(
        54,
        "user_delay",
        "User audio delay",
        KIND_NUMBER,
        minimum=0,
        maximum=340,
        depends_on=(53,),
        unit="ms",
        step=2,
        note="0 to 340 ms in 2 ms steps (an odd value is rounded up by the unit); needs Audio delay mode set to User defined",
    ),
    Control(
        55,
        "usb_audio_input",
        "Audio input for USB",
        KIND_SELECT,
        {0: "No audio", 1: "Input 1", 2: "Input 2", 3: "Input 3", 4: "Input 4", 5: "VGA 1", 6: "VGA 2", 7: "HDMI 1", 8: "HDMI 2"},
    ),
    # --- Geometry --------------------------------------------------------------
    Control(56, "geometry_application", "Geometry application", KIND_SELECT, {0: "Keystone", 1: "Anyplace", 2: "Rotation"}),
    Control(57, "geometry_location", "Geometry location", KIND_SELECT, {0: "Front", 1: "Ceiling", 2: "Rear", 3: "Rear ceiling"}),
    Control(58, "h_keystone", "Horizontal keystone", KIND_NUMBER, minimum=-40, maximum=40),
    Control(59, "v_keystone", "Vertical keystone", KIND_NUMBER, minimum=-30, maximum=30),
    Control(60, "top_left_h", "Top left H", KIND_NUMBER, minimum=0, maximum=1000, depends_on=(56,)),
    Control(61, "top_left_v", "Top left V", KIND_NUMBER, minimum=0, maximum=1000, depends_on=(56,)),
    Control(62, "top_right_h", "Top right H", KIND_NUMBER, minimum=-1000, maximum=0, depends_on=(56,)),
    Control(63, "top_right_v", "Top right V", KIND_NUMBER, minimum=0, maximum=1000, depends_on=(56,)),
    Control(64, "bottom_left_h", "Bottom left H", KIND_NUMBER, minimum=0, maximum=1000, depends_on=(56,)),
    Control(65, "bottom_left_v", "Bottom left V", KIND_NUMBER, minimum=-1000, maximum=0, depends_on=(56,)),
    Control(66, "bottom_right_h", "Bottom right H", KIND_NUMBER, minimum=-1000, maximum=0, depends_on=(56,)),
    Control(67, "bottom_right_v", "Bottom right V", KIND_NUMBER, minimum=-1000, maximum=0, depends_on=(56,)),
    Control(69, "pincushion_barrel", "Pincushion/barrel", KIND_NUMBER, minimum=-20, maximum=20),
    Control(70, "rotation", "Rotation", KIND_NUMBER, minimum=-180, maximum=180, depends_on=(56,)),
    # --- Set-up and display ----------------------------------------------------
    _on_off(74, "frame_lock", "Frame lock"),
    Control(76, "firmware", "Firmware revision", KIND_SENSOR, writable=False, diagnostic=True, icon="mdi:chip"),
    Control(77, "mode_set_1", "Mode set 1", KIND_SELECT, {0: "1400x1050x60", 1: "1680x1050x60"}),
    Control(78, "mode_set_2", "Mode set 2", KIND_SELECT, {0: "1280x1024x75", 1: "1280x1024x76"}),
    Control(
        79,
        "osd_position",
        "OSD menu position",
        KIND_SELECT,
        {0: "Centre", 1: "Top left", 2: "Top right", 3: "Bottom left", 4: "Bottom right"},
    ),
    Control(
        80,
        "osd_timeout",
        "OSD timeout",
        KIND_SELECT,
        {0: "5 seconds", 1: "10 seconds", 2: "20 seconds", 3: "30 seconds", 4: "60 seconds", 5: "90 seconds", 6: "Off"},
    ),
    Control(81, "logo", "Logo", KIND_SELECT, {0: "Off", 1: "On", 2: "Custom"}),
    Control(82, "blank_colour", "Blank colour", KIND_SELECT, {0: "Black", 1: "Blue"}),
    Control(84, "background", "Background", KIND_SELECT, {0: "Black", 1: "Blue", 2: "Custom", 3: "Disable analogue sync"}),
    _on_off(85, "save_lock", "Save lock", icon="mdi:lock-outline"),
    _on_off(86, "input_lock", "Input lock"),
    Control(87, "blank_key", "Blank key function", KIND_SELECT, {0: "Blank and mute", 1: "Blank", 2: "Mute"}),
    Control(88, "freeze_key", "Freeze key function", KIND_SELECT, {0: "Freeze and mute", 1: "Freeze", 2: "Mute"}),
    _on_off(89, "freeze", "Freeze", icon="mdi:snowflake"),
    _on_off(90, "blank", "Blank", icon="mdi:monitor-off"),
    _on_off(91, "power", "Power (standby)", settle=POWER_ON_SETTLE_SECONDS, icon="mdi:power"),
    _on_off(101, "mute", "Mute", icon="mdi:volume-off"),
    _on_off(102, "front_panel_lock", "Front panel lock", icon="mdi:lock"),
    # --- Input status (read only) ----------------------------------------------
    Control(103, "main_input_status", "Main input status", KIND_SENSOR, INPUT_STATUS, writable=False, icon="mdi:video-input-antenna"),
    Control(104, "pip_input_status", "PIP input status", KIND_SENSOR, INPUT_STATUS, writable=False, icon="mdi:video-input-antenna"),
    # --- Advanced timing (read only) -------------------------------------------
    Control(105, "adv_in_ht", "Advanced input mode HT", KIND_SENSOR, writable=False, diagnostic=True),
    Control(106, "adv_in_hw", "Advanced input mode HW", KIND_SENSOR, writable=False, diagnostic=True),
    Control(107, "adv_in_hs", "Advanced input mode HS", KIND_SENSOR, writable=False, diagnostic=True),
    Control(108, "adv_in_ha", "Advanced input mode HA", KIND_SENSOR, writable=False, diagnostic=True),
    Control(109, "adv_in_hp", "Advanced input mode HP", KIND_SENSOR, _POLARITY, writable=False, diagnostic=True),
    Control(110, "adv_in_vt", "Advanced input mode VT", KIND_SENSOR, writable=False, diagnostic=True),
    Control(111, "adv_in_vw", "Advanced input mode VW", KIND_SENSOR, writable=False, diagnostic=True),
    Control(112, "adv_in_vs", "Advanced input mode VS", KIND_SENSOR, writable=False, diagnostic=True),
    Control(113, "adv_in_va", "Advanced input mode VA", KIND_SENSOR, writable=False, diagnostic=True),
    Control(114, "adv_in_vp", "Advanced input mode VP", KIND_SENSOR, _POLARITY, writable=False, diagnostic=True),
    Control(115, "adv_in_oclk_int", "Advanced input mode OCLK (integer)", KIND_SENSOR, writable=False, diagnostic=True),
    Control(116, "adv_in_oclk_dec", "Advanced input mode OCLK (decimal)", KIND_SENSOR, writable=False, diagnostic=True),
    Control(117, "adv_in_enable", "Advanced input mode enabled", KIND_SENSOR, {0: "Off", 1: "On"}, writable=False, diagnostic=True),
    Control(119, "adv_out_ht", "Advanced output mode HT", KIND_SENSOR, writable=False, diagnostic=True),
    Control(120, "adv_out_hw", "Advanced output mode HW", KIND_SENSOR, writable=False, diagnostic=True),
    Control(121, "adv_out_hs", "Advanced output mode HS", KIND_SENSOR, writable=False, diagnostic=True),
    Control(122, "adv_out_ha", "Advanced output mode HA", KIND_SENSOR, writable=False, diagnostic=True),
    Control(123, "adv_out_hp", "Advanced output mode HP", KIND_SENSOR, _POLARITY, writable=False, diagnostic=True),
    Control(124, "adv_out_vt", "Advanced output mode VT", KIND_SENSOR, writable=False, diagnostic=True),
    Control(125, "adv_out_vw", "Advanced output mode VW", KIND_SENSOR, writable=False, diagnostic=True),
    Control(126, "adv_out_vs", "Advanced output mode VS", KIND_SENSOR, writable=False, diagnostic=True),
    Control(127, "adv_out_va", "Advanced output mode VA", KIND_SENSOR, writable=False, diagnostic=True),
    Control(128, "adv_out_vp", "Advanced output mode VP", KIND_SENSOR, _POLARITY, writable=False, diagnostic=True),
    Control(129, "adv_out_oclk_int", "Advanced output mode OCLK (integer)", KIND_SENSOR, writable=False, diagnostic=True),
    Control(130, "adv_out_oclk_dec", "Advanced output mode OCLK (decimal)", KIND_SENSOR, writable=False, diagnostic=True),
    # --- Keys (press only) -------------------------------------------------------
    Control(83, "capture", "Capture", KIND_BUTTON, icon="mdi:camera"),
    Control(92, "key_info", "Info", KIND_BUTTON, icon="mdi:information-outline"),
    Control(93, "key_menu", "Menu", KIND_BUTTON, icon="mdi:menu"),
    Control(94, "key_top", "Up", KIND_BUTTON, icon="mdi:arrow-up-bold"),
    Control(95, "key_down", "Down", KIND_BUTTON, icon="mdi:arrow-down-bold"),
    Control(96, "key_left", "Left", KIND_BUTTON, icon="mdi:arrow-left-bold"),
    Control(97, "key_right", "Right", KIND_BUTTON, icon="mdi:arrow-right-bold"),
    Control(98, "key_enter", "Enter", KIND_BUTTON, icon="mdi:keyboard-return"),
    Control(99, "key_picture", "Picture", KIND_BUTTON, icon="mdi:image"),
    Control(100, "key_swap", "Swap", KIND_BUTTON, icon="mdi:swap-horizontal"),
)

# Volume up/down are the same function (48) with a special value, and the
# profiles are one function (72 save, 73 recall) with a profile number, so
# they get their own button definitions.
VOLUME_BUTTONS: tuple[tuple[str, str, int, str], ...] = (
    ("volume_up", "Volume up", 111, "mdi:volume-plus"),
    ("volume_down", "Volume down", -111, "mdi:volume-minus"),
)

PROFILE_SAVE_FN = 72
PROFILE_RECALL_FN = 73
# Measured on a real unit: a save is acknowledged after about 9 seconds; a recall is
# acknowledged at once and then the unit is silent for about 15 seconds.
PROFILE_SAVE_TIMEOUT = 20.0
PROFILE_SAVE_SETTLE_SECONDS = 1.0
PROFILE_RECALL_SETTLE_SECONDS = 20.0
PROFILE_COUNT = len(_PROFILES)

BY_FN: dict[int, Control] = {control.fn: control for control in CONTROLS}
READABLE: tuple[Control, ...] = tuple(control for control in CONTROLS if control.readable)

# Functions whose reading depends on another function, for re-reading after a change.
DEPENDENTS: dict[int, tuple[int, ...]] = {}
for _control in CONTROLS:
    for _dep in _control.depends_on:
        DEPENDENTS.setdefault(_dep, ())
        DEPENDENTS[_dep] = (*DEPENDENTS[_dep], _control.fn)


# ---------------------------------------------------------------------------
# Limits that depend on the output resolution (measured on a real unit)
# ---------------------------------------------------------------------------

_SD_480 = (720, 480)
_SD_576 = (720, 576)
_HD_720 = (1280, 720)
_HD_1080 = (1920, 1080)

# Output resolution (function 25) -> (width, height). Native HDMI and the custom
# modes have no fixed size, so they are not listed.
OUTPUT_SIZES: dict[int, tuple[int, int]] = {
    1: (640, 480),
    2: (640, 480),
    3: (800, 600),
    4: (800, 600),
    5: (800, 600),
    6: (1024, 768),
    7: (1024, 768),
    8: (1024, 768),
    9: (1280, 768),
    10: (1280, 768),
    11: _HD_720,
    12: (1280, 800),
    13: (1280, 1024),
    14: (1280, 1024),
    15: (1280, 1024),
    16: (1366, 768),
    17: (1366, 768),
    18: (1400, 1050),
    19: (1400, 1050),
    20: (1600, 1200),
    21: (1600, 1200),
    22: (1680, 1050),
    23: _HD_1080,
    24: (1920, 1200),
    25: _SD_480,
    26: _SD_576,
    27: _HD_720,
    28: _HD_720,
    29: _HD_1080,
    30: _HD_1080,
    31: _HD_1080,
    32: _HD_1080,
    33: _SD_480,
    34: _HD_720,
    35: _HD_1080,
    36: _HD_1080,
    37: _HD_1080,
    38: _HD_1080,
}

# Corner moves: the unit takes 0.45 of the output width (H corners) or height (V
# corners), in one direction only, and silently ignores anything beyond that.
# Measured at 1920x1080 (864 / 486), 1280x720 (576 / 324) and 1024x768 (461 / 346).
CORNER_FRACTION = 0.45
CORNER_H = (60, 62, 64, 66)
CORNER_V = (61, 63, 65, 67)
CORNER_NEGATIVE = (62, 65, 66, 67)
CUSTOM_ZOOM_FN = 33
CUSTOM_ZOOM_MAX_1080 = 21  # at 1920x1080 the unit stops at 21 (32 at 1280x720 and 1024x768)


def limits(control: Control, resolution: int | str | None) -> tuple[int, int]:
    """Return (minimum, maximum) for a number, allowing for the output resolution.

    Resolutions that were not measured use the widest range the unit documents,
    so a valid value is never blocked; the unit still decides.
    """
    low, high = control.minimum, control.maximum
    size = OUTPUT_SIZES.get(resolution) if isinstance(resolution, int) else None
    if size is None:
        return low, high
    if control.fn in CORNER_H or control.fn in CORNER_V:
        edge = size[0] if control.fn in CORNER_H else size[1]
        reach = int(CORNER_FRACTION * edge + 0.5)
        return (-reach, 0) if control.fn in CORNER_NEGATIVE else (0, reach)
    if control.fn == CUSTOM_ZOOM_FN and size == _HD_1080:
        return low, CUSTOM_ZOOM_MAX_1080
    return low, high


# ---------------------------------------------------------------------------
# Why a control is not available
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """A condition another function must meet before a control works."""

    fn: int
    values: tuple[int, ...]
    equal: bool
    text: str

    def met(self, value: int | str | None) -> bool:
        """Return whether the condition holds (an unknown value is not blamed)."""
        if not isinstance(value, int):
            return True
        return (value in self.values) == self.equal


_ASPECT_CUSTOM = Requirement(27, (5,), True, "Aspect ratio set to Custom")
_ZOOM_CUSTOM = Requirement(32, (11,), True, "Zoom set to Custom")
_PIP_ON = Requirement(36, (1,), True, "Picture in picture on")
_PIP_OFF = Requirement(36, (0,), True, "Picture in picture off")
_PIP_CUSTOM = Requirement(39, (4,), True, "PIP size set to Custom")

REQUIREMENTS: dict[int, tuple[Requirement, ...]] = {
    28: (_ASPECT_CUSTOM, Requirement(30, (0,), False, "H-zoom other than 0")),
    29: (_ASPECT_CUSTOM, Requirement(31, (0,), False, "V-zoom other than 0")),
    30: (_ASPECT_CUSTOM,),
    31: (_ASPECT_CUSTOM,),
    32: (Requirement(27, (5,), False, "Aspect ratio not set to Custom"), _PIP_OFF),
    33: (_ZOOM_CUSTOM,),
    34: (_ZOOM_CUSTOM, Requirement(33, (0,), False, "Custom zoom above 0")),
    35: (_ZOOM_CUSTOM, Requirement(33, (0,), False, "Custom zoom above 0")),
    37: (_PIP_ON,),
    39: (_PIP_ON,),
    40: (_PIP_ON, _PIP_CUSTOM),
    41: (_PIP_ON, _PIP_CUSTOM),
    42: (_PIP_ON, _PIP_CUSTOM),
    43: (_PIP_ON, _PIP_CUSTOM),
    44: (_PIP_ON,),
    45: (_PIP_ON,),
    54: (Requirement(53, (1,), True, "Audio delay mode set to User defined"),),
    60: (Requirement(56, (1,), True, "Geometry application set to Anyplace"),),
    70: (Requirement(56, (2,), True, "Geometry application set to Rotation"),),
    83: (_PIP_OFF,),
}
for _fn in (61, 62, 63, 64, 65, 66, 67):
    REQUIREMENTS[_fn] = REQUIREMENTS[60]

# Things the unit refuses on some inputs, whatever else is set.
INPUT_DEPENDENT_FNS = (5, 6, 7, 8, 9, 10, 11)
STANDBY_REASON = "The unit is in standby"


def unavailable_reason(fn: int, values: dict[int, int | str | None]) -> str | None:
    """Say why a function cannot be used now, or None if nothing is known to be missing."""
    if fn != POWER_FN and values.get(POWER_FN) == 0:
        return STANDBY_REASON
    unmet = [req.text for req in REQUIREMENTS.get(fn, ()) if not req.met(values.get(req.fn))]
    if fn == 83:
        size = OUTPUT_SIZES.get(values.get(RESOLUTION_FN)) if isinstance(values.get(RESOLUTION_FN), int) else None
        if size and size[0] >= 1400:
            unmet.append("an output resolution under 1400 pixels wide")
    if unmet:
        return "Needs " + " and ".join(unmet)
    if fn in INPUT_DEPENDENT_FNS:
        return "Not available for the selected input"
    return None


def label(control: Control, value: int | str | None) -> str | None:
    """Return the text for a value of a control with named options."""
    if value is None:
        return None
    if control.options is None:
        return str(value)
    if isinstance(value, int) and value in control.options:
        return control.options[value]
    return f"Unknown ({value})"
