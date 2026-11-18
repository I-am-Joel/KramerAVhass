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
VP729_KEY = "vp-729"  # the VP-729 presentation scaler/switcher (not a matrix, so not in MODELS)
VP729_NAME = "VP-729"
MAX_PORTS = 127
MAX_PRESET_SLOTS = 99


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
    ),
    "vs-812": KramerModel("vs-812", "VS-812", 8, 1, False, supports_off=False, supports_lock=False),
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


def resolve_model_name(data: Mapping[str, Any]) -> str:
    """Return the model name to show for a config entry's data."""
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


def resolve_notes(data: Mapping[str, Any]) -> str:
    """Return the notes shipped with a model (empty for unknown models)."""
    key = resolve_model_key(data.get("model"))
    return MODELS[key].notes if key else ""


def model_label(name: str, protocol: str) -> str:
    """Return a model name with its protocol, for example "VS-808YC (Protocol 2000)"."""
    return f"{name} ({protocol})"


OTHER_LABEL = f"Other ({PROTOCOL_2000} switchers only)"


def model_choices() -> list[tuple[str, str]]:
    """Return (key, label) for the setup dropdown: alphabetical, with "Other" last."""
    choices = [(m.key, model_label(m.name, PROTOCOL_2000)) for m in MODELS.values()]
    choices.append((VP729_KEY, model_label(VP729_NAME, PROTOCOL_ASCII)))
    choices.sort(key=lambda item: item[1].casefold())
    choices.append((OTHER, OTHER_LABEL))
    return choices


def default_port_for(model: str | None) -> int:
    """Return the port a model normally listens on."""
    if model == VP729_KEY:
        return DEFAULT_VP729_PORT
    key = resolve_model_key(model)
    return MODELS[key].default_port if key else DEFAULT_PORT
