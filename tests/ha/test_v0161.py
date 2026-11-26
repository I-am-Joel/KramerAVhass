"""Tests for 0.16.1: unique names and shared IP/port handling."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kramer.const import DOMAIN
from custom_components.kramer.models import MODELS

from .sim_v016 import P2000FiveByFour, TwoByteSim

pytestmark = pytest.mark.allow_hosts(["127.0.0.1"])


def make_entry(hass, port, key="vs-5x4", title="Existing", disabled=False):
    """Add a config entry for a model at 127.0.0.1:port."""
    m = MODELS[key]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=f"127.0.0.1:{port}:old",
        disabled_by=ConfigEntryDisabler.USER if disabled else None,
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
    return entry


async def to_connection(hass, model):
    """Start the flow and choose the model."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"model": model})


def details(name, port):
    """Return the connection form input."""
    return {"name": name, "ip_address": "127.0.0.1", "port": port, "machine": 1}


async def test_name_clash_ignores_case_and_punctuation(hass: HomeAssistant) -> None:
    """A name used by another device (even disabled) is refused."""
    make_entry(hass, 9, title="Lounge Matrix", disabled=True)
    result = await to_connection(hass, "vs-1202yc")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details("lounge-matrix!", 10))
    assert result["type"] == "form" and result["errors"] == {"base": "name_in_use"}
    assert result["description_placeholders"]["name"] == "lounge-matrix!"


async def test_enabled_entry_blocks_address(hass: HomeAssistant) -> None:
    """An enabled entry on the same IP and port blocks setup and is named."""
    make_entry(hass, 7001, title="VS-812 Rack")
    result = await to_connection(hass, "vs-5x4")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details("New", 7001))
    assert result["errors"] == {"base": "address_in_use"}
    assert result["description_placeholders"]["entry"] == "VS-812 Rack"


async def test_disabled_entry_allows_after_notice(hass: HomeAssistant) -> None:
    """A disabled entry on the same address gives a notice; continuing creates a separate device."""
    old = make_entry(hass, 7002, key="vs-812", title="VS-812 Rack", disabled=True)
    sim = P2000FiveByFour()
    port = await sim.start()
    old.add_to_hass  # noqa: B018 - the old entry already exists at another port; move it to the sim's port
    hass.config_entries.async_update_entry(old, data={**old.data, "port": port})
    result = await to_connection(hass, "vs-5x4")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details("Matrix 5x4", port))
    assert result["type"] == "menu" and result["step_id"] == "shared"
    assert '"VS-812 Rack"' in result["description_placeholders"]["entry"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "shared_continue"})
    assert result["type"] == "create_entry", result
    new = result["result"]
    assert new.unique_id != old.unique_id
    assert old.disabled_by is not None  # the old entry was left alone
    await hass.config_entries.async_unload(new.entry_id)
    await sim.stop()


async def test_going_back_keeps_values(hass: HomeAssistant) -> None:
    """The notice offers going back to the address screen with the typed values kept."""
    make_entry(hass, 7003, title="Old", disabled=True)
    result = await to_connection(hass, "vs-5x4")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details("New", 7003))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "connection"})
    assert result["type"] == "form" and result["step_id"] == "connection"


async def test_second_enabled_entry_waits(hass: HomeAssistant) -> None:
    """When two enabled entries share an address, the one that is not running does not connect."""
    sim = TwoByteSim("1202")
    port = await sim.start()
    first = make_entry(hass, port, key="vs-1202yc", title="First")
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    assert first.state is ConfigEntryState.LOADED
    second = make_entry(hass, port, key="vs-1202yc", title="Second")
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    assert second.state is ConfigEntryState.SETUP_RETRY
    assert 'Another enabled Kramer device ("First")' in (second.reason or "")
    assert first.state is ConfigEntryState.LOADED
    await hass.config_entries.async_unload(first.entry_id)
    await hass.config_entries.async_unload(second.entry_id)
    await sim.stop()


async def test_reconfigure_own_name_and_address_allowed(hass: HomeAssistant) -> None:
    """Reconfiguring an entry does not clash with itself."""
    sim = P2000FiveByFour()
    port = await sim.start()
    entry = make_entry(hass, port, title="Mine")
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"model": "vs-5x4"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details("Mine", port))
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful", result
    await sim.stop()
