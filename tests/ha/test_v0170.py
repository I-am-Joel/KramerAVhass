"""Tests for 0.17.0: SP-11D, SIS-4x4, dynamic Mixed, default names, blank names and model notes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kramer.const import DOMAIN
from custom_components.kramer.models import MODELS, SP11D_NOTES

from ..sp11d_sim import Sp11dSim
from .sim_v016 import TwoByteSim
from .test_v016 import choose, ent, finish
from .test_v016 import setup as matrix_setup

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def sp_setup(hass: HomeAssistant, sim: Sp11dSim, dip8: str = "off") -> MockConfigEntry:
    """Set up an SP-11D against the simulated unit."""
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Processor",
        unique_id=f"127.0.0.1:{port}:sp",
        data={"ip_address": "127.0.0.1", "port": port, "model": "sp-11d", "model_name": "SP-11D", "dip8": dip8},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await entry.runtime_data.client.async_wait_ready(5)
    return entry


def sp_ent(hass, entry, domain, key):
    """Find an SP-11D entity id."""
    found = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert found, f"no {domain} {key}"
    return found


async def sp_done(hass, entry, sim):
    """Unload and stop."""
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


# ---- SP-11D ------------------------------------------------------------------------------------


async def test_sp11d_entities_and_values(hass: HomeAssistant) -> None:
    """Every kind of entity exists and shows what the unit reports, including a negative value."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    state = hass.states.get
    assert state(sp_ent(hass, entry, "select", "input_standard")).state == "PAL N"
    assert state(sp_ent(hass, entry, "select", "preset")).state == "Preset 1"
    assert state(sp_ent(hass, entry, "select", "output_format")).state == "RGBS"
    assert state(sp_ent(hass, entry, "number", "contrast")).state == "2"
    assert state(sp_ent(hass, entry, "number", "blue")).state == "-6"
    assert state(sp_ent(hass, entry, "switch", "panel_lock")).state == "off"
    assert state(sp_ent(hass, entry, "sensor", "firmware")).state == "2.0"
    assert state(sp_ent(hass, entry, "sensor", "model_code")).state == "1.2"
    assert state(sp_ent(hass, entry, "sensor", "detected_input_standard")).state == "PAL B"
    assert state(sp_ent(hass, entry, "binary_sensor", "connection")).state == "on"
    assert state(sp_ent(hass, entry, "number", "refresh_interval")) is not None
    for key in ("sch_up", "sch_down", "delay_up", "delay_down", "store_preset_1", "store_preset_16"):
        assert state(sp_ent(hass, entry, "button", key)).state != "unavailable", key
    assert hass.states.get(sp_ent(hass, entry, "button", "reset_defaults")) is None  # disabled by default
    reg = er.async_get(hass).async_get(sp_ent(hass, entry, "button", "reset_defaults"))
    assert reg.disabled_by is not None
    await sp_done(hass, entry, sim)


