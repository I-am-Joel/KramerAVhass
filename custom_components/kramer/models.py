"""Library of known Kramer matrix switchers.

Add a model by adding one entry here. Anything not listed can be set up with
the "Other" choice in the setup dialogue.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import DEFAULT_PORT, DEFAULT_VP729_PORT

OTHER = "other"
PROTOCOL_2000 = "Protocol 2000"
PROTOCOL_ASCII = "ASCII"
PROTOCOL_2BYTE = "2-byte protocol"
PROTOCOL_SP11D = "Video processor protocol"  # the SP-11D's; rename if another processor shares it
VP729_KEY = "vp-729"  # the VP-729 presentation scaler/switcher (not a matrix, so not in MODELS)
VP729_NAME = "VP-729"
SP11D_KEY = "sp-11d"  # the SP-11D digital video processor (not a matrix, so not in MODELS)
SP11D_NAME = "SP-11D"
SP11D_NOTES = (
    "**Model-specific limits:**\n\n"
    "- Uses the video processor protocol. Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit.\n"
    "- Tested as working with DIP switches 1, 2 & 3 on and 4 to 8 off. Set the DIP switch 8 position here to match the device.\n"
    "- Changing the output standard restarts the device. This takes about 3 seconds.\n"
    "- Reset all defaults clears every parameter and all stored presets."
)
DIP8_OFF = "off"
DIP8_ON = "on"
MAX_PORTS = 127
MAX_PRESET_SLOTS = 99
AUDIO_LEVELS_5X4 = (0, 2, 4, 6)


@dataclass(frozen=True)
class KramerModel:
    """Description of one switcher model."""

    key: str
    name: str
    inputs: int
    outputs: int
    supports_modes: bool
    supports_off: bool = True
    supports_lock: bool = True
    preset_slots: int = 0
    notes: str = ""
    default_port: int = DEFAULT_PORT
    protocol: str = PROTOCOL_2000
    machine_max: int = 15  # the highest machine number the model can be given
    audio_levels: tuple[int, ...] = ()  # selectable output levels in dB (empty = no level setting)
    retry_error: bool = False  # answers "error" while someone is using the front panel, so retry
    # The next two apply to the 2-byte protocol only.
    reply_id: int = 0  # bits N6 to N3 of the first byte of the unit's replies
    has_all: bool = False  # has one command that sends an input to every output
    off_command_base: int = 25  # code to switch one output off is this plus the output number
    off_all_command: int = 30  # code that switches every output off
    off_status_base: int = 20  # an output that is off is reported as this plus its position


MODELS: dict[str, KramerModel] = {
    "vs-162av-rca": KramerModel(
        "vs-162av-rca",
        "VS-162AV/RCA",
        16,
        16,
        True,
        preset_slots=60,
        notes=(
            "**Model-specific limits:**\n\n"
            "- Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit.\n"
            "- Front panel lock state is not announced by this model. The integration reads missed "
            'announcements on a refresh cycle (60 seconds by default). "Custom refresh" in the device\'s '
            "Controls will shorten that interval at the cost of increased network traffic.\n"
            "- Presets can be overwritten but the unit refuses to delete them."
        ),
    ),
    "vs-808yc": KramerModel(
        "vs-808yc",
        "VS-808YC",
        8,
        8,
        True,
        preset_slots=16,
        notes=(
            "**Model-specific limits:**\n\n"
            "- Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit.\n"
            "- Tested as working with all DIP switches off."
        ),
    ),
    "vs-812": KramerModel(
        "vs-812",
        "VS-812",
        8,
        1,
        False,
        supports_off=False,
        supports_lock=False,
        notes="**Model-specific limits:**\n\n- Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit.",
    ),
    "vs-1202yc": KramerModel(
        "vs-1202yc",
        "VS-1202YC",
        12,
        2,
        False,
        supports_off=False,
        supports_lock=False,
        protocol=PROTOCOL_2BYTE,
        machine_max=8,
        reply_id=7,
        notes=(
            "**Model-specific limits:**\n\n"
            "- Uses the 2-byte protocol. Serial settings: 1200 baud, 8 data bits, no parity, 1 stop bit.\n"
            "- Audio always follows video."
        ),
    ),
    "vs-5x4": KramerModel(
        "vs-5x4",
        "VS-5x4",
        5,
        4,
        False,
        supports_lock=False,
        machine_max=8,
        audio_levels=AUDIO_LEVELS_5X4,
        retry_error=True,
        notes=(
            "**Model-specific limits:**\n\n"
            "- Uses Protocol 2000. Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit. "
            "Set DIP switch 5 OFF and power the unit off and on again after changing it.\n"
            "- Audio always follows video. Each output has an audio level setting (0, 2, 4 or 6 dB)."
        ),
    ),
    "vs-5x4-2byte": KramerModel(
        "vs-5x4-2byte",
        "VS-5x4",
        5,
        4,
        False,
        supports_lock=False,
        protocol=PROTOCOL_2BYTE,
        machine_max=8,
        audio_levels=AUDIO_LEVELS_5X4,
        reply_id=8,
        has_all=True,
        notes=(
            "**Model-specific limits:**\n\n"
            "- Uses the 2-byte protocol. Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit. "
            "Set DIP switch 5 ON and power the unit off and on again after changing it. "
            "Protocol 2000 is recommended for this unit.\n"
            "- Audio always follows video. Each output has an audio level setting (0, 2, 4 or 6 dB)."
        ),
    ),
    "sis-4x4": KramerModel(
        "sis-4x4",
        "SIS-4x4",
        4,
        4,
        False,
        supports_lock=False,
        protocol=PROTOCOL_2BYTE,
        machine_max=8,
        reply_id=8,
        has_all=True,
        off_command_base=16,
        off_all_command=25,
        off_status_base=16,
        notes=(
            "**Model-specific limits:**\n\n"
            "- Uses the 2-byte protocol. Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit.\n"
            "- Tested as working with DIP switches 1, 2 & 3 on and all others off.\n"
            "- Audio always follows video."
        ),
    ),
}

# Keys saved by earlier versions, mapped to their current entry.
LEGACY_KEYS = {"vs-162av": "vs-162av-rca", "vs-162avrca": "vs-162av-rca"}


def resolve_model_key(key: str | None) -> str | None:
    """Return the current library key for a saved key, or None if unknown."""
    key = LEGACY_KEYS.get(key or "", key)
    return key if key in MODELS else None


def is_vp729(data: Mapping[str, Any]) -> bool:
    """Return whether a config entry's data describes a VP-729."""
    return data.get("model") == VP729_KEY


