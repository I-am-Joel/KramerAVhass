"""Library of known Kramer matrix switchers.

Add a model by adding one entry here. Anything not listed can be set up with
the "Other" choice in the setup dialogue.
"""

from __future__ import annotations

from dataclasses import dataclass

OTHER = "other"
MAX_PORTS = 64


@dataclass(frozen=True)
class KramerModel:
    """Description of one switcher model."""

    key: str
    name: str
    inputs: int
    outputs: int
    supports_modes: bool


MODELS: dict[str, KramerModel] = {
    "vs-162av": KramerModel("vs-162av", "VS-162AV", 16, 16, True),
    "vs-162avrca": KramerModel("vs-162avrca", "VS-162AVRCA", 16, 16, True),
}
