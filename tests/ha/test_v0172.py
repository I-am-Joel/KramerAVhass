"""Tests for 0.17.2: readable error messages, reads left out when never answered, and a unit that is off stays off."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.kramer.models import MODELS
from custom_components.kramer.protocol import (
    INSTR_REQUEST_BREAKAWAY,
    INSTR_REQUEST_VIDEO_STATUS,
    INSTR_SWITCH_AUDIO,
    INSTR_SWITCH_VIDEO,
    KramerClient,
    KramerCommandError,
    KramerTimeoutError,
)
from custom_components.kramer.sp11d_defs import PANEL_LOCK_FN
from custom_components.kramer.sp11d_protocol import Sp11dClient
from custom_components.kramer.twobyte import KramerTwoByteClient, TwoByteSpec
from custom_components.kramer.vp436_defs import BY_KEY
from custom_components.kramer.vp436_protocol import Vp436Client
from custom_components.kramer.vp729_defs import BY_FN
from custom_components.kramer.vp729_protocol import Vp729Client

from ..sp11d_sim import Sp11dSim
from ..vp436_sim import Vp436Sim
from ..vp729_sim import Vp729Sim
from .sim import Sim
from .sim_v016 import TwoByteSim

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def wait_for(check, seconds: float = 20.0) -> bool:
    """Wait until check() is true."""
    for _ in range(int(seconds / 0.05)):
        if check():
            return True
        await asyncio.sleep(0.05)
    return bool(check())


def matrix_client(port: int, **kw) -> KramerClient:
    """Build a Protocol 2000 client with short waits."""
    return KramerClient(
        "127.0.0.1",
        port,
        1,
        16,
        16,
        reconnect_delay=0.1,
        retry_interval=0.2,
        refresh_interval=0.3,
        request_timeout=0.15,
        **kw,
    )


# ---- readable messages -----------------------------------------------------------------------


async def test_protocol_2000_messages_say_what_was_being_done() -> None:
    """A write that gets no answer says which route it was setting."""
    sim = Sim()
    port = await sim.start()
    client = matrix_client(port, supports_modes=True)
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        sim.deaf = {(INSTR_SWITCH_VIDEO, 3), (INSTR_SWITCH_AUDIO, None)}
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_set_route(3, 5)
        assert str(err.value) == "No reply from the unit (setting the video of output 3 to input 5)"
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_set_audio_route(0, 0)
        assert str(err.value) == "No reply from the unit (disconnecting the audio of all outputs)"
        assert client.last_error == "No reply from the unit (disconnecting the audio of all outputs)"
    finally:
        await client.async_stop()
        await sim.stop()


async def test_two_byte_message_says_what_was_being_done() -> None:
    """The 2-byte protocol names the output and input in its error."""
    sim = TwoByteSim("1202")
    port = await sim.start()
    model = MODELS["vs-1202yc"]
    spec = TwoByteSpec(
        model.reply_id,
        model.inputs,
        model.outputs,
        model.has_all,
        model.supports_off,
        bool(model.audio_levels),
        model.off_command_base,
        model.off_all_command,
        model.off_status_base,
    )
    client = KramerTwoByteClient(
        "127.0.0.1", port, 1, spec, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=5, request_timeout=0.15
    )
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        sim.silent = True
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_set_route(2, 5)
        assert str(err.value) == "No reply from the unit (setting output 2 to input 5)"
    finally:
        await client.async_stop()
        await sim.stop()


async def test_vp436_messages_say_what_was_being_done() -> None:
    """VP-436 errors keep the command and add what it was for."""
    sim = Vp436Sim()
    port = await sim.start()
    client = Vp436Client("127.0.0.1", port, reconnect_delay=0.1, request_timeout=0.3)
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        control = BY_KEY["brightness"]
        sim.silent = True
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_set(control, 5)
        assert str(err.value) == f"No reply to Y {control.set_type} {control.fn} 5 (setting brightness to 5)"
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_press(20)
        assert str(err.value).endswith("(pressing Menu)")
    finally:
        await client.async_stop()
        await sim.stop()


async def test_sp11d_message_says_what_was_being_done() -> None:
    """SP-11D errors name the setting and its new value."""
    sim = Sp11dSim()
    port = await sim.start()
    client = Sp11dClient("127.0.0.1", port, reconnect_delay=0.1, request_timeout=0.2)
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        sim.silent = True
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_set(6, 40)
        assert str(err.value) == "No reply from the unit (setting brightness to 40)"
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_set(PANEL_LOCK_FN, 1)
        assert str(err.value) == "No reply from the unit (setting panel lock to on)"
    finally:
        await client.async_stop()
        await sim.stop()


async def test_vp729_message_says_what_was_being_done() -> None:
    """VP-729 errors name the function in words."""
    sim = Vp729Sim()
    port = await sim.start()
    client = Vp729Client("127.0.0.1", port, reconnect_delay=0.1, request_timeout=0.3)
    await client.async_start()
    try:
        assert await client.async_wait_ready(10)
        fn = next(f for f, c in BY_FN.items() if c.name == "Brightness")
        sim.deaf = {f"Y 0 {fn} 30"}
        with pytest.raises(KramerCommandError) as err:
            await client.async_set(fn, 30)  # no answer, so the read-back decides: it still shows the old value
        assert "(setting brightness to 30)" in str(err.value)
        sim.deaf = {f"Y 0 {fn} 30", f"Y 1 {fn}"}
        with pytest.raises(KramerTimeoutError) as err:
            await client.async_set(fn, 30)
        assert "(reading brightness)" in str(err.value)
    finally:
        await client.async_stop()
        await sim.stop()


# ---- a read the unit never answers -----------------------------------------------------------


async def test_protocol_2000_unanswered_read_is_left_out() -> None:
    """One output's route read that is never answered cannot keep the unit unavailable."""
    sim = Sim()
    sim.deaf = {(INSTR_REQUEST_VIDEO_STATUS, 5)}
    port = await sim.start()
    client = matrix_client(port, supports_modes=False)
    await client.async_start()
    try:
        assert await wait_for(lambda: client.available)
        assert "video:5" in client.quarantined
        assert 5 not in client.routes and client.routes[4] == 1 and client.routes[6] == 1
        await client.async_set_route(5, 7)  # still controllable
        assert sim.video[5] == 7
    finally:
        await client.async_stop()
        await sim.stop()


