"""Tests for 0.17.1: VP-436/437XL, VS-808XL, SP-11D busy sensor, forced panel lock and the double-press buttons."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kramer.const import DOMAIN
from custom_components.kramer.models import (
    MODELS,
    VP436_NOTES,
    default_name_for,
    model_choices,
    resolve_notes,
)

from ..sp11d_sim import Sp11dSim
from ..vp436_sim import Vp436Sim
from .sim import Sim
from .test_v016 import choose, ent, finish
from .test_v016 import setup as matrix_setup
from .test_v0170 import sp_done, sp_ent, sp_setup

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def wait_for(check, seconds: float = 5.0) -> None:
    """Wait until check() is true."""
    for _ in range(int(seconds / 0.05)):
        if check():
            return
        await asyncio.sleep(0.05)
    assert check()


# ---- VP-436/437XL ------------------------------------------------------------------------------


async def vp_setup(hass: HomeAssistant, sim: Vp436Sim) -> MockConfigEntry:
    """Set up a VP-436/437XL against the simulated device."""
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Presenter",
        unique_id=f"127.0.0.1:{port}:vp436",
        data={"ip_address": "127.0.0.1", "port": port, "model": "vp-436", "model_name": "VP-436/437XL"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await entry.runtime_data.client.async_wait_ready(10)
    await hass.async_block_till_done()
    return entry


def vp_ent(hass, entry, domain, key):
    """Find a VP-436 entity id."""
    found = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert found, f"no {domain} {key}"
    return found


async def vp_done(hass, entry, sim):
    """Unload and stop."""
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


def test_model_listing_and_notes() -> None:
    """The model is listed as VP-436/437XL (ASCII) and its notes carry the approved wording."""
    assert ("vp-436", "VP-436/437XL (ASCII)") in model_choices()
    assert default_name_for("vp-436") == "VP-436/437XL"
    assert ("vs-808xl", "VS-808XL (Protocol 2000)") in model_choices()
    assert resolve_notes({"model": "vp-436"}) == VP436_NOTES
    for line in (
        "- Serial settings: 9600 baud, 8 data bits, no parity, 1 stop bit, no flow control. A null-modem adapter is required.",
        "- Tested as working on a VP-436 (firmware V6.24) connected by serial through a bridge. "
        "The VP-437XL has the same software and adds an Ethernet port, which has not been tested.",
        "- Input and output HDCP controls appear when the firmware supports them (V6.27 and V6.29).",
        "- Factory reset restores every setting and takes about 8 seconds.",
        "- Auto sync off is untested.",
    ):
        assert line in VP436_NOTES
    xl = MODELS["vs-808xl"]
    assert xl.notes.startswith("**Model-specific limits:**\n\n- 8 inputs, 8 outputs\n- Independent audio and video")
    assert "- 8 stored presets\n- Highest machine address 8\n" in xl.notes
    assert xl.preset_slots == 8 and xl.supports_lock is False and xl.flash_busy


async def test_vp436_entities_and_values(hass: HomeAssistant) -> None:
    """Every kind of entity is created and shows the device's values."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    state = hass.states.get
    assert state(vp_ent(hass, entry, "select", "input")).state == "HDMI 1"
    assert state(vp_ent(hass, entry, "select", "resolution")).state == "Native"
    assert state(vp_ent(hass, entry, "select", "size")).state == "Full"
    assert state(vp_ent(hass, entry, "select", "noise_reduction")).state == "Off"
    assert state(vp_ent(hass, entry, "switch", "power")).state == "on"
    assert state(vp_ent(hass, entry, "switch", "panel_lock")).state == "off"
    assert state(vp_ent(hass, entry, "switch", "auto_sync_off")).state == "off"
    assert state(vp_ent(hass, entry, "number", "osd_timeout")).state == "50"
    assert state(vp_ent(hass, entry, "binary_sensor", "signal_detected")).state == "on"
    assert state(vp_ent(hass, entry, "sensor", "firmware")).state == "V6.24"
    assert state(vp_ent(hass, entry, "sensor", "status")).state == "online"
    assert state(vp_ent(hass, entry, "button", "menu")).state != "unavailable"
    assert state(vp_ent(hass, entry, "button", "auto_adjust")) is not None
    assert state(vp_ent(hass, entry, "number", "refresh_interval")) is not None
    status = state(vp_ent(hass, entry, "sensor", "status")).attributes
    assert status["device_type"] == "presentation_scaler" and status["standby"] is False
    assert status["settings"]["input"] == "HDMI 1" and status["settings"]["signal_detected"] is True
    # The HDCP controls are only created where the firmware answers.
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_input_hdcp") is None
    # The factory reset button starts disabled.
    reset = registry.async_get(vp_ent(hass, entry, "button", "factory_reset"))
    assert reset.disabled_by is not None
    await vp_done(hass, entry, sim)