async def test_sp11d_writes_including_negative(hass: HomeAssistant) -> None:
    """A negative value is sent with bit 6 of the address byte set, and the unit's echo confirms it."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    await hass.services.async_call(
        "number", "set_value", {"entity_id": sp_ent(hass, entry, "number", "brightness"), "value": -37}, blocking=True
    )
    assert sim.live[6] == -37
    assert (33, 6, -37, 0xD8) in sim.log
    assert hass.states.get(sp_ent(hass, entry, "number", "brightness")).state == "-37"
    await choose(hass, sp_ent(hass, entry, "select", "input_standard"), "Auto")
    assert sim.live[1] == 6
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": sp_ent(hass, entry, "switch", "freeze")}, blocking=True
    )
    assert sim.live[26] == 1
    await sp_done(hass, entry, sim)


async def test_sp11d_dip8_on_uses_address_99(hass: HomeAssistant) -> None:
    """With DIP switch 8 on, the integration talks to address 99 (and a unit at 98 would not answer)."""
    sim = Sp11dSim(address=0x99)
    entry = await sp_setup(hass, sim, dip8="on")
    assert entry.runtime_data.client.available
    assert all(address & 0xBF == 0x99 for _, _, _, address in sim.log)
    await sp_done(hass, entry, sim)


async def test_sp11d_step_buttons(hass: HomeAssistant) -> None:
    """SCH and Delay move one step, and stop at the end of the range."""
    sim = Sp11dSim()
    sim.live[25] = 31
    entry = await sp_setup(hass, sim)
    await hass.services.async_call("button", "press", {"entity_id": sp_ent(hass, entry, "button", "delay_down")}, blocking=True)
    assert sim.live[25] == 30
    await hass.services.async_call("button", "press", {"entity_id": sp_ent(hass, entry, "button", "sch_up")}, blocking=True)
    assert sim.live[24] == 1
    sim.live[25] = 31
    entry.runtime_data.client.values[25] = 31
    with pytest.raises(HomeAssistantError, match="already at its limit"):
        await hass.services.async_call(
            "button", "press", {"entity_id": sp_ent(hass, entry, "button", "delay_up")}, blocking=True
        )
    await sp_done(hass, entry, sim)


async def test_sp11d_preset_recall_store_and_panel(hass: HomeAssistant) -> None:
    """Choosing a preset recalls it and re-reads everything; a Store to preset button stores where asked; the panel is followed."""
    sim = Sp11dSim()
    sim.presets[2][5] = 40
    entry = await sp_setup(hass, sim)
    client = entry.runtime_data.client
    await choose(hass, sp_ent(hass, entry, "select", "preset"), "Preset 3")
    await asyncio.sleep(0.5)
    assert sim.current == 2
    assert hass.states.get(sp_ent(hass, entry, "select", "preset")).state == "Preset 3"
    assert hass.states.get(sp_ent(hass, entry, "number", "contrast")).state == "40"
    # Change a value, store it into the preset in use, then recall another and back.
    await hass.services.async_call(
        "number", "set_value", {"entity_id": sp_ent(hass, entry, "number", "contrast"), "value": 55}, blocking=True
    )
    store_5 = sp_ent(hass, entry, "button", "store_preset_5")
    with pytest.raises(HomeAssistantError) as warning:
        await hass.services.async_call("button", "press", {"entity_id": store_5}, blocking=True)
    assert sim.presets[4][5] == 0  # the first press only warns
    assert str(warning.value) == (
        "This will overwrite whatever is stored in preset 5 with the device's current settings. "
        "This cannot be undone. Are you sure? Press the button again within 30 seconds to confirm."
    )
    assert hass.states.get(store_5).attributes["armed"] is True
    await hass.services.async_call("button", "press", {"entity_id": store_5}, blocking=True)
    assert hass.states.get(store_5).attributes["armed"] is False
    assert sim.presets[4][5] == 55 and sim.presets[2][5] == 40  # stored where asked, not in the preset in use
    assert any(instruction == 35 for instruction, *_ in sim.log)
    # A change on the front panel arrives as an announcement.
    sim.push(7, 12)
    await asyncio.sleep(0.2)
    assert client.value(7) == 12
    assert hass.states.get(sp_ent(hass, entry, "number", "video_gain")).state == "12"
    # A recall made on the panel is announced and everything is read again.
    sim.load(0)
    sim.announce_recall()
    await asyncio.sleep(0.6)
    assert hass.states.get(sp_ent(hass, entry, "select", "preset")).state == "Preset 1"
    assert hass.states.get(sp_ent(hass, entry, "number", "blue")).state == "-6"
    await sp_done(hass, entry, sim)


async def test_sp11d_output_standard_restart(hass: HomeAssistant) -> None:
    """Changing the output standard waits out the restart and then shows the unit's state."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    await choose(hass, sp_ent(hass, entry, "select", "output_standard"), "NTSC 4")
    await asyncio.sleep(0.5)
    assert sim.live[21] == 4
    assert hass.states.get(sp_ent(hass, entry, "select", "output_standard")).state == "NTSC 4"
    assert entry.runtime_data.client.available
    await sp_done(hass, entry, sim)


