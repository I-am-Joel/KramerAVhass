"""Tests for the VP-729 client against the simulated unit (no Home Assistant needed)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.kramer.protocol import KramerCommandError, KramerConnectionError
from custom_components.kramer.vp729_defs import BY_FN, CONTROLS, label
from custom_components.kramer.vp729_protocol import (
    Vp729Client,
    async_probe_vp729,
    parse_lines,
    parse_value,
)

from .vp729_sim import FIRMWARE, Vp729Sim


@pytest.fixture
async def unit():
    sim = Vp729Sim()
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
async def client(unit):
    c = Vp729Client("127.0.0.1", unit.port, refresh_interval=60, reconnect_delay=0.2)
    await c.async_start()
    assert await c.async_wait_ready(10)
    yield c
    await c.async_stop()


def test_parse_lines_handles_prompt_and_crlf():
    buffer = bytearray(b"Y 1 76\r\nZ 1 76 VTB 1.26h    Audio 2.30\r\n>Z 0 36 1\r\n> par")
    assert parse_lines(buffer) == ["Y 1 76", "Z 1 76 VTB 1.26h    Audio 2.30", "Z 0 36 1"]
    assert bytes(buffer) == b"> par"  # an unfinished line stays until its end arrives


def test_parse_value():
    assert parse_value("-5") == -5
    assert parse_value(" 7 ") == 7
    assert parse_value("VTB 1.26h") == "VTB 1.26h"
    assert parse_value(None) is None
    assert parse_value("") is None


def test_control_table_is_consistent():
    fns = [c.fn for c in CONTROLS]
    assert len(fns) == len(set(fns))
    keys = [c.key for c in CONTROLS]
    assert len(keys) == len(set(keys))
    for control in CONTROLS:
        if control.kind in ("select", "switch"):
            assert control.options
        if control.kind == "number":
            assert control.minimum is not None and control.maximum is not None
    assert label(BY_FN[27], 5) == "Custom"
    assert label(BY_FN[27], 9) == "Unknown (9)"


async def test_probe_reads_firmware(unit):
    assert await async_probe_vp729("127.0.0.1", unit.port) == FIRMWARE


async def test_probe_refused():
    with pytest.raises(KramerConnectionError):
        await async_probe_vp729("127.0.0.1", 1, timeout=1)


async def test_refresh_reads_everything_and_marks_unavailable(client):
    assert client.firmware == FIRMWARE
    assert client.value(27) == 2
    assert client.function_available(25)
    # Needs aspect Custom, so the unit says ERR 3.
    assert not client.function_available(30)
    assert client.errors[30] == 3
    # Pip off: its sub-settings are unavailable but the source is readable.
    assert not client.function_available(37)
    assert client.function_available(38)


async def test_set_is_read_back_and_unlocks_dependants(client, unit):
    await client.async_set(27, 5)
    assert client.value(27) == 5
    await asyncio.sleep(0.5)  # dependants are read again in the background
    assert client.function_available(30)
    assert client.function_available(31)
    assert not client.function_available(28)  # needs H-zoom not zero
    await client.async_set(30, 2)
    await asyncio.sleep(0.5)
    assert client.function_available(28)
    assert not client.function_available(32)  # zoom is unavailable while aspect is Custom


async def test_set_without_value_is_not_used_for_settings(client, unit):
    await client.async_set(12, 55)
    assert "Y 0 12 55" in unit.log
    assert client.value(12) == 55


async def test_out_of_range_set_is_rejected_after_read_back(client):
    with pytest.raises(KramerCommandError):
        await client.async_set(12, 500)
    assert client.value(12) != 500


async def test_set_on_unavailable_function_raises(client):
    with pytest.raises(KramerCommandError) as err:
        await client.async_set(30, 1)
    assert "Needs Aspect ratio set to Custom" in str(err.value)


async def test_key_press_has_no_value(client, unit):
    await client.async_press(93)
    assert "Y 0 93" in unit.log
    assert client.value(93) is None


async def test_volume_up_does_not_become_the_level(client, unit):
    before = client.value(48)
    await client.async_press(48, 111)
    assert "Y 0 48 111" in unit.log
    assert client.value(48) == before


async def test_profile_buttons(client, unit):
    await client.async_press(72, 2)
    assert "Y 0 72 2" in unit.log


async def test_pushed_change_updates_state(client, unit):
    unit.push(12, 77)
    for _ in range(20):
        if client.value(12) == 77:
            break
        await asyncio.sleep(0.05)
    assert client.value(12) == 77


async def test_garbled_read_is_retried(client, unit):
    unit.garble_next = True
    assert await client.async_read(13) == unit.state[13]


async def test_listener_called_and_removed(client, unit):
    calls = []
    remove = client.add_listener(lambda: calls.append(1))
    await client.async_set(14, 33)
    assert calls
    remove()
    calls.clear()
    await client.async_set(14, 34)
    assert not calls


async def test_power_set_settles_and_standby_hides_everything_else(client, unit):
    reads_of_12 = unit.log.count("Y 1 12")
    await client.async_set(91, 0, settle=0.2)
    assert client.value(91) == 0
    await asyncio.sleep(1.0)
    assert client.standby
    assert not client.function_available(12)
    assert client.function_available(91)
    assert client.reason_unavailable(12) == "The unit is in standby"
    assert unit.log.count("Y 1 12") == reads_of_12  # in standby only the power function is read
    unit.push(91, 1)  # woken from the front panel or remote
    for _ in range(100):
        if client.function_available(12):
            break
        await asyncio.sleep(0.1)
    assert client.function_available(12)


async def test_reconnects_after_drop(client, unit):
    for writer in list(unit._writers):
        writer.close()
    for _ in range(100):
        await asyncio.sleep(0.1)
        if client.available and unit._writers:
            break
    assert client.available


async def test_set_is_confirmed_even_when_the_unit_is_slow_to_report_it(client, unit):
    unit.lag = 0.4  # the real unit takes up to ~0.3 s to show a value it has accepted
    await client.async_set(89, 1)
    assert client.value(89) == 1


async def test_refused_value_names_the_value_the_unit_still_reads(client, unit):
    with pytest.raises(KramerCommandError) as err:
        await client.async_set(12, 500)
    assert "still reads" in str(err.value)


async def test_garbled_set_is_sent_once_more(client, unit):
    unit.garble_next = True
    await client.async_set(13, 41)
    assert client.value(13) == 41
    assert unit.log.count("Y 0 13 41") == 2


async def test_commands_wait_for_the_prompt_so_none_are_garbled(client, unit):
    unit.min_gap = 0.02
    unit.too_fast = 0
    for value in range(1, 8):
        await client.async_set(59, value)
    assert client.value(59) == 7
    assert unit.too_fast == 0


async def test_fast_repeated_values_send_only_the_latest(client, unit):
    await asyncio.gather(client.async_set(12, 10), client.async_set(12, 20), client.async_set(12, 30))
    assert client.value(12) == 30
    sets = [line for line in unit.log if line.startswith("Y 0 12 ")]
    assert sets == ["Y 0 12 10", "Y 0 12 30"]


async def test_key_presses_are_never_merged(client, unit):
    await asyncio.gather(*(client.async_press(94) for _ in range(3)))
    assert unit.log.count("Y 0 94") == 3


async def test_profile_save_waits_for_its_slow_acknowledgement(client, unit):
    unit.save_delay = 0.5
    started = asyncio.get_running_loop().time()
    task = asyncio.create_task(client.async_press(72, 4, timeout=3.0, settle=0.1, busy=True))
    await asyncio.sleep(0.2)
    assert client.busy
    await task
    assert asyncio.get_running_loop().time() - started >= 0.5
    await asyncio.sleep(0.3)
    assert not client.busy


async def test_commands_are_held_back_while_a_recall_is_silent(client, unit):
    unit.recall_silence = 0.6
    await client.async_press(73, 4, settle=0.8, busy=True)
    await client.async_set(12, 55)  # must wait for the hold instead of being dropped
    assert client.value(12) == 55
    assert unit.dropped == []
