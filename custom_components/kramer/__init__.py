"""Custom integration for Kramer media switches with Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_IP_ADDRESS, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .api import KramerApiClient
from .const import DOMAIN
from .coordinator import KramerDataUpdateCoordinator

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = KramerDataUpdateCoordinator(
        hass=hass,
        client=KramerApiClient(
            name=entry.data[CONF_NAME],
            ip_address=entry.data[CONF_IP_ADDRESS],
            port=entry.data.get(CONF_PORT, None),
        ),
    )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()

    # Start the listener after the first successful connection
    await coordinator.async_start_listener()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    coordinator: KramerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Stop the listener before unloading
    await coordinator.async_stop_listener()

    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