async def test_sp11d_reset_needs_two_presses(hass: HomeAssistant) -> None:
    """The first press only shows the warning; a second press within 30 seconds resets everything."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    eid = sp_ent(hass, entry, "button", "reset_defaults")
    registry = er.async_get(hass)
    registry.async_update_entity(eid, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert await entry.runtime_data.client.async_wait_ready(5)
    with pytest.raises(HomeAssistantError) as warning:
        await hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
    assert sim.presets[0][5] == 2  # nothing happened yet
    assert str(warning.value) == (
        "This action will reset every parameter on the device and clears all stored presets. "
        "This cannot be undone. Are you sure? Press the button again within 30 seconds to confirm."
    )
    await hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
    await asyncio.sleep(0.6)
    assert all(value == 0 for preset in sim.presets for value in preset.values())
    assert hass.states.get(sp_ent(hass, entry, "number", "contrast")).state == "0"
    await sp_done(hass, entry, sim)


async def test_sp11d_reset_second_press_too_late(hass: HomeAssistant) -> None:
    """A second press after the 30 seconds is treated as a first press again."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    eid = sp_ent(hass, entry, "button", "reset_defaults")
    er.async_get(hass).async_update_entity(eid, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert await entry.runtime_data.client.async_wait_ready(5)
    clock = [1000.0]
    with patch("custom_components.kramer.entity._now", lambda: clock[0]):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
        clock[0] = 1100.0
        with pytest.raises(HomeAssistantError):  # too late: this is a first press again
            await hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
    assert sim.presets[0][5] == 2
    await sp_done(hass, entry, sim)


async def test_sp11d_unavailable_when_silent(hass: HomeAssistant) -> None:
    """A unit that does not answer leaves entities unavailable without blocking start-up."""
    sim = Sp11dSim()
    sim.silent = True
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Processor",
        unique_id=f"127.0.0.1:{port}:sp",
        data={"ip_address": "127.0.0.1", "port": port, "model": "sp-11d", "model_name": "SP-11D", "dip8": "off"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.kramer.sp11d_protocol.Sp11dClient.async_wait_ready", return_value=False):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(sp_ent(hass, entry, "number", "contrast")).state == "unavailable"
    assert hass.states.get(sp_ent(hass, entry, "binary_sensor", "connection")).state == "off"
    await sp_done(hass, entry, sim)


async def test_sp11d_front_panel_entry_keeps_entities_available(hass: HomeAssistant) -> None:
    """While a panel button flashes the unit refuses commands: say why and keep the last values."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    client = entry.runtime_data.client
    contrast = sp_ent(hass, entry, "number", "contrast")
    sim.busy = True
    with pytest.raises(HomeAssistantError, match="front panel"):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": contrast, "value": 5}, blocking=True
        )
    assert client.busy
    assert hass.states.get(contrast).state == "2"  # still the last known value, not unavailable
    client.async_request_refresh()
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    assert hass.states.get(contrast).state == "2"
    assert hass.states.get(sp_ent(hass, entry, "binary_sensor", "connection")).state == "on"
    sim.busy = False
    await hass.services.async_call("number", "set_value", {"entity_id": contrast, "value": 5}, blocking=True)
    assert hass.states.get(contrast).state == "5"
    assert not client.busy
    await sp_done(hass, entry, sim)


# ---- config flow -------------------------------------------------------------------------------


def _translations() -> dict:
    """Return the integration's English strings."""
    return json.loads(Path("custom_components/kramer/translations/en.json").read_text())



async def to_connection(hass, model):
    """Start the flow and choose a model."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"model": model})


def defaults(result) -> dict:
    """Return the form's default values (UNDEFINED where there is none)."""
    return {str(k): k.default() if k.default is not vol.UNDEFINED else None for k in result["data_schema"].schema}


async def test_flow_sp11d(hass: HomeAssistant) -> None:
    """The SP-11D flow asks for the DIP switch 8 position (not a machine number) and probes the right address."""
    sim = Sp11dSim(address=0x99)
    port = await sim.start()
    result = await to_connection(hass, "sp-11d")
    values = defaults(result)
    assert "machine" not in values and values["dip8"] == "off" and values["name"] == "SP-11D" and values["port"] == 9001
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Processor", "ip_address": "127.0.0.1", "port": port, "dip8": "off"}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "no_reply_sp11d"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Processor", "ip_address": "127.0.0.1", "port": port, "dip8": "on"}
    )
    assert result["type"] == "create_entry", result
    assert result["data"]["model"] == "sp-11d" and result["data"]["dip8"] == "on" and "machine" not in result["data"]
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_flow_default_names(hass: HomeAssistant) -> None:
    """The name starts as the model's name, numbered if used; "Other" starts empty."""
    result = await to_connection(hass, "vs-5x4")
    assert defaults(result)["name"] == "VS-5x4"
    MockConfigEntry(domain=DOMAIN, title="VS-5x4", data={"ip_address": "x", "port": 1}).add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, title="VS-5x4 2", data={"ip_address": "y", "port": 2}).add_to_hass(hass)
    result = await to_connection(hass, "vs-5x4")
    assert defaults(result)["name"] == "VS-5x4 3"
    result = await to_connection(hass, "vp-729")
    assert defaults(result)["name"] == "VP-729"
    result = await to_connection(hass, "other")
    assert defaults(result)["name"] is None


