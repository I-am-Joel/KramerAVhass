"""Home Assistant level tests for the VP-729, against the simulated unit."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kramer.const import DOMAIN

from ..vp729_sim import Vp729Sim

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


async def setup(hass: HomeAssistant, sim: Vp729Sim) -> MockConfigEntry:
    """Set up the integration against the simulated VP-729."""
    port = await sim.start()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"ip_address": "127.0.0.1", "port": port, "model": "vp-729", "model_name": "VP-729"},
        title="Scaler",
        unique_id=f"127.0.0.1:{port}:vp729",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass, entry, domain: str, key: str) -> str:
    """Find an entity by its key."""
    found = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert found, f"no {domain} {key}"
    return found


async def test_entities_created_and_states(hass: HomeAssistant) -> None:
    """Every kind of entity is created and shows the unit's values."""
    sim = Vp729Sim()
    entry = await setup(hass, sim)
    assert hass.states.get(entity_id(hass, entry, "sensor", "status")).state == "online"
    assert hass.states.get(entity_id(hass, entry, "binary_sensor", "connection")).state == "on"
    assert hass.states.get(entity_id(hass, entry, "select", "aspect_ratio")).state == "Follow output"
    assert hass.states.get(entity_id(hass, entry, "select", "output_resolution")).state == "1280x720@60Hz"
    assert hass.states.get(entity_id(hass, entry, "switch", "power")).state == "on"
    assert hass.states.get(entity_id(hass, entry, "sensor", "firmware")).state.startswith("VTB 1.26h")
    assert hass.states.get(entity_id(hass, entry, "sensor", "main_input_status")).state == "PAL"
    assert hass.states.get(entity_id(hass, entry, "sensor", "pip_input_status")).state == "No input detected"
    assert hass.states.get(entity_id(hass, entry, "number", "brightness")) is not None
    assert hass.states.get(entity_id(hass, entry, "button", "key_menu")) is not None
    assert hass.states.get(entity_id(hass, entry, "button", "save_profile_8")) is not None
    assert hass.states.get(entity_id(hass, entry, "button", "volume_up")) is not None
    assert hass.states.get(entity_id(hass, entry, "number", "refresh_interval")) is not None
    # Needs aspect Custom, so it is unavailable now.
    assert hass.states.get(entity_id(hass, entry, "number", "h_zoom")).state == "unavailable"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_controls_write_and_dependants_unlock(hass: HomeAssistant) -> None:
    """Setting aspect to Custom makes the zoom controls available."""
    sim = Vp729Sim()
    entry = await setup(hass, sim)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id(hass, entry, "select", "aspect_ratio"), "option": "Custom"}, blocking=True
    )
    assert sim.state[27] == 5
    for _ in range(40):
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        if hass.states.get(entity_id(hass, entry, "number", "h_zoom")).state != "unavailable":
            break
    assert hass.states.get(entity_id(hass, entry, "number", "h_zoom")).state != "unavailable"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id(hass, entry, "number", "brightness"), "value": 42}, blocking=True
    )
    assert sim.state[12] == 42
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id(hass, entry, "switch", "mute")}, blocking=True)
    assert sim.state[101] == 1
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_buttons_send_keys_without_value(hass: HomeAssistant) -> None:
    """Key buttons, volume and profile buttons send the agreed commands."""
    sim = Vp729Sim()
    entry = await setup(hass, sim)
    for key in ("key_menu", "volume_up", "volume_down", "save_profile_3"):
        await hass.services.async_call("button", "press", {"entity_id": entity_id(hass, entry, "button", key)}, blocking=True)
    assert "Y 0 93" in sim.log
    assert "Y 0 48 111" in sim.log
    assert "Y 0 48 -111" in sim.log
    assert "Y 0 72 2" in sim.log
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_refused_set_raises(hass: HomeAssistant) -> None:
    """A set the unit refuses is reported as an error, not shown as success."""
    sim = Vp729Sim()
    entry = await setup(hass, sim)
    sim.available = lambda fn: fn != 17  # the unit now says "not available" for output gamma
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": entity_id(hass, entry, "select", "gamma"), "option": "Gamma 3"}, blocking=True
        )
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_diagnostics(hass: HomeAssistant, hass_client) -> None:
    """Diagnostics work for a VP-729 and hide the address."""
    from custom_components.kramer.diagnostics import async_get_config_entry_diagnostics

    sim = Vp729Sim()
    entry = await setup(hass, sim)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["model"] == "VP-729"
    assert result["entry"]["data"]["ip_address"] == "**REDACTED**"
    assert result["state"]["values"]["27"] == 2
    assert result["state"]["unavailable_functions"]["30"] == 3
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_config_flow_vp729(hass: HomeAssistant) -> None:
    """Choosing the VP-729 asks for its port and tests the connection."""
    sim = Vp729Sim()
    port = await sim.start()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Scaler", "ip_address": "127.0.0.1", "port": 9001, "machine": 1, "model": "vp-729"},
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "vp729"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"port": port})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["model"] == "vp-729"
    assert "machine" not in result["data"] and "inputs" not in result["data"]
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_config_flow_vp729_no_reply(hass: HomeAssistant) -> None:
    """A port that connects but is not a VP-729 gives the VP-729 hint."""
    async def silent(reader, writer):
        await asyncio.sleep(30)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Scaler", "ip_address": "127.0.0.1", "port": 1, "machine": 1, "model": "vp-729"},
    )
    import custom_components.kramer.config_flow as cf

    original = cf.async_probe_vp729

    async def quick(host, port_, timeout=0.5):
        return await original(host, port_, timeout=0.5)

    cf.async_probe_vp729 = quick
    try:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"port": port})
    finally:
        cf.async_probe_vp729 = original
        server.close()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_reply_vp729"}


async def test_reconfigure_cannot_change_type(hass: HomeAssistant) -> None:
    """A VP-729 entry cannot be turned into a matrix entry."""
    sim = Vp729Sim()
    entry = await setup(hass, sim)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Scaler", "ip_address": "127.0.0.1", "port": entry.data["port"], "machine": 1, "model": "vs-812"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "type_change"}
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()


async def test_options_flow_has_nothing_to_configure(hass: HomeAssistant) -> None:
    """The options screen explains that a VP-729 has none."""
    sim = Vp729Sim()
    entry = await setup(hass, sim)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_options"
    await hass.config_entries.async_unload(entry.entry_id)
    await sim.stop()