async def test_vp436_hdcp_appears_when_the_firmware_has_it(hass: HomeAssistant) -> None:
    """Input and output HDCP exist only on firmware that answers for them."""
    sim = Vp436Sim(version="V6.29", hdcp=True)
    entry = await vp_setup(hass, sim)
    assert hass.states.get(vp_ent(hass, entry, "switch", "input_hdcp")).state == "off"
    assert hass.states.get(vp_ent(hass, entry, "switch", "output_hdcp")).state == "off"
    assert hass.states.get(vp_ent(hass, entry, "sensor", "firmware")).state == "V6.29"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": vp_ent(hass, entry, "switch", "output_hdcp")}, blocking=True
    )
    assert sim.state["output_hdcp"] == 1
    await vp_done(hass, entry, sim)


async def test_vp436_writes_and_per_input_settings(hass: HomeAssistant) -> None:
    """Changing the input re-reads the picture settings kept for the new input."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    brightness = vp_ent(hass, entry, "number", "brightness")
    before = hass.states.get(brightness).state
    await choose(hass, vp_ent(hass, entry, "select", "input"), "Component 1")
    assert sim.state["input"] == 3
    await wait_for(lambda: hass.states.get(brightness).state == str(sim.per_input[3]["brightness"]))
    assert hass.states.get(brightness).state != before
    await choose(hass, vp_ent(hass, entry, "select", "resolution"), "1080p at 60 Hz")
    assert sim.state["resolution"] == 10
    await choose(hass, vp_ent(hass, entry, "select", "size"), "Underscan 2 (9%)")
    assert sim.state["size"] == 5
    await choose(hass, vp_ent(hass, entry, "select", "audio_delay"), "110 ms")
    assert sim.state["audio_delay"] == 2
    await hass.services.async_call("number", "set_value", {"entity_id": brightness, "value": 77}, blocking=True)
    assert sim.state["brightness"] == 77
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": vp_ent(hass, entry, "switch", "mute")}, blocking=True
    )
    assert sim.state["mute"] == 1
    assert "Y 3 21 10" in sim.log and "Y 1 51 2" in sim.log
    await vp_done(hass, entry, sim)


async def test_vp436_announcements(hass: HomeAssistant) -> None:
    """Changes made on the device's own buttons are announced and shown."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    sim.push(6, 1, 1)  # the freeze button
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "switch", "freeze")).state == "on")
    sim.push(6, 3, 1)  # the mute button
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "switch", "mute")).state == "on")
    sim.push(3, 0, 2)  # an input button
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "select", "input")).state == "S-video (YC)")
    await wait_for(
        lambda: hass.states.get(vp_ent(hass, entry, "number", "brightness")).state
        == str(sim.per_input[2]["brightness"])
    )
    await vp_done(hass, entry, sim)


async def test_vp436_standby(hass: HomeAssistant) -> None:
    """In standby input, freeze, Panel lock and the keys are unavailable; levels and options still work."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    power = vp_ent(hass, entry, "switch", "power")
    await hass.services.async_call("switch", "turn_off", {"entity_id": power}, blocking=True)
    assert sim.state["power"] == 0
    for domain, key in (
        ("select", "input"),
        ("switch", "freeze"),
        ("switch", "panel_lock"),
        ("button", "menu"),
        ("button", "reboot"),
    ):
        await wait_for(lambda d=domain, k=key: hass.states.get(vp_ent(hass, entry, d, k)).state == "unavailable")
    for domain, key in (
        ("switch", "power"),
        ("switch", "blank"),
        ("switch", "mute"),
        ("select", "resolution"),
        ("select", "size"),
        ("number", "brightness"),
    ):
        assert hass.states.get(vp_ent(hass, entry, domain, key)).state != "unavailable"
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "sensor", "status")).attributes["standby"] is True)
    # A level can still be changed in standby.
    await hass.services.async_call(
        "number", "set_value", {"entity_id": vp_ent(hass, entry, "number", "contrast"), "value": 33}, blocking=True
    )
    assert sim.state["contrast"] == 33
    # Wake: everything is available again and the freeze and lock are read again.
    await hass.services.async_call("switch", "turn_on", {"entity_id": power}, blocking=True)
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "select", "input")).state != "unavailable")
    assert hass.states.get(vp_ent(hass, entry, "switch", "freeze")).state == "off"
    await vp_done(hass, entry, sim)


async def test_vp436_wake_announcement_refreshes(hass: HomeAssistant) -> None:
    """A device that wakes on its own (the remote's power key) announces it and is read again."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    sim.state["power"] = 0
    sim.state["freeze"] = 1
    entry.runtime_data.client.async_request_refresh()
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "switch", "power")).state == "off")
    sim.state["power"] = 1
    sim.state["freeze"] = 0
    sim.say("\r\nZ 6 0 1\r\n")
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "switch", "power")).state == "on")
    await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "switch", "freeze")).state == "off")
    await vp_done(hass, entry, sim)


