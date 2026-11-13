"""Library of known Kramer matrix switchers.

Add a model by adding one entry here. Anything not listed can be set up with
the "Other" choice in the setup dialogue.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

OTHER = "other"
MAX_PORTS = 64
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


MODELS: dict[str, KramerModel] = {
    "vs-162av-rca": KramerModel("vs-162av-rca", "VS-162AV/RCA", 16, 16, True, preset_slots=60),
    "vs-812": KramerModel("vs-812", "VS-812", 8, 1, False, supports_off=False, supports_lock=False),
}

# Keys saved by earlier versions, mapped to their current entry.
LEGACY_KEYS = {"vs-162av": "vs-162av-rca", "vs-162avrca": "vs-162av-rca"}


def resolve_model_key(key: str | None) -> str | None:
    """Return the current library key for a saved key, or None if unknown."""
    key = LEGACY_KEYS.get(key or "", key)
    return key if key in MODELS else None


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
