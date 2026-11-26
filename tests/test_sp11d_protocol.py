"""Tests for the SP-11D message encoding, with no Home Assistant involved."""

from __future__ import annotations

import pytest

from custom_components.kramer.sp11d_protocol import decode_value, frame, parse_frames


@pytest.mark.parametrize(
    ("value", "expected_last"),
    [(0, 0x98), (100, 0x98), (127, 0x98), (-1, 0xD8), (-6, 0xD8), (-100, 0xD8), (-128, 0xD8)],
)
def test_value_encoding_round_trip(value: int, expected_last: int) -> None:
    """A negative value is carried in bit 6 of the address byte, and decodes back exactly."""
    message = frame(33, 5, value, 0x98)
    assert message[3] == expected_last
    assert decode_value(message[2], message[3]) == value


def test_known_frames_from_the_real_unit() -> None:
    """Bytes seen on a real unit: -1 as FF/D8, -6 as FA/D8, +100 as E4/98."""
    assert frame(33, 5, -1, 0x98) == bytes([0x21, 0x85, 0xFF, 0xD8])
    assert frame(33, 14, -6, 0x98) == bytes([0x21, 0x8E, 0xFA, 0xD8])
    assert frame(33, 5, 100, 0x98) == bytes([0x21, 0x85, 0xE4, 0x98])
    assert frame(33, 5, -1, 0x99) == bytes([0x21, 0x85, 0xFF, 0xD9])


def test_parse_frames_resynchronises() -> None:
    """Stray bytes are skipped, complete messages are returned and a partial one is kept."""
    buffer = bytearray([0x85, 0x60, 0x9B, 0x80, 0x98, 0x61, 0x86, 0xFF, 0xD8, 0x62, 0x80])
    assert parse_frames(buffer) == [(0x60, 0x9B, 0x80, 0x98), (0x61, 0x86, 0xFF, 0xD8)]
    assert bytes(buffer) == bytes([0x62, 0x80])
    buffer.extend([0x83, 0x98])
    assert parse_frames(buffer) == [(0x62, 0x80, 0x83, 0x98)]
    assert not buffer