async def test_vp436_signal_sensor_is_polled(hass: HomeAssistant) -> None:
    """The signal sensor follows the device at the next read (the device does not announce it)."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    sensor = vp_ent(hass, entry, "binary_sensor", "signal_detected")
    sim.signal = False
    entry.runtime_data.client.async_request_refresh()
    await wait_for(lambda: hass.states.get(sensor).state == "off")
    sim.signal = True
    entry.runtime_data.client.async_request_refresh()
    await wait_for(lambda: hass.states.get(sensor).state == "on")
    await vp_done(hass, entry, sim)


async def test_vp436_keys_and_volume(hass: HomeAssistant) -> None:
    """Key buttons send the key; the volume keys are followed by a read of the volume."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    await hass.services.async_call(
        "button", "press", {"entity_id": vp_ent(hass, entry, "button", "menu")}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": vp_ent(hass, entry, "button", "volume_up")}, blocking=True
    )
    assert sim.keys == [20, 37]
    volume = vp_ent(hass, entry, "number", "output_volume")
    await wait_for(lambda: hass.states.get(volume).state == str(sim.state["output_volume"]))
    assert sim.state["output_volume"] == 51
    await vp_done(hass, entry, sim)


async def test_vp436_reboot_and_factory_reset(hass: HomeAssistant) -> None:
    """Reboot sends power 2; the factory reset needs two presses and then restores the defaults."""
    sim = Vp436Sim()
    with (
        patch("custom_components.kramer.vp436_protocol.REBOOT_SETTLE_SECONDS", 0.1),
        patch("custom_components.kramer.vp436_protocol.FACTORY_RESET_SETTLE_SECONDS", 0.1),
    ):
        entry = await vp_setup(hass, sim)
        await hass.services.async_call(
            "button", "press", {"entity_id": vp_ent(hass, entry, "button", "reboot")}, blocking=True
        )
        assert "Y 6 0 2" in sim.log
        eid = vp_ent(hass, entry, "button", "factory_reset")
        er.async_get(hass).async_update_entity(eid, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert await entry.runtime_data.client.async_wait_ready(10)
        sim.state["brightness"] = 99
        with pytest.raises(HomeAssistantError) as warning:
            await hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
        assert str(warning.value) == (
            "This will restore every setting on the device to its factory default. This cannot be undone. "
            "Are you sure? Press the button again within 30 seconds to confirm."
        )
        assert sim.state["brightness"] == 99
        await hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
        assert "Y 3 23 1" in sim.log
        await wait_for(lambda: hass.states.get(vp_ent(hass, entry, "number", "brightness")).state != "99")
        assert sim.state["brightness"] != 99
        await vp_done(hass, entry, sim)


async def test_vp436_unavailable_when_silent(hass: HomeAssistant) -> None:
    """A device that does not answer leaves its entities unavailable without blocking start-up."""
    sim = Vp436Sim()
    sim.silent = True
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Presenter",
        unique_id=f"127.0.0.1:{port}:vp436",
        data={"ip_address": "127.0.0.1", "port": port, "model": "vp-436", "model_name": "VP-436/437XL"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.kramer.vp436_protocol.Vp436Client.async_wait_ready", return_value=False):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(vp_ent(hass, entry, "number", "brightness")).state == "unavailable"
    assert hass.states.get(vp_ent(hass, entry, "sensor", "status")).state == "offline"
    assert hass.states.get(vp_ent(hass, entry, "binary_sensor", "connection")).state == "off"
    await vp_done(hass, entry, sim)


async def test_vp436_config_flow(hass: HomeAssistant) -> None:
    """The model is chosen first, the connection screen has no machine number, and the device is probed."""
    sim = Vp436Sim()
    port = await sim.start()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"model": "vp-436"})
    assert result["step_id"] == "connection"
    fields = {str(k): k.default() if k.default is not vol.UNDEFINED else None for k in result["data_schema"].schema}
    assert fields["name"] == "VP-436/437XL" and fields["port"] == 9001
    assert "machine" not in fields and "dip8" not in fields
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Presenter", "ip_address": "127.0.0.1", "port": port}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["model"] == "vp-436" and result["data"]["model_name"] == "VP-436/437XL"
    await sim.stop()


