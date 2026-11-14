"""Helpers for stored setups (presets) that do not need Home Assistant."""

from __future__ import annotations

from collections.abc import Mapping

MAX_DESCRIPTION_LENGTH = 60


def parse_descriptions(raw: Mapping[str, str] | None, slots: int) -> dict[int, str]:
    """Turn the saved {"7": "Film night"} option into {7: "Film night"}, dropping bad entries."""
    result: dict[int, str] = {}
    for key, value in (raw or {}).items():
        try:
            slot = int(key)
        except (TypeError, ValueError):
            continue
        text = str(value).strip()
        if 1 <= slot <= slots and text:
            result[slot] = text
    return result


def serialise_descriptions(descriptions: Mapping[int, str]) -> dict[str, str]:
    """Turn {7: "Film night"} into the form saved in the options."""
    return {str(slot): text for slot, text in sorted(descriptions.items())}


def validate_description(text: str, slot: int, descriptions: Mapping[int, str]) -> str | None:
    """Return an error key if the description cannot be used, else None."""
    text = text.strip()
    if not text:
        return "description_required"
    if len(text) > MAX_DESCRIPTION_LENGTH:
        return "description_too_long"
    if text.isdigit():
        return "description_numeric"
    folded = text.casefold()
    for other_slot, other in descriptions.items():
        if other_slot != slot and other.casefold() == folded:
            return "description_duplicate"
    return None


def resolve_slot(token: object, descriptions: Mapping[int, str], slots: int) -> int:
    """Turn a slot number or a description into a slot number, or raise ValueError."""
    if slots <= 0:
        raise ValueError("This model has no presets")
    text = str(token).strip()
    if text.isdigit():
        slot = int(text)
        if 1 <= slot <= slots:
            return slot
        raise ValueError(f"Preset {slot} is out of range (1 to {slots})")
    folded = text.casefold()
    for slot, description in descriptions.items():
        if description.casefold() == folded:
            return slot
    raise ValueError(f'No slot is described as "{text}"')


def slot_label(slot: int, occupied: bool | None, description: str | None) -> str:
    """Return the line shown for a slot in the preset picker."""
    state = "Occupied" if occupied else "Blank" if occupied is False else "Not checked"
    text = f"{slot:02d} - {state}"
    if description:
        text += f" - {description}"
    return text


def summarise_routes(routes: Mapping[int, int]) -> str:
    """Describe routes compactly, for example "outputs 1: in 2; 2: in 3; 3 to 16: in 1" (0 = off)."""
    if not routes:
        return "nothing read"
    outputs = sorted(routes)
    runs: list[tuple[int, int, int]] = []
    for output in outputs:
        value = routes[output]
        if runs and runs[-1][2] == value and runs[-1][1] == output - 1:
            runs[-1] = (runs[-1][0], output, value)
        else:
            runs.append((output, output, value))
    if len(runs) == 1:
        value = runs[0][2]
        return "every output off" if value == 0 else f"every output from input {value}"
    parts = []
    for first, last, value in runs:
        span = f"{first}" if first == last else f"{first} to {last}"
        parts.append(f"{span}: {'off' if value == 0 else f'in {value}'}")
    return "outputs " + "; ".join(parts)
