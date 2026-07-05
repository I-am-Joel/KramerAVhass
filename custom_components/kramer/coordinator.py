"""DataUpdateCoordinator for Kramer integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    KramerApiState,
    KramerApiClient,
    KramerApiClientError,
)
from .const import DOMAIN, LOGGER
from .listener import KramerListener


class KramerDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from devices."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: KramerApiClient,
    ) -> None:
        """Initialise."""
        self.client = client
        self._listener: KramerListener | None = None
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )

    async def async_start_listener(self) -> None:
        """Create and start the status listener."""
        entry = self.config_entry
        ip_address: str = entry.data[CONF_IP_ADDRESS]
        port: int = int(entry.data.get(CONF_PORT, 5000))

        self._listener = KramerListener(
            ip_address=ip_address,
            port=port,
            on_source_change=self._handle_source_change,
        )
        await self._listener.start()

    async def async_stop_listener(self) -> None:
        """Stop the status listener cleanly."""
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None

    def _handle_source_change(self, source: str) -> None:
        """
        Called by the listener when the Kramer device reports a
        source change. Updates the cached API state and schedules
        an immediate HA entity refresh.
        """
        self.client.update_selected_source(source)
        self.hass.loop.call_soon_threadsafe(
            self.async_set_updated_data,
            self.client.state,
        )

    async def _async_update_data(self) -> KramerApiState:
        """Update device state from the device on the poll interval."""
        try:
            return await self.hass.async_add_executor_job(self._get_data)
        except KramerApiClientError as exception:
            raise UpdateFailed(exception) from exception

    def _get_data(self) -> KramerApiState:
        """Synchronous; invoke via hass.async_add_executor_job."""
        self.client.refresh_state()
        return self.client.state