async def test_vp436_config_flow_no_reply(hass: HomeAssistant) -> None:
    """Connected but silent shows the no-reply message."""
    sim = Vp436Sim()
    sim.silent = True
    port = await sim.start()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"model": "vp-436"})
    with patch("custom_components.kramer.vp436_protocol.async_probe_vp436.__defaults__", (0.3,)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "Presenter", "ip_address": "127.0.0.1", "port": port}
        )
    assert result["errors"] == {"base": "no_reply_vp436"}
    await sim.stop()


async def test_vp436_options_show_notes(hass: HomeAssistant) -> None:
    """The configuration page shows the model's notes."""
    sim = Vp436Sim()
    entry = await vp_setup(hass, sim)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init_notes"
    assert "Auto sync off is untested." in result["description_placeholders"]["notes"]
    await vp_done(hass, entry, sim)


# ---- VS-808XL ----------------------------------------------------------------------------------


async def test_vs808xl_front_panel_busy(hass: HomeAssistant) -> None:
    """While a button flashes the unit answers error 3: say why, keep the values, and recover on its own."""
    sim = Sim(outputs=8, lock=False)
    with patch("custom_components.kramer.protocol.BUSY_INTERVAL", 0.2):
        entry = await matrix_setup(hass, sim, "vs-808xl")
        client = entry.runtime_data.client
        busy = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_front_panel_busy")
        assert busy and hass.states.get(busy).state == "off"
        output = ent(hass, entry, "output_1")
        before = hass.states.get(output).state
        sim.busy = True
        with pytest.raises(HomeAssistantError, match="front panel"):
            await choose(hass, output, "Input 002")
        assert client.busy and hass.states.get(busy).state == "on"
        assert hass.states.get(output).state == before  # the last value is kept, not unavailable
        assert client.available
        sim.busy = False
        await wait_for(lambda: hass.states.get(busy).state == "off")
        await choose(hass, output, "Input 002")
        assert sim.video[1] == 2
        await finish(hass, entry, sim)


async def test_other_models_have_no_busy_sensor(hass: HomeAssistant) -> None:
    """The sensor is only offered on models that need it."""
    sim = Sim(outputs=8, lock=False)
    entry = await matrix_setup(hass, sim, "vs-808yc")
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_front_panel_busy") is None
    await finish(hass, entry, sim)


# ---- SP-11D ------------------------------------------------------------------------------------


async def test_sp11d_busy_sensor_and_recovery(hass: HomeAssistant) -> None:
    """The Front panel busy sensor follows the flashing button and clears by itself."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    client = entry.runtime_data.client
    client._busy_interval = 0.2
    sensor = sp_ent(hass, entry, "binary_sensor", "front_panel_busy")
    assert hass.states.get(sensor).state == "off"
    assert hass.states.get(sensor).name == "Processor Front panel busy (indicated by a flashing button)"
    sim.busy = True
    client.async_request_refresh()
    await wait_for(lambda: hass.states.get(sensor).state == "on")
    assert hass.states.get(sp_ent(hass, entry, "number", "contrast")).state == "2"
    sim.busy = False
    await wait_for(lambda: hass.states.get(sensor).state == "off", 6)
    await sp_done(hass, entry, sim)


async def test_sp11d_last_store_sensor(hass: HomeAssistant) -> None:
    """The sensor shows the preset the device last acknowledged storing, with the time."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    sensor = sp_ent(hass, entry, "sensor", "last_stored_preset")
    assert hass.states.get(sensor).state == "unknown"
    store = sp_ent(hass, entry, "button", "store_preset_16")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": store}, blocking=True)
    assert hass.states.get(sensor).state == "unknown"  # a first press records nothing
    await hass.services.async_call("button", "press", {"entity_id": store}, blocking=True)
    assert hass.states.get(sensor).state == "Preset 16"
    assert hass.states.get(sensor).attributes["time"] is not None
    assert hass.states.get(sensor).name == "Processor Double pressed preset (last recorded action)"
    await sp_done(hass, entry, sim)