async def test_protocol_2000_silent_mode_read_keeps_the_unit() -> None:
    """A unit that answers the routes but never the mode read stays available."""
    sim = Sim()
    sim.deaf = {(INSTR_REQUEST_BREAKAWAY, None)}
    port = await sim.start()
    client = matrix_client(port, supports_modes=True)
    await client.async_start()
    try:
        assert await wait_for(lambda: client.available)
        assert client.routes[1] == 1
    finally:
        await client.async_stop()
        await sim.stop()


async def test_protocol_2000_unit_that_is_off_never_looks_available() -> None:
    """A unit behind a bridge that answers nothing must stay unavailable, however long it is left."""
    sim = Sim()
    sim.mute = True
    port = await sim.start()
    client = matrix_client(port, supports_modes=True)
    await client.async_start()
    try:
        await asyncio.sleep(4)
        assert not client.available
        assert not client.quarantined
        sim.mute = False  # switched on again
        assert await wait_for(lambda: client.available)
        assert len(client.routes) == 16
    finally:
        await client.async_stop()
        await sim.stop()


async def test_vp436_unanswered_read_is_left_out() -> None:
    """One setting that is never read does not keep a VP-436 unavailable, or make it look off."""
    sim = Vp436Sim()
    control = BY_KEY["sharpness"]
    sim.deaf = {f"Y {control.get_type} {control.fn}"}
    port = await sim.start()
    client = Vp436Client(
        "127.0.0.1", port, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=0.3, request_timeout=0.15
    )
    await client.async_start()
    try:
        assert await wait_for(lambda: "sharpness" in client.quarantined)
        assert client.available
        assert client.values.get("brightness") is not None
    finally:
        await client.async_stop()
        await sim.stop()


async def test_vp436_silent_power_read_does_not_lock_it_out() -> None:
    """The power read comes first: if only that is silent, the rest is still read."""
    sim = Vp436Sim()
    power = BY_KEY["power"]
    sim.deaf = {f"Y {power.get_type} {power.fn}"}
    port = await sim.start()
    client = Vp436Client(
        "127.0.0.1", port, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=0.3, request_timeout=0.15
    )
    await client.async_start()
    try:
        assert await wait_for(lambda: "power" in client.quarantined)
        assert client.available
    finally:
        await client.async_stop()
        await sim.stop()


async def test_vp436_unit_that_is_off_never_looks_available() -> None:
    """A silent device stays unavailable, with nothing left out."""
    sim = Vp436Sim()
    sim.silent = True
    port = await sim.start()
    client = Vp436Client(
        "127.0.0.1", port, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=0.3, request_timeout=0.15
    )
    await client.async_start()
    try:
        await asyncio.sleep(4)
        assert not client.available
        assert not client.quarantined
        sim.silent = False
        assert await wait_for(lambda: client.available)
    finally:
        await client.async_stop()
        await sim.stop()


async def test_sp11d_unanswered_read_is_left_out() -> None:
    """One parameter the SP-11D never answers does not stop the rest being read."""
    sim = Sp11dSim()
    sim.deaf = {(32, 3)}
    port = await sim.start()
    client = Sp11dClient(
        "127.0.0.1", port, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=0.3, request_timeout=0.1
    )
    await client.async_start()
    try:
        assert await wait_for(lambda: 3 in client.quarantined)
        assert client.available
        assert client.values.get(6) is not None
    finally:
        await client.async_stop()
        await sim.stop()


