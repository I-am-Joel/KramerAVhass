"""Home Assistant level tests against the simulated unit."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kramer.const import DOMAIN

from .sim import Sim

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


def make_entry(port: int, model: str = "vs-162av-rca", **extra) -> MockConfigEntry:
    """Make entry."""
    data = {
        "ip_address": "127.0.0.1",
        "port": port,
        "machine": 1,
        "model": model,
        "model_name": "VS-162AV/RCA",
        "inputs": 16,
        "outputs": 16,
        "supports_modes": True,
        **extra,
    }
    return MockConfigEntry(
        domain=DOMAIN, data=data, title="Test Matrix", unique_id=f"127.0.0.1:{port}:1"
    )


async def setup(hass: HomeAssistant, sim: Sim, **kw):
    """Set up the integration against the simulated unit."""
    port = await sim.start()
    entry = make_entry(port, **kw)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def device_id(hass, entry) -> str:
    """Device id."""
    return (
        dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)}).id
    )


def state_of(hass, entry, domain: str, key: str):
    """State of."""
    reg = er.async_get(hass)
    entity_id = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id, f"no {domain} {key}"
    return hass.states.get(entity_id)


async def test_entities_and_defaults(hass: HomeAssistant) -> None:
    """Test entities and defaults."""
    sim = Sim()
    entry = await setup(hass, sim)
    # The slot scan finishes just after start-up; the sensor fills in then.
    for _ in range(50):
        if entry.runtime_data.client.preset_defined:
            break
        await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert state_of(hass, entry, "binary_sensor", "connection").state == "on"
    assert state_of(hass, entry, "sensor", "stored_presets").state == "0"
    routing = state_of(hass, entry, "sensor", "routing_summary")
    assert routing.state == "All from Input 001 Video"
    assert state_of(hass, entry, "sensor", "connected_outputs").state == "16"
    assert state_of(hass, entry, "sensor", "disconnected_outputs").state == "0"
    assert state_of(hass, entry, "sensor", "last_reply").state != "unknown"
    assert state_of(hass, entry, "sensor", "last_error").state == "None"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_set_route(hass: HomeAssistant) -> None:
    """Test set route."""
    sim = Sim()
    entry = await setup(hass, sim)
    dev = device_id(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "3", "outputs": ["1", "Output 002"]},
        blocking=True,
    )
    assert sim.video[1] == 3 and sim.video[2] == 3 and sim.video[3] == 1
    await hass.services.async_call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "Input 005 Video", "outputs": ["all"]},
        blocking=True,
    )
    assert set(sim.video.values()) == {5}
    await hass.services.async_call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "off", "outputs": 4},
        blocking=True,
    )
    assert sim.video[4] == 0
    for bad in (
        {"input": "99", "outputs": ["1"]},
        {"input": "1", "outputs": ["Nowhere"]},
        {"input": "1", "outputs": ["17"]},
    ):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "set_route", {"device_id": dev, **bad}, blocking=True
            )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_route", {"input": "1", "outputs": ["1"]}, blocking=True
        )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_preset_actions(hass: HomeAssistant) -> None:
    """Test preset actions."""
    sim = Sim()
    entry = await setup(hass, sim)
    dev = device_id(hass, entry)
    call = hass.services.async_call

    # Set a known picture, store it in slot 60 (blank, so no confirmation needed).
    await call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "2", "outputs": ["1"]},
        blocking=True,
    )
    await call(
        DOMAIN, "store_preset", {"device_id": dev, "preset": "60"}, blocking=True
    )
    assert 60 in sim.slots and sim.slots[60]["video"][1] == 2
    sensor = state_of(hass, entry, "sensor", "stored_presets")
    assert sensor.state == "1" and "Preset 60" in sensor.attributes["filled_presets"]

    # Storing again without confirmation is refused and changes nothing.
    await call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "9", "outputs": ["all"]},
        blocking=True,
    )
    with pytest.raises(ServiceValidationError):
        await call(
            DOMAIN, "store_preset", {"device_id": dev, "preset": "60"}, blocking=True
        )
    assert sim.slots[60]["video"][1] == 2
    # Confirmed overwrite works.
    await call(
        DOMAIN,
        "store_preset",
        {"device_id": dev, "preset": "60", "confirm_overwrite": True},
        blocking=True,
    )
    assert sim.slots[60]["video"][1] == 9

    # Recall restores routes and mode; the burst of packets from the unit is tolerated.
    await call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "4", "outputs": ["all"]},
        blocking=True,
    )
    await call(
        DOMAIN, "recall_preset", {"device_id": dev, "preset": "60"}, blocking=True
    )
    await hass.async_block_till_done()
    assert (
        state_of(hass, entry, "sensor", "routing_summary").state
        == "All from Input 009 Video"
    )

    # Blank and out of range slots are refused.
    with pytest.raises(HomeAssistantError):
        await call(
            DOMAIN, "recall_preset", {"device_id": dev, "preset": "7"}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await call(
            DOMAIN, "recall_preset", {"device_id": dev, "preset": "61"}, blocking=True
        )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_audio_only_and_mode_recall(hass: HomeAssistant) -> None:
    """Test audio only and mode recall."""
    sim = Sim()
    entry = await setup(hass, sim)
    dev = device_id(hass, entry)
    client = entry.runtime_data.client
    call = hass.services.async_call
    await client.async_set_mode(1)
    await client.async_set_route(1, 5)
    await call(DOMAIN, "store_preset", {"device_id": dev, "preset": "3"}, blocking=True)
    await client.async_set_mode(0)
    await client.async_set_route(1, 7)
    await call(
        DOMAIN, "recall_preset", {"device_id": dev, "preset": "3"}, blocking=True
    )
    assert client.mode == 1 and client.routes[1] == 5
    # audio-only recall keeps the video as it is
    await client.async_set_route(1, 8)
    await call(
        DOMAIN,
        "recall_preset",
        {"device_id": dev, "preset": "3", "audio_only": True},
        blocking=True,
    )
    assert client.routes[1] == 8
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_delete_is_refused_by_unit(hass: HomeAssistant) -> None:
    """Test delete is refused by unit."""
    sim = Sim()
    entry = await setup(hass, sim)
    client = entry.runtime_data.client
    await client.async_store_preset(5)
    with pytest.raises(Exception, match="out of range"):
        await client._request(  # the unit's own refusal, which we never send from the integration
            __import__(
                "custom_components.kramer.protocol", fromlist=["x"]
            ).build_packet(3, 5, 1, 1),
            3,
        )
    assert await client.async_preset_defined(5)
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_vs812_has_no_preset_or_off_features(hass: HomeAssistant) -> None:
    """Test vs812 has no preset or off features."""
    sim = Sim(outputs=1, presets=False, lock=False)
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "ip_address": "127.0.0.1",
            "port": port,
            "machine": 1,
            "model": "vs-812",
            "model_name": "VS-812",
            "inputs": 8,
            "outputs": 1,
            "supports_modes": False,
        },
        title="Lounge",
        unique_id="x",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reg = er.async_get(hass)
    keys = {
        e.unique_id.removeprefix(f"{entry.entry_id}_")
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
    }
    assert (
        "stored_presets" not in keys
        and "routing_summary" not in keys
        and "connected_outputs" not in keys
    )
    assert {"connection", "last_reply", "last_error"} <= keys
    # options flow shows the device-name menu with only "names" (no presets)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU and result["menu_options"] == ["names"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "names"}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "names"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_options_flow_presets(hass: HomeAssistant) -> None:
    """Test options flow presets."""
    sim = Sim()
    entry = await setup(hass, sim)
    opts = hass.config_entries.options
    flow = await opts.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU and set(flow["menu_options"]) == {
        "names",
        "presets",
    }
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "presets"})
    assert flow["step_id"] == "presets"
    choices = flow["data_schema"].schema["preset"].config["options"]
    assert len(choices) == 60 and choices[0]["label"] == "01 - Empty"

    # Pick slot 12: blank, so store needs no tick.
    flow = await opts.async_configure(flow["flow_id"], {"preset": "12"})
    assert flow["type"] is FlowResultType.MENU and "forget" not in flow["menu_options"]
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "store"})
    assert (
        flow["step_id"] == "store"
        and "empty" in flow["description_placeholders"]["warning"]
        and "Preset 12" in flow["description_placeholders"]["warning"]
    )
    result = await opts.async_configure(flow["flow_id"], {"scope": "all"})
    assert (
        result["type"] is FlowResultType.ABORT and result["reason"] == "preset_stored"
    )
    assert 12 in sim.slots

    # Describe it.
    flow = await opts.async_init(entry.entry_id)
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "presets"})
    choices = flow["data_schema"].schema["preset"].config["options"]
    assert choices[11]["label"] == "12 - Filled"
    flow = await opts.async_configure(flow["flow_id"], {"preset": "12"})
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "describe"})
    bad = await opts.async_configure(flow["flow_id"], {"description": "12"})
    assert bad["errors"] == {"description": "description_numeric"}
    done = await opts.async_configure(flow["flow_id"], {"description": "Film night"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["preset_descriptions"] == {"12": "Film night"}
    assert entry.state.value == "loaded"

    # Occupied slot: overwrite needs the tick.
    flow = await opts.async_init(entry.entry_id)
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "presets"})
    flow = await opts.async_configure(flow["flow_id"], {"preset": "12"})
    assert "forget" in flow["menu_options"]
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "store"})
    assert "overwrite" in flow["description_placeholders"]["warning"]
    again = await opts.async_configure(
        flow["flow_id"], {"scope": "all", "confirm": False}
    )
    assert again["errors"] == {"confirm": "confirm_required"}
    old = dict(sim.slots[12]["video"])
    sim.video[3] = 11
    done = await opts.async_configure(
        flow["flow_id"], {"scope": "all", "confirm": True}
    )
    assert (
        done["reason"] == "preset_stored" and sim.slots[12]["video"][3] == 11 != old[3]
    )

    # Recall needs the tick, then works by description.
    flow = await opts.async_init(entry.entry_id)
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "presets"})
    flow = await opts.async_configure(flow["flow_id"], {"preset": "12"})
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "recall"})
    again = await opts.async_configure(
        flow["flow_id"], {"scope": "all", "confirm": False}
    )
    assert again["errors"] == {"confirm": "confirm_required"}
    done = await opts.async_configure(
        flow["flow_id"], {"scope": "all", "confirm": True}
    )
    assert done["reason"] == "preset_recalled"

    # Recalling a blank slot is not possible.
    flow = await opts.async_init(entry.entry_id)
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "presets"})
    flow = await opts.async_configure(flow["flow_id"], {"preset": "30"})
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "recall"})
    assert flow["type"] is FlowResultType.ABORT and flow["reason"] == "slot_blank"

    # Forget: needs the tick; unit untouched; description gone.
    flow = await opts.async_init(entry.entry_id)
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "presets"})
    flow = await opts.async_configure(flow["flow_id"], {"preset": "12"})
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "forget"})
    again = await opts.async_configure(flow["flow_id"], {"confirm": False})
    assert again["errors"] == {"confirm": "confirm_required"}
    done = await opts.async_configure(flow["flow_id"], {"confirm": True})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["preset_descriptions"] == {}
    assert 12 in sim.slots

    # Names step keeps the descriptions already saved.
    flow = await opts.async_init(entry.entry_id)
    flow = await opts.async_configure(flow["flow_id"], {"next_step_id": "names"})
    inputs = "\n".join(["Sky"] + [f"Input {i:03d}" for i in range(2, 17)])
    outputs = "\n".join(f"Output {i:03d}" for i in range(1, 17))
    done = await opts.async_configure(
        flow["flow_id"],
        {
            "input_names": inputs,
            "audio_input_names": "\n".join(
                f"Input {i:03d} Audio" for i in range(1, 17)
            ),
            "output_names": outputs,
        },
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert (
        entry.options["input_names"][0] == "Sky"
        and "preset_descriptions" in entry.options
    )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_diagnostics_redacts(hass: HomeAssistant) -> None:
    """Test diagnostics redacts."""
    from custom_components.kramer.diagnostics import async_get_config_entry_diagnostics

    sim = Sim()
    entry = await setup(hass, sim, preset_slots=0)
    hass.config_entries.async_update_entry(
        entry,
        options={"input_names": ["Secret TV"], "preset_descriptions": {"1": "Private"}},
    )
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert (
        "127.0.0.1" not in text
        and "Secret TV" not in text
        and "Private" not in text
        and "Test Matrix" not in text
    )
    assert (
        diag["state"]["available"] is True
        and diag["capabilities"]["preset_slots"] == 60
    )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_panel_events_and_connection_sensor(hass: HomeAssistant) -> None:
    """Test panel events and connection sensor."""
    sim = Sim()
    entry = await setup(hass, sim)
    sim.video[2] = 6
    sim.panel(1, 2, 6)
    await hass.async_block_till_done()
    await asyncio.sleep(0.2)
    assert entry.runtime_data.client.routes[2] == 6
    assert state_of(hass, entry, "sensor", "routing_summary").state == "Mixed"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_config_flow_known_and_other(hass: HomeAssistant) -> None:
    """Test config flow known and other."""
    sim = Sim()
    port = await sim.start()
    base = {"name": "Dining", "ip_address": "127.0.0.1", "port": port, "machine": 1}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**base, "model": "vs-162av-rca"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["inputs"] == 16 and "preset_slots" not in result["data"]
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.runtime_data.preset_slots == 60
    await hass.config_entries.async_unload(entry.entry_id)

    # An "Other" model asks for the number of stored setups.
    sim2 = Sim(machine=2, presets=True)
    port2 = await sim2.start()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**base, "port": port2, "machine": 2, "model": "other"}
    )
    assert result["step_id"] == "other"
    assert "preset_slots" in result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "model_name": "VS-9000",
            "inputs": 8,
            "outputs": 8,
            "supports_modes": True,
            "preset_slots": 10,
        },
    )
    assert (
        result["type"] is FlowResultType.CREATE_ENTRY
        and result["data"]["preset_slots"] == 10
    )
    await hass.async_block_till_done()
    other = next(
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.data["model"] == "other"
    )
    assert other.runtime_data.preset_slots == 10
    await hass.config_entries.async_unload(other.entry_id)
    await sim.stop()
    await sim2.stop()


async def test_other_model_connection_failure_returns_to_first_screen(
    hass: HomeAssistant,
) -> None:
    """A failed connection test on the second screen goes back to the first, keeping every typed value."""
    dead = Sim()
    dead_port = await dead.start()
    await dead.stop()  # nothing listens here any more
    base = {"name": "Spare", "ip_address": "127.0.0.1", "port": dead_port, "machine": 3}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**base, "model": "other"}
    )
    assert result["step_id"] == "other"
    second = {
        "model_name": "VS-9000",
        "inputs": 12,
        "outputs": 6,
        "supports_modes": True,
        "preset_slots": 7,
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], second)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    assert result["errors"]["base"] in {"cannot_connect", "no_reply"}
    shown = {str(k): k.default() for k in result["data_schema"].schema}
    assert (
        shown["name"] == "Spare"
        and shown["ip_address"] == "127.0.0.1"
        and shown["machine"] == 3
    )
    assert shown["model"] == "other"

    # Fixing the address and continuing finds the second screen already filled in.
    sim = Sim(machine=3, presets=True)
    port = await sim.start()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**base, "port": port, "model": "other"}
    )
    assert result["step_id"] == "other"
    filled = {str(k): k.default() for k in result["data_schema"].schema}
    assert (
        filled["model_name"] == "VS-9000"
        and filled["inputs"] == 12
        and filled["outputs"] == 6
    )
    assert filled["preset_slots"] == 7 and filled["supports_modes"] is True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], second)
    assert (
        result["type"] is FlowResultType.CREATE_ENTRY and result["data"]["inputs"] == 12
    )
    await hass.async_block_till_done()
    for e in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(e.entry_id)
    await sim.stop()


async def test_existing_entry_gets_three_digit_names(hass: HomeAssistant) -> None:
    """Old two-digit defaults (saved or not) become three-digit; real custom names are kept."""
    sim = Sim(outputs=4)
    port = await sim.start()
    entry = make_entry(port, outputs=4, inputs=4)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            "input_names": ["01", "Input 02", "Sky", ""],
            "output_names": ["Output 01", "Lounge TV", "", ""],
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = entry.runtime_data
    assert data.input_names == [
        "Input 001 Video",
        "Input 002 Video",
        "Sky",
        "Input 004 Video",
    ]
    assert data.audio_input_names == [
        "Input 001 Audio",
        "Input 002 Audio",
        "Input 003 Audio",
        "Input 004 Audio",
    ]
    assert data.output_names == [
        "Output 001 Video",
        "Lounge TV",
        "Output 003 Video",
        "Output 004 Video",
    ]
    assert data.audio_output_names == [
        "Output 001 Audio",
        "Output 002 Audio",
        "Output 003 Audio",
        "Output 004 Audio",
    ]
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_identity_and_firmware_sensors(hass: HomeAssistant) -> None:
    """Test identity and firmware sensors."""
    sim = Sim()
    entry = await setup(hass, sim)
    for _ in range(50):
        if entry.runtime_data.client.identity_checked:
            break
        await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    firmware = state_of(hass, entry, "sensor", "firmware")
    assert firmware.state == "1.5" and firmware.attributes["audio_firmware"] == "1.5"
    identity = state_of(hass, entry, "sensor", "identity")
    assert identity.state == "162AV" and identity.attributes["name_raw"] == [1, 62]
    assert state_of(hass, entry, "sensor", "last_error").state == "None"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_no_identity_sensors_when_unit_is_silent(hass: HomeAssistant) -> None:
    """Test no identity sensors when unit is silent."""
    sim = Sim(identify=False)
    entry = await setup(hass, sim)
    for _ in range(80):
        if entry.runtime_data.client.identity_checked:
            break
        await asyncio.sleep(0.1)
    await hass.async_block_till_done()
    assert entry.runtime_data.client.identity_checked
    reg = er.async_get(hass)
    assert (
        reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_firmware") is None
    )
    assert (
        reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_identity") is None
    )
    assert state_of(hass, entry, "binary_sensor", "connection").state == "on"
    # Unanswered identity questions are feature detection, not errors.
    assert state_of(hass, entry, "sensor", "last_error").state == "None"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


def entity_id(hass, entry, domain: str, key: str) -> str:
    """Entity id."""
    reg = er.async_get(hass)
    found = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert found, f"no {domain} {key}"
    return found


async def settle(hass: HomeAssistant, entry, seconds: float = 0.3) -> None:
    """Settle."""
    await asyncio.sleep(seconds)
    await hass.async_block_till_done()


async def test_video_and_audio_entities(hass: HomeAssistant) -> None:
    """Test video and audio entities."""
    sim = Sim()
    entry = await setup(hass, sim)
    video = state_of(hass, entry, "select", "output_1")
    audio = state_of(hass, entry, "select", "audio_output_1")
    step = state_of(hass, entry, "binary_sensor", "in_step_1")
    assert (
        video.name == "Test Matrix Output 001 Video"
        and audio.name == "Test Matrix Output 001 Audio"
    )
    assert video.state == "Input 001 Video" and audio.state == "Input 001 Audio"
    assert (
        "Input 002 Audio" in audio.attributes["options"]
        and "Off" in audio.attributes["options"]
    )
    assert step.state == "on" and step.attributes["audio_follow_video"] is True
    assert (
        video.attributes["icon"] == "mdi:link"
        and video.attributes["audio_follow_video"] is True
    )

    # Audio cannot be changed on its own in Audio follows video.
    with pytest.raises(ServiceValidationError, match="Breakaway"):
        await hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": entity_id(hass, entry, "select", "audio_output_1"),
                "option": "Input 004 Audio",
            },
            blocking=True,
        )

    # Breakaway: video and audio move independently and the chain breaks.
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id(hass, entry, "select", "mode"), "option": "Breakaway"},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": entity_id(hass, entry, "select", "audio_output_1"),
            "option": "Input 004 Audio",
        },
        blocking=True,
    )
    await settle(hass, entry)
    assert sim.audio[1] == 4 and sim.video[1] == 1
    assert state_of(hass, entry, "select", "audio_output_1").state == "Input 004 Audio"
    assert state_of(hass, entry, "select", "output_1").state == "Input 001 Video"
    step = state_of(hass, entry, "binary_sensor", "in_step_1")
    assert step.state == "off" and step.attributes["icon"] == "mdi:link-off"
    assert (
        step.attributes["video_input"] == "Input 001 Video"
        and step.attributes["audio_input"] == "Input 004 Audio"
    )
    assert state_of(hass, entry, "sensor", "routing_summary").state == "Mixed"
    assert (
        "Audio: Input 004 Audio"
        in state_of(hass, entry, "sensor", "routing_summary").attributes["Output 001"]
    )

    # Video alone leaves audio where it is.
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": entity_id(hass, entry, "select", "output_1"),
            "option": "Input 006 Video",
        },
        blocking=True,
    )
    assert sim.video[1] == 6 and sim.audio[1] == 4

    # Back to Audio follows video: audio snaps to video and the chain is whole again.
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": entity_id(hass, entry, "select", "mode"),
            "option": "Audio follows video",
        },
        blocking=True,
    )
    await settle(hass, entry, 0.6)
    assert sim.audio[1] == 6
    assert state_of(hass, entry, "binary_sensor", "in_step_1").state == "on"
    assert state_of(hass, entry, "select", "audio_output_1").state == "Input 006 Audio"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_panel_events_for_audio_and_afv(hass: HomeAssistant) -> None:
    """Test panel events for audio and afv."""
    sim = Sim()
    entry = await setup(hass, sim)
    client = entry.runtime_data.client
    # AFV: the unit announces only the video route, yet audio moved too.
    sim.panel_route("video", 2, 7)
    await settle(hass, entry)
    assert client.routes[2] == 7 and client.audio_routes[2] == 7
    # Breakaway: a panel audio route is announced and only audio moves.
    await client.async_set_mode(1)
    sim.panel_route("audio", 2, 9)
    await settle(hass, entry)
    assert client.audio_routes[2] == 9 and client.routes[2] == 7
    assert state_of(hass, entry, "binary_sensor", "in_step_2").state == "off"
    sim.panel_route("video", 2, 9)
    await settle(hass, entry)
    assert state_of(hass, entry, "binary_sensor", "in_step_2").state == "on"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_media_player_and_all_outputs_set_both(hass: HomeAssistant) -> None:
    """Test media player and all outputs set both."""
    sim = Sim()
    entry = await setup(hass, sim)
    client = entry.runtime_data.client
    await client.async_set_mode(1)
    await client.async_set_audio_route(3, 8)
    await settle(hass, entry)
    player = hass.states.get(
        er.async_get(hass).async_get_entity_id("media_player", DOMAIN, entry.entry_id)
    )
    assert player.attributes["source"] == "Mixed"
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": player.entity_id, "source": "Input 005 Video"},
        blocking=True,
    )
    assert set(sim.video.values()) == {5} and set(sim.audio.values()) == {5}
    player = hass.states.get(player.entity_id)
    assert (
        player.attributes["source"] == "Input 005 Video"
        and player.attributes["icon"] == "mdi:link"
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {
            "entity_id": entity_id(hass, entry, "select", "all_outputs"),
            "option": "Input 002 Video",
        },
        blocking=True,
    )
    assert set(sim.video.values()) == {2} and set(sim.audio.values()) == {2}
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_set_route_layers(hass: HomeAssistant) -> None:
    """Test set route layers."""
    sim = Sim()
    entry = await setup(hass, sim)
    dev = device_id(hass, entry)
    call = hass.services.async_call
    with pytest.raises(ServiceValidationError, match="Breakaway"):
        await call(
            DOMAIN,
            "set_route",
            {"device_id": dev, "input": "3", "outputs": ["1"], "layer": "audio"},
            blocking=True,
        )
    await entry.runtime_data.client.async_set_mode(1)
    await call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "3", "outputs": ["1"], "layer": "audio"},
        blocking=True,
    )
    assert sim.audio[1] == 3 and sim.video[1] == 1
    await call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "4", "outputs": ["1"], "layer": "video"},
        blocking=True,
    )
    assert sim.video[1] == 4 and sim.audio[1] == 3
    await call(
        DOMAIN,
        "set_route",
        {"device_id": dev, "input": "Input 007 Audio", "outputs": ["Output 001"]},
        blocking=True,
    )
    assert sim.video[1] == 7 and sim.audio[1] == 7
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_vs812_has_no_audio_entities_or_suffixes(hass: HomeAssistant) -> None:
    """Test vs812 has no audio entities or suffixes."""
    sim = Sim(outputs=1, presets=False, lock=False)
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "ip_address": "127.0.0.1",
            "port": port,
            "machine": 1,
            "model": "vs-812",
            "model_name": "VS-812",
            "inputs": 8,
            "outputs": 1,
            "supports_modes": False,
        },
        title="Lounge",
        unique_id="y",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    video = state_of(hass, entry, "select", "output_1")
    assert video.name == "Lounge Output 001" and video.state == "Input 001"
    reg = er.async_get(hass)
    keys = {
        e.unique_id.removeprefix(f"{entry.entry_id}_")
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
    }
    assert "audio_output_1" not in keys and "in_step_1" not in keys
    assert (
        "icon" not in video.attributes and "audio_follow_video" not in video.attributes
    )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_unit_that_does_not_report_audio_is_still_usable(
    hass: HomeAssistant,
) -> None:
    """Test unit that does not report audio is still usable."""
    sim = Sim(audio=False)
    entry = await setup(hass, sim)
    client = entry.runtime_data.client
    assert client.available and client.audio_supported is False
    reg = er.async_get(hass)
    assert (
        reg.async_get_entity_id("select", DOMAIN, f"{entry.entry_id}_audio_output_1")
        is None
    )
    assert (
        reg.async_get_entity_id("binary_sensor", DOMAIN, f"{entry.entry_id}_in_step_1")
        is None
    )
    assert state_of(hass, entry, "select", "output_1").name == "Test Matrix Output 001"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_panel_lock_announced_without_unit_marker(hass: HomeAssistant) -> None:
    """Test panel lock announced without unit marker."""
    sim = Sim()
    entry = await setup(hass, sim)
    assert state_of(hass, entry, "switch", "panel_lock").state == "off"
    sim.panel_lock(True)
    await settle(hass, entry)
    assert state_of(hass, entry, "switch", "panel_lock").state == "on"
    sim.panel_lock(False)
    await settle(hass, entry)
    assert state_of(hass, entry, "switch", "panel_lock").state == "off"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_panel_store_and_recall_are_noticed(hass: HomeAssistant) -> None:
    """Test panel store and recall are noticed."""
    sim = Sim()
    entry = await setup(hass, sim)
    client = entry.runtime_data.client
    await asyncio.sleep(0.5)  # let the first slot scan finish
    assert not any(client.preset_defined.values())

    # A setup stored from the front panel shows up as occupied, and a rescan agrees.
    sim.video = dict.fromkeys(range(1, 17), 5)
    sim.audio = dict.fromkeys(range(1, 17), 5)
    sim.panel_store(10)
    await settle(hass, entry, 0.6)
    assert client.preset_defined[10] is True
    sensor = state_of(hass, entry, "sensor", "stored_presets")
    assert sensor.state == "1" and "Preset 10" in sensor.attributes["filled_presets"]

    # A panel recall re-reads the routes, even though the unit sends no route packets.
    sim.video = dict.fromkeys(range(1, 17), 2)
    sim.audio = dict.fromkeys(range(1, 17), 2)
    sim.panel_recall(10)
    await settle(hass, entry, 0.6)
    assert client.routes[1] == 5 and client.audio_routes[1] == 5

    # Deleting from the panel clears it.
    sim.panel_store(10, delete=True)
    await settle(hass, entry, 0.6)
    assert client.preset_defined[10] is False
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_vs808yc_model(hass: HomeAssistant) -> None:
    """Test vs808yc model."""
    from custom_components.kramer.models import MODELS

    model = MODELS["vs-808yc"]
    assert (model.name, model.inputs, model.outputs, model.preset_slots) == (
        "VS-808YC",
        8,
        8,
        16,
    )
    assert model.supports_modes and model.supports_off and model.supports_lock

    sim = Sim(outputs=8)
    entry = await setup(
        hass, sim, model="vs-808yc", model_name="VS-808YC", inputs=8, outputs=8
    )
    assert entry.runtime_data.preset_slots == 16
    reg = er.async_get(hass)
    keys = {
        e.unique_id.removeprefix(f"{entry.entry_id}_")
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
    }
    assert {
        "output_8",
        "audio_output_8",
        "mode",
        "panel_lock",
        "stored_presets",
    } <= keys
    assert "output_9" not in keys
    # The config flow offers it by name.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    labels = [
        o["label"] for o in result["data_schema"].schema["model"].config["options"]
    ]
    assert "VS-808YC" in labels
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_custom_refresh_switch_and_number(hass: HomeAssistant) -> None:
    """Test custom refresh switch and number."""
    sim = Sim()
    entry = await setup(hass, sim)
    client = entry.runtime_data.client
    switch = state_of(hass, entry, "switch", "custom_refresh")
    number = state_of(hass, entry, "number", "refresh_interval")
    assert switch.state == "off"
    assert number.state == "unavailable"  # greyed out while on the default
    assert client.refresh_interval == 60
    reg = er.async_get(hass)
    switch_id = reg.async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_custom_refresh"
    )
    number_id = reg.async_get_entity_id(
        "number", DOMAIN, f"{entry.entry_id}_refresh_interval"
    )

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "60"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 15}, blocking=True
    )
    await hass.async_block_till_done()
    assert client.refresh_interval == 15
    assert (
        entry.options["refresh_seconds"] == 15
        and entry.options["custom_refresh"] is True
    )
    # Out of range is refused, and the integration was not reloaded by the change.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number_id, "value": 2}, blocking=True
        )
    assert entry.runtime_data.client is client

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert client.refresh_interval == 60
    assert hass.states.get(number_id).state == "unavailable"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_saved_custom_refresh_is_used_at_start(hass: HomeAssistant) -> None:
    """Test saved custom refresh is used at start."""
    sim = Sim()
    port = await sim.start()
    entry = make_entry(port)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={"custom_refresh": True, "refresh_seconds": 600}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.client.refresh_interval == 600
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_model_notes_and_form_values_kept(hass: HomeAssistant) -> None:
    """Test model notes and form values kept."""
    from custom_components.kramer.models import MODELS

    assert (
        "not announced" in MODELS["vs-162av-rca"].notes
        and "- Presets" in MODELS["vs-162av-rca"].notes
    )
    sim = Sim()
    entry = await setup(hass, sim)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert "not announced" in result["description_placeholders"]["notes"]
    assert result["description_placeholders"]["name"] == entry.title
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "names"}
    )
    bad = "\n".join(["Same", "Same"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"input_names": bad, "audio_input_names": "A", "output_names": "Out"},
    )
    assert result["errors"]["input_names"] == "duplicate_names"
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults["input_names"] == bad and defaults["output_names"] == "Out"

    # Add-integration form keeps typed values after a failed connection.
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "Typed",
            "ip_address": "127.0.0.1",
            "port": 1,
            "machine": 1,
            "model": "vs-812",
        },
    )
    assert flow["errors"]["base"] == "cannot_connect"
    defaults = {str(k): k.default() for k in flow["data_schema"].schema}
    assert (
        defaults["name"] == "Typed"
        and defaults["port"] == 1
        and defaults["model"] == "vs-812"
    )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


MATRIX_KEYS = {
    "schema_version", "name", "model", "inputs", "outputs", "features", "mode", "input_names", "audio_input_names",
    "output_names", "audio_output_names", "video_routes", "audio_routes", "presets", "firmware", "last_error", "locked",
}  # fmt: skip


async def test_status_entity_contents_and_contract(hass: HomeAssistant) -> None:
    """Test status entity contents and contract."""
    from homeassistant.const import MATCH_ALL

    from custom_components.kramer.sensor import KramerStatusSensor

    sim = Sim()
    entry = await setup(hass, sim)
    await settle(hass, entry, 0.5)
    state = state_of(hass, entry, "sensor", "status")
    assert state.state == "online"
    a = state.attributes
    # The contract: these names and types are only ever added to, never changed.
    assert set(a) >= MATRIX_KEYS
    assert a["schema_version"] == 1
    assert a["name"] == "Test Matrix" and a["model"] == "VS-162AV/RCA"
    assert (a["inputs"], a["outputs"]) == (16, 16)
    assert a["features"] == {
        "modes": True,
        "audio": True,
        "off": True,
        "lock": True,
        "presets": 60,
    }
    assert a["mode"] == "afv"
    assert len(a["input_names"]) == 16 and len(a["audio_input_names"]) == 16
    assert (
        a["output_names"][0] == "Output 001 Video"
        and a["audio_output_names"][0] == "Output 001 Audio"
    )
    assert len(a["video_routes"]) == 16 and len(a["audio_routes"]) == 16
    assert set(a["video_routes"]) == {1} and set(a["audio_routes"]) == {1}
    assert (
        set(a["presets"]) == {"slots", "filled", "descriptions"}
        and a["presets"]["slots"] == 60
    )
    assert set(a["last_error"]) == {"message", "time"}
    # Route changes are not stored in history.
    assert KramerStatusSensor._unrecorded_attributes == frozenset({MATCH_ALL})

    # A front-panel change shows up (updates are combined, so allow a moment).
    sim.panel_route("video", 3, 5)
    await settle(hass, entry, 0.5)
    a = state_of(hass, entry, "sensor", "status").attributes
    assert a["video_routes"][2] == 5 and a["audio_routes"][2] == 5
    # The unit going away makes it unavailable.
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_status_entity_on_single_output_model(hass: HomeAssistant) -> None:
    """Test status entity on single output model."""
    sim = Sim(outputs=1, presets=False, lock=False)
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "ip_address": "127.0.0.1", "port": port, "machine": 1, "model": "vs-812", "model_name": "VS-812",
            "inputs": 8, "outputs": 1, "supports_modes": False,
        },
        title="Lounge",
        unique_id="y",
    )  # fmt: skip
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await settle(hass, entry, 0.5)
    a = state_of(hass, entry, "sensor", "status").attributes
    assert (
        a["features"]["modes"] is False
        and a["features"]["audio"] is False
        and a["features"]["presets"] == 0
    )
    assert (
        a["mode"] is None and a["audio_routes"] == [] and a["audio_input_names"] == []
    )
    assert a["outputs"] == 1 and len(a["video_routes"]) == 1
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_audio_output_names(hass: HomeAssistant) -> None:
    """Test audio output names."""
    sim = Sim()
    port = await sim.start()
    entry = make_entry(port)
    entry.add_to_hass(hass)
    audio = ["Cinema"] + [""] * 15
    hass.config_entries.async_update_entry(entry, options={"audio_output_names": audio})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await settle(hass, entry, 0.4)
    # A named audio output uses its own name; blanks use "Output 002 Audio" and so on.
    assert (
        state_of(hass, entry, "select", "audio_output_1")
        .attributes["friendly_name"]
        .endswith("Cinema")
    )
    assert (
        "Output 002 Audio"
        in state_of(hass, entry, "select", "audio_output_2").attributes["friendly_name"]
    )
    assert (
        "Output 001 Video"
        in state_of(hass, entry, "select", "output_1").attributes["friendly_name"]
    )
    a = state_of(hass, entry, "sensor", "status").attributes
    assert (
        a["audio_output_names"][0] == "Cinema"
        and a["audio_output_names"][1] == "Output 002 Audio"
    )

    # The names screen offers and saves them (blank where equal to the output's name).
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "names"}
    )
    assert (
        result["step_id"] == "names_av"
    )  # the form with "Video output names" and "Audio output names"
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults["audio_output_names"].splitlines()[0] == "Cinema"
    assert defaults["output_names"].splitlines()[0] == "Output 001 Video"
    typed = {
        "input_names": defaults["input_names"],
        "audio_input_names": defaults["audio_input_names"],
        "output_names": defaults["output_names"],
        "audio_output_names": "Cinema\nSame\nSame",
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], typed)
    assert result["errors"]["audio_output_names"] == "duplicate_names"
    typed["audio_output_names"] = "Cinema\n\nSurround"
    result = await hass.config_entries.options.async_configure(result["flow_id"], typed)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["audio_output_names"][:3] == ["Cinema", "", "Surround"]
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_set_routing_mode_action(hass: HomeAssistant) -> None:
    """Test set routing mode action."""
    from custom_components.kramer.protocol import MODE_AFV, MODE_BREAKAWAY

    sim = Sim()
    entry = await setup(hass, sim)
    dev = device_id(hass, entry)
    client = entry.runtime_data.client
    await hass.services.async_call(
        DOMAIN,
        "set_routing_mode",
        {"device_id": dev, "mode": "breakaway"},
        blocking=True,
    )
    assert client.mode == MODE_BREAKAWAY and sim.mode == 1
    await hass.services.async_call(
        DOMAIN, "set_routing_mode", {"device_id": dev, "mode": "afv"}, blocking=True
    )
    assert client.mode == MODE_AFV and sim.mode == 0
    with pytest.raises(Exception):  # noqa: B017 - a bad choice is refused by the schema
        await hass.services.async_call(
            DOMAIN, "set_routing_mode", {"device_id": dev, "mode": "x"}, blocking=True
        )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()