async def test_sp11d_button_names_and_armed(hass: HomeAssistant) -> None:
    """The buttons are named for what they do and show whether they are armed."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    store = sp_ent(hass, entry, "button", "store_preset_3")
    assert hass.states.get(store).name == "Processor Double press to store to preset 3"
    assert hass.states.get(store).attributes["armed"] is False
    registry = er.async_get(hass)
    reset = registry.async_get(sp_ent(hass, entry, "button", "reset_defaults"))
    assert reset.original_name == "Double press to reset all defaults"
    await sp_done(hass, entry, sim)


async def test_sp11d_forced_lock(hass: HomeAssistant) -> None:
    """Panel lock (forced) locks the panel at connect and at every refresh, and takes over the Panel lock switch."""
    sim = Sp11dSim()
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Processor",
        unique_id=f"127.0.0.1:{port}:sp",
        data={
            "ip_address": "127.0.0.1",
            "port": port,
            "model": "sp-11d",
            "model_name": "SP-11D",
            "dip8": "off",
            "force_lock": True,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = entry.runtime_data.client
    assert await client.async_wait_ready(5)
    assert sim.live[23] == 1  # locked when it connected
    forced = sp_ent(hass, entry, "switch", "panel_lock_forced")
    lock = sp_ent(hass, entry, "switch", "panel_lock")
    assert hass.states.get(forced).state == "on"
    assert hass.states.get(forced).name == "Processor Panel lock (forced)"
    assert hass.states.get(forced).attributes["panel_lock"] == (
        'Unavailable because "Panel lock (forced)" is on. Turn that off first.'
    )
    assert hass.states.get(lock).state == "unavailable"
    # Released on the device: locked again at the next refresh.
    sim.live[23] = 0
    client.async_request_refresh()
    await wait_for(lambda: sim.live[23] == 1)
    # Turn it off: the panel is left as it is, and Panel lock works again; the setting is saved.
    await hass.services.async_call("switch", "turn_off", {"entity_id": forced}, blocking=True)
    assert entry.data["force_lock"] is False
    await wait_for(lambda: hass.states.get(lock).state == "on")
    assert sim.live[23] == 1
    await hass.services.async_call("switch", "turn_off", {"entity_id": lock}, blocking=True)
    assert sim.live[23] == 0
    sim.live[23] = 0
    client.async_request_refresh()
    await asyncio.sleep(0.4)
    assert sim.live[23] == 0  # not locked again now it is off
    await hass.services.async_call("switch", "turn_on", {"entity_id": forced}, blocking=True)
    assert entry.data["force_lock"] is True
    await wait_for(lambda: sim.live[23] == 1)
    await sp_done(hass, entry, sim)


async def test_sp11d_forced_lock_off_by_default(hass: HomeAssistant) -> None:
    """Without the setting nothing is locked and Panel lock is usable."""
    sim = Sp11dSim()
    entry = await sp_setup(hass, sim)
    assert sim.live[23] == 0
    assert hass.states.get(sp_ent(hass, entry, "switch", "panel_lock_forced")).state == "off"
    assert hass.states.get(sp_ent(hass, entry, "switch", "panel_lock")).state == "off"
    assert hass.states.get(sp_ent(hass, entry, "switch", "panel_lock_forced")).attributes.get("panel_lock") is None
    await sp_done(hass, entry, sim)


async def test_sp11d_flow_offers_forced_lock(hass: HomeAssistant) -> None:
    """The connection screen has the Panel lock (forced) option, off by default, and saves it."""
    sim = Sp11dSim()
    port = await sim.start()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"model": "sp-11d"})
    fields = {str(k): k.default() if k.default is not vol.UNDEFINED else None for k in result["data_schema"].schema}
    assert fields["force_lock"] is False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Processor", "ip_address": "127.0.0.1", "port": port, "dip8": "off", "force_lock": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["force_lock"] is True
    await sim.stop()
