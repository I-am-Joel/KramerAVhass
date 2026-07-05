"""Listener for unsolicited status messages from Kramer devices."""
from __future__ import annotations

import asyncio
from typing import Callable

from .const import LOGGER

# Protocol 2000 constants
_PACKET_SIZE = 4
_RECONNECT_DELAY = 5  # seconds between reconnection attempts

class KramerListener:
    """
    Maintains a persistent TCP connection to receive unsolicited
    Protocol 2000 status messages from a Kramer device via a
    serial-to-IP bridge such as the Brainboxes ES-257.
    """

    def __init__(
        self,
        ip_address: str,
        port: int,
        on_source_change: Callable[[str], None],
    ) -> None:
        """Initialise the listener."""
        self._ip_address = ip_address
        self._port = port
        self._on_source_change = on_source_change
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the listener loop as a background task."""
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        LOGGER.info(
            "Kramer listener started for %s:%s",
            self._ip_address,
            self._port,
        )

    async def stop(self) -> None:
        """Stop the listener loop cleanly."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        LOGGER.info(
            "Kramer listener stopped for %s:%s",
            self._ip_address,
            self._port,
        )

    async def _listen_loop(self) -> None:
        """
        Outer loop: connects and reconnects indefinitely until stopped.
        All failure modes — lost power on the Kramer unit, lost power
        on the ES-257, network interruption — are handled here by
        waiting briefly and retrying.
        """
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                # Listener is being stopped intentionally
                raise
            except Exception as exc:
                if not self._running:
                    break
                LOGGER.debug(
                    "Kramer listener connection lost for %s:%s (%s) — "
                    "retrying in %s seconds",
                    self._ip_address,
                    self._port,
                    exc,
                    _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _connect_and_listen(self) -> None:
        """
        Inner loop: opens a TCP connection and reads incoming bytes.
        Exits if the connection drops, which causes the outer loop
        to reconnect.
        """
        LOGGER.debug(
            "Kramer listener connecting to %s:%s",
            self._ip_address,
            self._port,
        )
        reader, _ = await asyncio.open_connection(
            self._ip_address, self._port
        )
        LOGGER.debug(
            "Kramer listener connected to %s:%s",
            self._ip_address,
            self._port,
        )

        buffer = bytearray()

        while self._running:
            chunk = await reader.read(64)
            if not chunk:
                # Zero bytes means the remote end closed the connection
                LOGGER.debug(
                    "Kramer listener: connection closed by remote "
                    "device at %s:%s",
                    self._ip_address,
                    self._port,
                )
                return

            buffer.extend(chunk)

            # Process all complete 4-byte packets in the buffer
            while len(buffer) >= _PACKET_SIZE:
                packet = buffer[:_PACKET_SIZE]
                buffer = buffer[_PACKET_SIZE:]
                self._handle_packet(bytes(packet))

    def _handle_packet(self, packet: bytes) -> None:
        """
        Parse a 4-byte Protocol 2000 packet and call the state
        update callback if it is a valid switching status message.

        Protocol 2000 packet structure:
          Byte 0: Instruction (0x01 = video/audio switch)
          Byte 1: Input number (1-based, high bit set = 0x80 + input)
          Byte 2: Output number (high bit set = 0x80 + output)
          Byte 3: Machine number (high bit set = 0x80 + machine)
        """
        LOGGER.debug(
            "Kramer listener raw packet: %s",
            packet.hex(),
        )

        instruction = packet[0]
        if instruction != 0x01:
            # Not a switch instruction — ignore
            LOGGER.debug(
                "Kramer listener: ignoring non-switch packet "
                "(instruction byte: 0x%02x)",
                instruction,
            )
            return

        # Strip the high bit flag to get the actual input number
        input_number = packet[1] & 0x7F
        source = str(input_number)

        LOGGER.info(
            "Kramer listener: source changed to input %s at %s:%s",
            source,
            self._ip_address,
            self._port,
        )
        self._on_source_change(source)
