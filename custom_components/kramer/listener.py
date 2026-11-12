"""Listener for unsolicited status messages from Kramer devices."""
from __future__ import annotations

import asyncio
import socket
from typing import Callable

from .const import LOGGER

_PACKET_SIZE = 4
_RECONNECT_DELAY = 5  # seconds between reconnection attempts
_CONNECT_TIMEOUT = 10  # seconds to wait for a connection to open
_INSTRUCTION_SWITCH_VIDEO = 0x01


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
        """Connect and reconnect indefinitely until stopped."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._running:
                    break
                LOGGER.debug(
                    "Kramer listener connection lost for %s:%s (%s)",
                    self._ip_address,
                    self._port,
                    exc,
                )
            if self._running:
                LOGGER.debug(
                    "Kramer listener retrying %s:%s in %s seconds",
                    self._ip_address,
                    self._port,
                    _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _connect_and_listen(self) -> None:
        """Open a connection and read until it drops."""
        LOGGER.debug(
            "Kramer listener connecting to %s:%s",
            self._ip_address,
            self._port,
        )
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._ip_address, self._port),
            timeout=_CONNECT_TIMEOUT,
        )
        try:
            self._enable_keepalive(writer)
            LOGGER.debug(
                "Kramer listener connected to %s:%s",
                self._ip_address,
                self._port,
            )

            buffer = bytearray()
            while self._running:
                chunk = await reader.read(64)
                if not chunk:
                    LOGGER.debug(
                        "Kramer listener: connection closed by remote "
                        "device at %s:%s",
                        self._ip_address,
                        self._port,
                    )
                    return
                buffer.extend(chunk)
                self._process_buffer(buffer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
        """Make a silently dead connection (power loss) detectable."""
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
            if hasattr(socket, "TCP_KEEPINTVL"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            if hasattr(socket, "TCP_KEEPCNT"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except OSError as exc:
            LOGGER.debug("Kramer listener: could not set keepalive (%s)", exc)

    def _process_buffer(self, buffer: bytearray) -> None:
        """
        Split the buffer into 4-byte packets. The first byte of a packet
        has its top bit clear and the other three have it set, so any
        stray bytes are discarded to stay aligned.
        """
        while buffer:
            if buffer[0] & 0x80:
                LOGGER.debug(
                    "Kramer listener: discarding stray byte 0x%02x",
                    buffer[0],
                )
                del buffer[0]
                continue
            if len(buffer) < _PACKET_SIZE:
                return
            if not all(b & 0x80 for b in buffer[1:_PACKET_SIZE]):
                LOGGER.debug(
                    "Kramer listener: discarding malformed start byte 0x%02x",
                    buffer[0],
                )
                del buffer[0]
                continue
            packet = bytes(buffer[:_PACKET_SIZE])
            del buffer[:_PACKET_SIZE]
            self._handle_packet(packet)

    def _handle_packet(self, packet: bytes) -> None:
        """
        Handle one 4-byte packet: [instruction][input][output][machine].
        Only "input N to all outputs" updates Home Assistant for now.
        """
        LOGGER.debug("Kramer listener raw packet: %s", packet.hex())

        instruction = packet[0] & 0x3F
        if instruction != _INSTRUCTION_SWITCH_VIDEO:
            LOGGER.debug(
                "Kramer listener: ignoring packet (instruction %s)",
                instruction,
            )
            return

        input_number = packet[1] & 0x7F
        output_number = packet[2] & 0x7F

        if output_number != 0:
            LOGGER.debug(
                "Kramer listener: input %s routed to output %s only "
                "(single-output routes are not tracked yet)",
                input_number,
                output_number,
            )
            return

        LOGGER.info(
            "Kramer listener: all outputs set to input %s at %s:%s",
            input_number,
            self._ip_address,
            self._port,
        )
        self._on_source_change(str(input_number))