async def test_sp11d_unit_that_is_off_never_looks_available() -> None:
    """A silent SP-11D stays unavailable, with nothing left out."""
    sim = Sp11dSim()
    sim.silent = True
    port = await sim.start()
    client = Sp11dClient(
        "127.0.0.1", port, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=0.3, request_timeout=0.1
    )
    await client.async_start()
    try:
        await asyncio.sleep(3)
        assert not client.available
        assert not client.quarantined
        sim.silent = False
        assert await wait_for(lambda: client.available)
    finally:
        await client.async_stop()
        await sim.stop()


async def test_vp729_unanswered_read_is_left_out() -> None:
    """One function the VP-729 never answers does not keep it unavailable."""
    sim = Vp729Sim()
    fn = next(f for f, c in BY_FN.items() if c.name == "Brightness")
    sim.deaf = {f"Y 1 {fn}"}
    port = await sim.start()
    client = Vp729Client(
        "127.0.0.1", port, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=0.3, request_timeout=0.15
    )
    await client.async_start()
    try:
        assert await wait_for(lambda: fn in client.quarantined, 30)
        assert client.available
    finally:
        await client.async_stop()
        await sim.stop()


async def test_vp729_unit_that_is_off_never_looks_available() -> None:
    """A silent VP-729 stays unavailable, with nothing left out."""
    sim = Vp729Sim()
    sim.mute_until = 1e12
    port = await sim.start()
    client = Vp729Client(
        "127.0.0.1", port, reconnect_delay=0.1, retry_interval=0.2, refresh_interval=0.3, request_timeout=0.15
    )
    await client.async_start()
    try:
        await asyncio.sleep(4)
        assert not client.available
        assert not client.quarantined
    finally:
        await client.async_stop()
        await sim.stop()


# ---- diagnostics show what was left out and what the unit said instead ------------------------------


async def test_diagnostics_list_left_out_reads_and_unexpected_replies(hass) -> None:
    """Every model's diagnostics carry the two new lists, and show a left-out read."""
    from custom_components.kramer.diagnostics import async_get_config_entry_diagnostics  # noqa: PLC0415

    from .test_v016 import finish  # noqa: PLC0415
    from .test_v016 import setup as matrix_setup  # noqa: PLC0415
    from .test_v0170 import sp_done, sp_setup  # noqa: PLC0415
    from .test_v0171 import vp_setup  # noqa: PLC0415
    from .test_vp729 import setup as vp729_setup  # noqa: PLC0415

    sim = Sim()
    entry = await matrix_setup(hass, sim, "vs-162av-rca")
    client = entry.runtime_data.client
    client.quarantined.add("video:5")
    client.unmatched["reading the panel lock"] = "10 81 82 81"
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["state"]["left_out_reads"] == ["video:5"]
    assert result["state"]["unmatched_packets"] == {"reading the panel lock": "10 81 82 81"}
    await finish(hass, entry, sim)

    sp = Sp11dSim()
    entry = await sp_setup(hass, sp)
    entry.runtime_data.client.quarantined.add(6)
    entry.runtime_data.client.unmatched["reading brightness"] = "60 86 80 98"
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["state"]["left_out_reads"] == ["Brightness"]
    assert result["state"]["unexpected_replies"] == {"reading brightness": "60 86 80 98"}
    await sp_done(hass, entry, sp)

    vp = Vp436Sim()
    entry = await vp_setup(hass, vp)
    entry.runtime_data.client.quarantined.add("sharpness")
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["state"]["left_out_reads"] == ["sharpness"]
    assert result["state"]["unexpected_replies"] == {}
    await hass.config_entries.async_unload(entry.entry_id)
    await vp.stop()

    vp7 = Vp729Sim()
    entry = await vp729_setup(hass, vp7)
    entry.runtime_data.client.quarantined.add(12)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["state"]["left_out_reads"] == ["12"]
    assert result["state"]["unexpected_replies"] == {}
    await hass.config_entries.async_unload(entry.entry_id)
    await vp7.stop()


# ---- the repository checks (Hassfest and HACS) ----------------------------------------------------


def test_services_have_no_device_filter_on_their_targets() -> None:
    """Home Assistant no longer allows a device filter on a service target."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    import yaml  # noqa: PLC0415
    from homeassistant.helpers import selector  # noqa: PLC0415

    folder = Path(__file__).parents[2] / "custom_components" / "kramer"
    services = yaml.safe_load((folder / "services.yaml").read_text())
    assert set(services) == {"set_route", "store_preset", "recall_preset", "set_routing_mode"}
    for name, service in services.items():
        target = service.get("target") or {}
        assert "device" not in target, name
        selector.TargetSelector.CONFIG_SCHEMA(target)
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["categories"] == ["media_entertainment"]
    assert manifest["version"].startswith("0.17.")