def is_sp11d(data: Mapping[str, Any]) -> bool:
    """Return whether a config entry's data describes an SP-11D video processor."""
    return data.get("model") == SP11D_KEY


def resolve_model_name(data: Mapping[str, Any]) -> str:
    """Return the model name to show for a config entry's data."""
    if is_sp11d(data):
        return SP11D_NAME
    key = resolve_model_key(data.get("model"))
    if key:
        return MODELS[key].name
    return data.get("model_name") or "Matrix switcher"


def resolve_supports_off(data: Mapping[str, Any]) -> bool:
    """Return whether the model can disconnect an output (unknown models: yes)."""
    key = resolve_model_key(data.get("model"))
    return MODELS[key].supports_off if key else True


def resolve_supports_lock(data: Mapping[str, Any]) -> bool:
    """Return whether the model has a front-panel lock (unknown models: yes)."""
    key = resolve_model_key(data.get("model"))
    return MODELS[key].supports_lock if key else True


def resolve_preset_slots(data: Mapping[str, Any]) -> int:
    """Return how many stored-setup slots the unit has (0 = none, also for unknown models)."""
    key = resolve_model_key(data.get("model"))
    if key:
        return MODELS[key].preset_slots
    try:
        return max(0, min(MAX_PRESET_SLOTS, int(data.get("preset_slots", 0))))
    except (TypeError, ValueError):
        return 0


def resolve_protocol(data: Mapping[str, Any]) -> str:
    """Return the protocol a config entry's model talks (unknown models: Protocol 2000)."""
    key = resolve_model_key(data.get("model"))
    return MODELS[key].protocol if key else PROTOCOL_2000


def resolve_retry_error(data: Mapping[str, Any]) -> bool:
    """Return whether a model answers "error" while its front panel is in use (so requests are retried)."""
    key = resolve_model_key(data.get("model"))
    return MODELS[key].retry_error if key else False


def resolve_audio_levels(data: Mapping[str, Any]) -> tuple[int, ...]:
    """Return the output levels (in dB) a model can be set to; empty if it has no level setting."""
    key = resolve_model_key(data.get("model"))
    return MODELS[key].audio_levels if key else ()


def resolve_notes(data: Mapping[str, Any]) -> str:
    """Return the notes shipped with a model (empty for unknown models)."""
    if is_sp11d(data):
        return SP11D_NOTES
    key = resolve_model_key(data.get("model"))
    return MODELS[key].notes if key else ""


def model_label(name: str, protocol: str) -> str:
    """Return a model name with its protocol, for example "VS-808YC (Protocol 2000)"."""
    return f"{name} ({protocol})"


OTHER_LABEL = f"Other ({PROTOCOL_2000} switchers only)"


def model_choices() -> list[tuple[str, str]]:
    """Return (key, label) for the setup dropdown: by model name (Protocol 2000 first), with "Other" last."""
    entries = [(m.name, m.protocol, m.key, model_label(m.name, m.protocol)) for m in MODELS.values()]
    entries.append((VP729_NAME, PROTOCOL_ASCII, VP729_KEY, model_label(VP729_NAME, PROTOCOL_ASCII)))
    entries.append((SP11D_NAME, PROTOCOL_SP11D, SP11D_KEY, model_label(SP11D_NAME, PROTOCOL_SP11D)))
    entries.sort(key=lambda e: (e[0].casefold(), e[1] == PROTOCOL_2BYTE))
    choices = [(key, label) for _, _, key, label in entries]
    choices.append((OTHER, OTHER_LABEL))
    return choices


def machine_max_for(model: str | None) -> int:
    """Return the highest machine number a model can be given."""
    key = resolve_model_key(model)
    return MODELS[key].machine_max if key else 15


def default_name_for(model: str | None) -> str:
    """Return the name pre-filled for a new device of this model (empty for "Other")."""
    if model == VP729_KEY:
        return VP729_NAME
    if model == SP11D_KEY:
        return SP11D_NAME
    key = resolve_model_key(model)
    return MODELS[key].name if key else ""


def dip8_address(dip8: str | None) -> int:
    """Return the SP-11D's machine address byte for the position of its DIP switch 8."""
    return 0x99 if dip8 == DIP8_ON else 0x98


def default_port_for(model: str | None) -> int:
    """Return the port a model normally listens on."""
    if model == VP729_KEY:
        return DEFAULT_VP729_PORT
    key = resolve_model_key(model)
    return MODELS[key].default_port if key else DEFAULT_PORT