async def test_flow_blank_name_refused(hass: HomeAssistant) -> None:
    """A blank or spaces-only name is refused with the approved wording, and the other values are kept."""
    result = await to_connection(hass, "vs-5x4")
    for blank in ("", "   "):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": blank, "ip_address": "127.0.0.1", "port": 9, "machine": 1}
        )
        assert result["type"] == "form" and result["errors"] == {"name": "name_required"}
    text = _translations()
    assert text["config"]["error"]["name_required"] == "Please enter a name for this device."


async def test_no_reply_wording() -> None:
    """The matrix no-reply message is the approved wording."""
    text = _translations()
    assert text["config"]["error"]["no_reply"] == (
        "Connected, but the device did not answer. Check the cable and that the protocol, model chosen, "
        "machine number and serial settings - including baud rate - match the device."
    )


# ---- SIS-4x4 -----------------------------------------------------------------------------------


async def test_sis_4x4(hass: HomeAssistant) -> None:
    """Routes, ALL, per-output OFF, all off, and panel announcements, with the SIS-4x4's own codes."""
    sim = TwoByteSim("sis")
    entry = await matrix_setup(hass, sim, "sis-4x4")
    c = entry.runtime_data.client
    assert c.routes == {1: 1, 2: 1, 3: 1, 4: 1}
    await choose(hass, ent(hass, entry, "output_3"), "Input 004")
    assert sim.routes[3] == 4
    assert sim.log[-1][1] & 0x1F == (4 - 1) * 4 + 3
    await c.async_set_route(0, 3)
    assert set(sim.routes.values()) == {3} and sim.log[-1][1] & 0x1F == 23
    await c.async_set_route(2, 0)
    assert sim.routes[2] == 0 and c.routes[2] == 0 and sim.log[-1][1] & 0x1F == 18
    await c.async_set_route(0, 0)
    assert set(sim.routes.values()) == {0} and sim.log[-1][1] & 0x1F == 25
    await c._refresh()
    assert set(c.routes.values()) == {0}
    sim.panel_route(2, 4)
    await asyncio.sleep(0.2)
    assert c.routes[2] == 4
    assert c.levels == {} or not entry.runtime_data.audio_levels
    await finish(hass, entry, sim)


# ---- Mixed -------------------------------------------------------------------------------------


