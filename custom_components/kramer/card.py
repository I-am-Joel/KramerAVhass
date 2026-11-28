"""Serve the dashboard card that comes with the integration and have Home Assistant load it."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_FILE = "kramerav-matrix-panel-card.js"
CARD_PATH = Path(__file__).parent / "www" / CARD_FILE
CARD_URL = f"/{DOMAIN}/{CARD_FILE}"
_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card's one file and add it to the pages Home Assistant loads.

    Nothing is written to the user's dashboard settings: the card is loaded by Home Assistant
    itself, so it works in storage and YAML dashboards and goes when the integration is removed.
    Only that one file is served. The version is added to the address so that a new card is
    fetched after an update, not taken from the browser's cache.
    """
    if hass.data.get(_REGISTERED):
        return
    http = getattr(hass, "http", None)
    if http is None:
        _LOGGER.debug("No web server here: not serving the dashboard card")
        return
    hass.data[_REGISTERED] = True
    await http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_PATH), True)])
    integration = await async_get_integration(hass, DOMAIN)
    try:
        add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    except KeyError:
        _LOGGER.warning("The dashboard card could not be added: the frontend is not loaded")
