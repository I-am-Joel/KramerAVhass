"""Tests for 0.17.3: brand images and the dashboard card that comes with the integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from PIL import Image

from custom_components.kramer import card
from custom_components.kramer.const import DOMAIN

FOLDER = Path(__file__).parents[2] / "custom_components" / "kramer"


def test_brand_images_are_square_png_icons() -> None:
    """HACS needs an icon.png in the brand folder; a dark version goes with it."""
    for name in ("icon.png", "dark_icon.png"):
        with Image.open(FOLDER / "brand" / name) as image:
            assert image.format == "PNG"
            assert image.size == (256, 256)
    with Image.open(FOLDER / "brand" / "icon.png") as light, Image.open(FOLDER / "brand" / "dark_icon.png") as dark:
        # The light-mode icon is dark on transparent and the dark-mode icon is the reverse.
        assert light.convert("RGBA").getpixel((0, 0))[3] == 0
        assert dark.convert("RGBA").getpixel((0, 0))[3] == 0
        assert sum(light.convert("L").getdata()) < sum(dark.convert("L").getdata())


def test_manifest_loads_after_the_frontend_and_http() -> None:
    """The card is added through the frontend and the web server, so they load first."""
    manifest = json.loads((FOLDER / "manifest.json").read_text())
    assert manifest["after_dependencies"] == ["frontend", "http"]
    assert manifest["version"] == "0.17.3"


def test_the_card_file_is_the_one_that_comes_with_the_integration() -> None:
    """The bundled card is a single file that defines its card once, and carries its font licences."""
    text = (FOLDER / "www" / card.CARD_FILE).read_text()
    assert 'CARD_VERSION = "0.1.5"' in text
    assert "if (!customElements.get(CARD_TYPE))" in text  # a second copy (from HACS) does no harm
    assert "https://" not in text  # nothing is fetched from outside
    assert (FOLDER / "www" / "FONT-LICENCES.md").exists()


async def test_the_card_is_served_once_and_added_to_the_pages(hass: HomeAssistant, hass_client) -> None:
    """The one file is served, the frontend is told to load it, and doing it again changes nothing."""
    assert await async_setup_component(hass, "http", {})
    client = await hass_client()
    added: list[str] = []
    with patch("custom_components.kramer.card.add_extra_js_url", lambda _hass, url: added.append(url)):
        await card.async_register_card(hass)
        await card.async_register_card(hass)
    assert len(added) == 1
    assert added[0].startswith(f"/{DOMAIN}/{card.CARD_FILE}?v=0.17.")
    response = await client.get(card.CARD_URL)
    assert response.status == 200
    assert (await response.read()) == card.CARD_PATH.read_bytes()
    # Only that file is served, not the folder it is in.
    assert (await client.get(f"/{DOMAIN}/FONT-LICENCES.md")).status == 404
    assert (await client.get(f"/{DOMAIN}/")).status == 404


async def test_no_web_server_is_not_an_error(hass: HomeAssistant) -> None:
    """Without a web server there is nothing to serve to: setup carries on."""
    assert getattr(hass, "http", None) is None
    with patch("custom_components.kramer.card.add_extra_js_url") as add:
        await card.async_register_card(hass)
    add.assert_not_called()


async def test_a_missing_frontend_is_logged_not_fatal(hass: HomeAssistant, hass_client, caplog) -> None:
    """If the frontend is not there to take the address, a warning is logged and setup goes on."""
    assert await async_setup_component(hass, "http", {})
    await hass_client()
    with patch("custom_components.kramer.card.add_extra_js_url", side_effect=KeyError("frontend_extra_module_url")):
        await card.async_register_card(hass)
    assert "dashboard card could not be added" in caplog.text


@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_setting_up_an_entry_registers_the_card(hass: HomeAssistant, hass_client) -> None:
    """Setting up the integration (as Home Assistant does) registers the card through async_setup."""
    from .sim import Sim  # noqa: PLC0415
    from .test_v016 import finish  # noqa: PLC0415
    from .test_v016 import setup as matrix_setup  # noqa: PLC0415

    assert await async_setup_component(hass, "http", {})
    await hass_client()
    added: list[str] = []
    sim = Sim()
    with patch("custom_components.kramer.card.add_extra_js_url", lambda _hass, url: added.append(url)):
        entry = await matrix_setup(hass, sim, "vs-162av-rca")
    assert len(added) == 1
    await finish(hass, entry, sim)
