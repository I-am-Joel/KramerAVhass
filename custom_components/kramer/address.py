"""Checks that devices have unique names and do not share an IP address and port while both are enabled."""

from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_PORT, DOMAIN


def normalise_name(name: str) -> str:
    """Return a name with case, spaces and punctuation removed, for comparing."""
    return re.sub(r"[\W_]+", "", str(name).casefold())


def _address(entry: ConfigEntry) -> tuple[str, int] | None:
    """Return (host, port) of an entry, or None if it has none."""
    try:
        return str(entry.data[CONF_HOST]).strip().casefold(), int(entry.data[CONF_PORT])
    except (KeyError, TypeError, ValueError):
        return None


def name_in_use(hass: HomeAssistant, name: str, exclude_entry_id: str | None = None) -> bool:
    """Return True if another Kramer device (enabled or disabled) already has this name."""
    wanted = normalise_name(name)
    return bool(wanted) and any(
        normalise_name(entry.title) == wanted
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != exclude_entry_id
    )


def entries_at_address(
    hass: HomeAssistant, host: str, port: int, exclude_entry_id: str | None = None
) -> list[ConfigEntry]:
    """Return the other Kramer entries that use this IP address and port."""
    wanted = (str(host).strip().casefold(), int(port))
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id != exclude_entry_id and _address(entry) == wanted
    ]


def blocking_entry(hass: HomeAssistant, entry: ConfigEntry) -> ConfigEntry | None:
    """Return the enabled entry that should keep the connection to this address, if it is not this one.

    A device that is already running keeps the address. If several are starting at once the
    oldest wins, so the result does not depend on which one happens to start first.
    """
    address = _address(entry)
    if address is None:
        return None
    others = [
        other
        for other in entries_at_address(hass, *address, exclude_entry_id=entry.entry_id)
        if other.disabled_by is None
    ]
    for other in others:
        if other.state is ConfigEntryState.LOADED:
            return other
    for other in sorted(others, key=lambda e: (e.created_at, e.entry_id)):
        if other.state is ConfigEntryState.SETUP_IN_PROGRESS and (other.created_at, other.entry_id) < (
            entry.created_at,
            entry.entry_id,
        ):
            return other
    return None
