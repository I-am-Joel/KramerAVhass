"""Tests for the 0.15.2 changes to the VP-729 driver (limits table, reasons, rounding, standby, locks)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.kramer import vp729_defs as defs
from custom_components.kramer.vp729_defs import BY_FN, OUTPUT_RESOLUTIONS, limits, unavailable_reason
from custom_components.kramer.vp729_protocol import Vp729Client

from .vp729_sim import Vp729Sim


@pytest.fixture
async def unit():
    """Start a simulated VP-729."""
    sim = Vp729Sim()
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
async def client(unit):
    """Connect a client to the simulated VP-729."""
    c = Vp729Client("127.0.0.1", unit.port, refresh_interval=60, reconnect_delay=0.2)
    await c.async_start()
    assert await c.async_wait_ready(10)
    yield c
    await c.async_stop()


# ---- per-resolution limits (measured on the unit, all 43 resolutions) ---------------


def test_table_covers_every_output_resolution():
    """Table covers every output resolution."""
    assert set(defs.RESOLUTION_LIMITS) == set(OUTPUT_RESOLUTIONS)
    assert len(defs.RESOLUTION_LIMITS) == 43


@pytest.mark.parametrize(
    ("resolution", "zoom", "corner_h", "corner_v"),
    [
        (1, 32, 288, 216),  # 640x480
        (7, 32, 461, 346),  # 1024x768
        (13, 32, 576, 461),  # 1280x1024
        (20, 26, 720, 540),  # 1600x1200
        (22, 24, 756, 473),  # 1680x1050
        (23, 21, 864, 486),  # 1920x1080
        (26, 32, 324, 259),  # 576p
    ],
)
def test_limits_come_from_the_table(resolution, zoom, corner_h, corner_v):
    """Limits come from the table."""
    assert limits(BY_FN[33], resolution) == (0, zoom)
    for fn in (60, 64):  # positive corners
        assert limits(BY_FN[fn], resolution) == (0, corner_h)
    for fn in (62, 66):  # negative corners
        assert limits(BY_FN[fn], resolution) == (-corner_h, 0)
    assert limits(BY_FN[61], resolution) == (0, corner_v)
    assert limits(BY_FN[65], resolution) == (-corner_v, 0)
    assert limits(BY_FN[34], resolution) == (0, 32)  # zoom pan: the same at every resolution


def test_unknown_resolution_uses_the_documented_range():
    """Unknown resolution uses the documented range."""
    assert limits(BY_FN[60], None) == (0, 1000)
    assert limits(BY_FN[60], 55) == (0, 1000)
    assert limits(BY_FN[62], "Unknown (55)") == (-1000, 0)
    assert limits(BY_FN[33], None) == (0, 32)


def test_fixed_ranges_do_not_change_with_resolution():
    """Fixed ranges do not change with resolution."""
    for resolution in defs.RESOLUTION_LIMITS:
        assert limits(BY_FN[58], resolution) == (-40, 40)
        assert limits(BY_FN[59], resolution) == (-30, 30)
        assert limits(BY_FN[69], resolution) == (-20, 20)
        assert limits(BY_FN[70], resolution) == (-180, 180)


# ---- names -------------------------------------------------------------------------


def test_user_delay_is_renamed():
    """User delay is renamed."""
    assert BY_FN[54].name == "Audio delay (user defined mode)"
    assert BY_FN[53].name == "Audio delay mode"


# ---- reasons -----------------------------------------------------------------------


def test_picture_in_picture_reasons():
    """Picture in picture reasons."""
    on_pip = {36: 1, 37: 0, 39: 1, 44: 0}
    assert unavailable_reason(36, {32: 3}) == "Needs Zoom set to 100%"
    assert unavailable_reason(36, {32: 0}) is None
    assert unavailable_reason(40, {36: 0}) == "Needs Picture in picture on"
    assert unavailable_reason(42, on_pip) == "Needs PIP size set to Custom"
    assert unavailable_reason(43, on_pip) == "Needs PIP size set to Custom"
    assert unavailable_reason(45, on_pip) == "Needs PIP frame on"
    assert unavailable_reason(45, {**on_pip, 44: 1}) is None
    # The two other PIP types keep only the horizontal size.
    split = {36: 1, 37: 2, 39: 1, 44: 0}
    assert unavailable_reason(42, split) is None
    assert unavailable_reason(40, split) == "Needs PIP type set to Picture-in-picture"
    assert unavailable_reason(43, split).startswith("Needs PIP type set to Picture-in-picture")
    assert unavailable_reason(27, split) == "Needs Picture in picture off or PIP type set to Picture-in-picture"
    assert unavailable_reason(27, {**split, 37: 0}) is None
    assert unavailable_reason(32, {36: 1, 27: 0}) == "Needs Picture in picture off"


def test_reasons_by_source():
    """Reasons by source."""
    hdmi = {0: 6, 103: 36}
    assert unavailable_reason(6, hdmi) == "Not available with HDMI"
    assert unavailable_reason(18, hdmi) == "Not available with HDMI"
    assert unavailable_reason(5, hdmi) == "Not available for the selected input"  # offered on HDMI; nothing known to be missing
    composite = {0: 0, 1: 2, 103: 102}
    assert unavailable_reason(5, composite) == "Not available with composite video"
    assert unavailable_reason(7, composite) == "Not available with composite video"
    assert unavailable_reason(6, composite) == "Not available for the selected input"
    svideo = {0: 3, 4: 1}
    assert unavailable_reason(10, svideo) == "Not available with S-video"
    component = {0: 2, 3: 0}
    assert unavailable_reason(6, component) == "Not available with component video"
    # Inputs not checked with a signal (VGA, USB) get the general wording.
    assert unavailable_reason(7, {0: 4}) == "Not available for the selected input"


def test_picture_controls_need_a_signal():
    """Picture controls need a signal."""
    no_signal = {0: 1, 1: 2, 103: 98}
    assert unavailable_reason(12, no_signal) == "Needs a signal on the selected input"
    assert unavailable_reason(27, no_signal) == "Needs a signal on the selected input"
    assert unavailable_reason(74, no_signal) == "Needs a signal on the selected input"
    assert unavailable_reason(12, {0: 1, 103: 102}) is None
    # The "none detected" reading must not hide a reason that is more specific.
    assert unavailable_reason(5, {0: 6, 103: 98}) == "Not available for the selected input"


def test_source_and_type_changes_reread_the_input_controls():
    """Source and type changes reread the input controls."""
    for fn in (5, 6, 7, 8, 10, 12, 18, 24, 46, 74):
        assert 0 in BY_FN[fn].depends_on and 1 in BY_FN[fn].depends_on
    for fn in (36,):
        assert 32 in BY_FN[fn].depends_on
    assert 36 in BY_FN[27].depends_on and 37 in BY_FN[27].depends_on
    assert 44 in BY_FN[45].depends_on


# ---- rounding of the user delay ------------------------------------------------------


async def test_odd_user_delay_is_rounded_up_by_the_integration(client, unit):
    """Odd user delay is rounded up by the integration."""
    from custom_components.kramer.vp729_entity import Vp729Number  # noqa: PLC0415

    await client.async_set(53, 1)  # user defined mode
    unit.state[53] = 1
    control = BY_FN[54]
    number = object.__new__(Vp729Number)
    number._control = control
    number._client = client
    number._call = lambda coro: coro  # the entity helper only awaits the command
    await Vp729Number.async_set_native_value(number, 7)
    assert client.value(54) == 8
    await Vp729Number.async_set_native_value(number, 340)
    assert client.value(54) == 340
    await Vp729Number.async_set_native_value(number, 339)
    assert client.value(54) == 340  # rounded up, and never beyond the maximum


# ---- locks are read again after waking from standby --------------------------------


async def test_locks_are_read_again_after_waking(client, unit, monkeypatch):
    """Locks are read again after waking."""
    monkeypatch.setattr("custom_components.kramer.vp729_protocol.LOCK_RECHECK_SECONDS", 0.3)
    await client.async_set(91, 0, settle=0.1)
    await asyncio.sleep(1.0)
    assert client.standby
    unit.state[102] = 1  # the unit reports a lock after waking
    unit.push(91, 1)
    for _ in range(100):
        if client.function_available(12):
            break
        await asyncio.sleep(0.1)
    reads_before = unit.log.count("Y 1 102")
    for _ in range(100):
        if unit.log.count("Y 1 102") > reads_before:
            break
        await asyncio.sleep(0.1)
    assert unit.log.count("Y 1 102") > reads_before
    assert client.value(102) == 1
    assert client.value(85) is not None
