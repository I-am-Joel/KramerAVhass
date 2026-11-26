"""Tests for the 0.16.0 models: VS-1202YC, VS-5x4 (2-byte) and VS-5x4 (Protocol 2000)."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kramer.const import DOMAIN
from custom_components.kramer.models import MODELS

from .sim_v016 import P2000FiveByFour, TwoByteSim

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def setup(hass: HomeAssistant, sim, key: str):
    """Set up the integration against a simulated unit."""
    port = await sim.start()
    m = MODELS[key]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test",
        unique_id=f"127.0.0.1:{port}:1",
        data={
            "ip_address": "127.0.0.1",
            "port": port,
            "machine": 1,
            "model": key,
            "model_name": m.name,
            "inputs": m.inputs,
            "outputs": m.outputs,
            "supports_modes": False,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await entry.runtime_data.client.async_wait_ready(5)
    return entry


def ent(hass, entry, key, domain="select"):
    """Find an entity state."""
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert eid, key
    return eid


async def choose(hass, eid, option):
    """Choose a select option."""
    await hass.services.async_call("select", "select_option", {"entity_id": eid, "option": option}, blocking=True)


async def finish(hass, entry, sim):
    """Tear down."""
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_1202yc(hass: HomeAssistant) -> None:
    """Routes, panel presses, no ALL command (one command at a time), no levels."""
    sim = TwoByteSim("1202")
    entry = await setup(hass, sim, "vs-1202yc")
    c = entry.runtime_data.client
    assert c.routes == {1: 1, 2: 1}
    await choose(hass, ent(hass, entry, "output_2"), "Input 005")
    assert sim.routes[2] == 5 and c.routes[2] == 5
    await c.async_set_route(0, 7)
    assert sim.routes == {1: 7, 2: 7}
    sent = [x[1] for x in sim.log[-2:]]
    assert sent == [0x80 | 13, 0x80 | 14]  # 2*7+o-2
    sim.panel_route(1, 12)
    await asyncio.sleep(0.1)
    assert c.routes[1] == 12
    assert not [e for e in er.async_get(hass).entities.values() if e.unique_id.startswith(f"{entry.entry_id}_level")]
    assert entry.runtime_data.supports_off is False
    await finish(hass, entry, sim)


async def test_5x4_two_byte(hass: HomeAssistant) -> None:
    """Routes, ALL, OFF, levels and panel announcements."""
    sim = TwoByteSim("5x4")
    sim.levels[2] = 2
    entry = await setup(hass, sim, "vs-5x4-2byte")
    c = entry.runtime_data.client
    assert c.levels == {1: 0, 2: 2, 3: 0, 4: 0}
    await choose(hass, ent(hass, entry, "output_3"), "Input 004")
    assert sim.routes[3] == 4
    await c.async_set_route(0, 3)
    assert set(sim.routes.values()) == {3}
    await c.async_set_route(2, 0)
    assert sim.routes[2] == 0 and c.routes[2] == 0
    await c.async_set_route(0, 0)
    assert set(sim.routes.values()) == {0}
    await choose(hass, ent(hass, entry, "level_4"), "6 dB")
    assert sim.levels[4] == 6 and c.levels[4] == 6
    sim.panel_level(1, 4)
    await asyncio.sleep(0.1)
    assert c.levels[1] == 4
    sim.panel_all(5)
    await asyncio.sleep(0.3)
    assert set(c.routes.values()) == {5}
    # the unit refuses nothing here, but the driver never sends out-of-range values
    with pytest.raises(Exception):
        await c.async_set_level(1, 3)
    with pytest.raises(Exception):
        await c.async_set_route(5, 1)
    await finish(hass, entry, sim)


async def test_5x4_protocol2000(hass: HomeAssistant) -> None:
    """P2000: no audio status, mode or lock questions; levels; busy retry; range checks."""
    sim = P2000FiveByFour()
    sim.levels[2] = 2
    entry = await setup(hass, sim, "vs-5x4")
    c = entry.runtime_data.client
    assert c.levels == {1: 0, 2: 2, 3: 0, 4: 0}
    assert c.routes == {1: 1, 2: 1, 3: 1, 4: 1}
    asked = {x[0] & 0x3F for x in sim.log}
    assert not asked & {6, 8, 30, 31, 11}  # questions the unit refuses are never asked
    await choose(hass, ent(hass, entry, "level_3"), "4 dB")
    assert sim.levels[3] == 4
    await choose(hass, ent(hass, entry, "output_2"), "Input 005")
    assert sim.routes[2] == 5
    sim.panel_level(1, 6)
    await asyncio.sleep(0.1)
    assert c.levels[1] == 6
    c._busy_wait = 0.05
    sim.busy = 2
    await c.async_set_level(4, 2)
    assert sim.levels[4] == 2 and c.available
    before = len(sim.log)
    for bad in (lambda: c.async_set_route(5, 1), lambda: c.async_set_route(1, 6), lambda: c.async_set_level(1, 3), lambda: c.async_set_level(5, 0)):
        with pytest.raises(Exception):
            await bad()
    assert len(sim.log) == before  # nothing out of range ever reached the unit
    await finish(hass, entry, sim)


async def test_status_and_models(hass: HomeAssistant) -> None:
    """The status sensor carries the levels; the dropdown lists the three new entries."""
    from custom_components.kramer.models import model_choices

    labels = [label for _, label in model_choices()]
    assert "VS-1202YC (2-byte protocol)" in labels
    assert "VS-5x4 (Protocol 2000)" in labels and "VS-5x4 (2-byte protocol)" in labels
    sim = P2000FiveByFour()
    entry = await setup(hass, sim, "vs-5x4")
    state = hass.states.get(ent(hass, entry, "status", "sensor"))
    assert state.attributes["audio_levels"]["available"] == [0, 2, 4, 6]
    assert state.attributes["audio_levels"]["outputs"]["1"] == 0
    assert state.attributes["features"]["lock"] is False
    await finish(hass, entry, sim)


async def test_config_flow_two_byte(hass: HomeAssistant) -> None:
    """A 2-byte model is probed with the 2-byte status request and the machine number is capped at 8."""
    sim = TwoByteSim("1202")
    port = await sim.start()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"model": "vs-1202yc"})
    assert result["step_id"] == "connection"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matrix", "ip_address": "127.0.0.1", "port": port, "machine": 1}
    )
    assert result["type"] == "create_entry", result
    assert result["data"]["model"] == "vs-1202yc" and result["data"]["outputs"] == 2
    await hass.config_entries.async_unload(result["result"].entry_id)
    await sim.stop()