async def test_mixed_only_while_outputs_differ(hass: HomeAssistant) -> None:
    """Mixed is listed in All outputs and the media player's sources only while the outputs differ."""
    sim = TwoByteSim("5x4")
    entry = await matrix_setup(hass, sim, "vs-5x4-2byte")
    c = entry.runtime_data.client
    all_outputs = ent(hass, entry, "all_outputs")
    player = er.async_get(hass).async_get_entity_id("media_player", DOMAIN, entry.entry_id)
    await c.async_set_route(0, 2)
    await hass.async_block_till_done()
    assert "Mixed" not in hass.states.get(all_outputs).attributes["options"]
    assert "Mixed" not in hass.states.get(player).attributes["source_list"]
    assert hass.states.get(all_outputs).state == "Input 002"
    await c.async_set_route(3, 4)
    await hass.async_block_till_done()
    assert "Mixed" in hass.states.get(all_outputs).attributes["options"]
    assert hass.states.get(all_outputs).state == "Mixed"
    assert "Mixed" in hass.states.get(player).attributes["source_list"]
    assert hass.states.get(player).attributes["source"] == "Mixed"
    # Choosing Mixed while mixed does nothing and gives no error.
    before = len(sim.log)
    await choose(hass, all_outputs, "Mixed")
    assert len(sim.log) == before
    await c.async_set_route(0, 5)
    await hass.async_block_till_done()
    assert "Mixed" not in hass.states.get(all_outputs).attributes["options"]
    await finish(hass, entry, sim)


# ---- model notes -------------------------------------------------------------------------------


def test_every_model_states_its_serial_settings() -> None:
    """Every model's notes give the serial settings, and none lists what the device cannot do."""
    for key, model in MODELS.items():
        assert "Serial settings" in model.notes, key
        assert "8 data bits, no parity, 1 stop bit" in model.notes, key
        assert "No presets" not in model.notes and "cannot be disconnected" not in model.notes, key
    assert "1200 baud" in MODELS["vs-1202yc"].notes
    assert "Tested as working with all DIP switches off." in MODELS["vs-808yc"].notes
    assert "Uses Protocol 2000. Serial settings:" in MODELS["vs-5x4"].notes
    assert "Uses the 2-byte protocol. Serial settings:" in MODELS["vs-5x4-2byte"].notes
    assert "Tested as working with DIP switches 1, 2 & 3 on and all others off." in MODELS["sis-4x4"].notes
    assert "Audio always follows video." in MODELS["sis-4x4"].notes
    assert "Changing the output standard restarts the device. This takes about 3 seconds." in SP11D_NOTES
    assert "Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit." in SP11D_NOTES


async def test_approved_sp11d_wording() -> None:
    """The SP-11D no-reply message and the reconfigure message are the approved wording."""
    errors = _translations()["config"]["error"]
    assert errors["no_reply_sp11d"] == (
        "Connected, but the device did not answer. Check the cable and that the model chosen and serial settings - "
        "including baud rate - match the device's configuration. Also check the DIP switches on the device are "
        "correctly set."
    )
    assert errors["type_change"] == (
        "You can only use Reconfigure to change a device for another of the same device type. To switch device type "
        "(for example between a matrix switcher, a presentation switcher and a video processor), add a new device instead."
    )


async def test_store_buttons_arm_separately(hass: HomeAssistant) -> None:
    """Arming one Store to preset button does not let another store; a late second press only warns again."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    five = sp_ent(hass, entry, "button", "store_preset_5")
    six = sp_ent(hass, entry, "button", "store_preset_6")
    clock = [1000.0]
    with patch("custom_components.kramer.entity._now", lambda: clock[0]):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call("button", "press", {"entity_id": five}, blocking=True)
        clock[0] = 1001.0
        with pytest.raises(HomeAssistantError):  # a different button is not armed by this one
            await hass.services.async_call("button", "press", {"entity_id": six}, blocking=True)
        clock[0] = 1100.0
        with pytest.raises(HomeAssistantError):  # too late for the first
            await hass.services.async_call("button", "press", {"entity_id": five}, blocking=True)
    assert not any(instruction == 35 for instruction, *_ in sim.log)
    await sp_done(hass, entry, sim)
