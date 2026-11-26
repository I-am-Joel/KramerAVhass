"""Kramer 2-byte protocol client (VS-1202YC and VS-5x4 with DIP switch 5 ON).

Every message is two bytes:

    byte 1: 0 N6..N3 N2..N0   N6..N3 = the model's reply signature (0 in commands), N2..N0 = machine number - 1
    byte 2: 1 0 F V4..V0      F = 1 for status and level codes, V = the route value (or code)

Rules learned from the real units:
- Commands must be sent one at a time; send the next only after the unit answers A2 (success) or A3.
- A unit never answers for another machine number, and never gets a command for one.
- Front-panel changes are announced as single packets in the same format as status packets.

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from .protocol import (
    KramerClient,
    KramerCommandError,
    KramerConnectionError,
    KramerError,
    KramerTimeoutError,
    _close,
)

_LOGGER = logging.getLogger(__name__)

SUCCESS = 0xA2
FAILURE = 0xA3
STATUS_REQUEST = 0xA1
LEVEL_BASE = 0xA4  # first level code; 4 per output, then 0/2/4/6 dB as 0..3
LEVEL_STEPS = (0, 2, 4, 6)
MAX_BUFFER = 64


class TwoByteSpec:
    """What differs between the models that talk the 2-byte protocol."""

    def __init__(
        self,
        reply_id: int,
        inputs: int,
        outputs: int,
        has_all: bool,
        has_off: bool,
        levels: bool,
        off_command_base: int = 25,
        off_all_command: int = 30,
        off_status_base: int = 20,
    ) -> None:
        """Store the model's facts.

        The last three say how an output is switched off: the code to send is the base plus the
        output number, the code that switches every output off, and the base of the code the
        unit reports for an output that is off.
        """
        self.off_command_base = off_command_base
        self.off_all_command = off_all_command
        self.off_status_base = off_status_base
        self.reply_id = reply_id
        self.inputs = inputs
        self.outputs = outputs
        self.has_all = has_all
        self.has_off = has_off
        self.levels = levels

    @property
    def status_packets(self) -> int:
        """Return how many packets a full status reply holds."""
        return self.outputs * (2 if self.levels else 1)

    def route_value(self, output: int, input_number: int) -> int:
        """Return the code that routes an input to an output."""
        if self.has_all:  # VS-5x4
            return (input_number - 1) * self.outputs + output
        return 2 * input_number + output - 2  # VS-1202YC

    def decode_route(self, value: int) -> tuple[int, int] | None:
        """Return (output, input) for a route value, or None if it is not a plain route."""
        if self.has_all:
            if 1 <= value <= self.inputs * self.outputs:
                return (value - 1) % self.outputs + 1, (value - 1) // self.outputs + 1
            return None
        if 1 <= value <= 2 * self.inputs:
            output = 1 if value % 2 else 2
            return output, (value - output + 2) // 2
        return None


def _first_byte(machine: int, reply_id: int = 0) -> int:
    """Return byte 1 for a given machine number."""
    return (reply_id << 3) | ((machine - 1) & 7)


async def async_probe_two_byte(host: str, port: int, machine: int, reply_id: int, timeout: float = 5.0) -> None:
    """Check that a unit answers a status request in the 2-byte protocol.

    Raises KramerConnectionError if the connection fails and KramerTimeoutError
    if connected but the unit stays silent.
    """
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError) as err:
        raise KramerConnectionError(f"Cannot connect to {host}:{port}: {err}") from err
    try:
        writer.write(bytes([_first_byte(machine), STATUS_REQUEST]))
        await writer.drain()
        buffer = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise KramerTimeoutError("No reply from the unit")
            try:
                chunk = await asyncio.wait_for(reader.read(64), remaining)
            except TimeoutError as err:
                raise KramerTimeoutError("No reply from the unit") from err
            if not chunk:
                raise KramerConnectionError("Connection closed by the other end")
            buffer.extend(chunk)
            if _extract(buffer, machine, reply_id):
                return
    except OSError as err:
        raise KramerConnectionError(str(err)) from err
    finally:
        await _close(writer)


def _extract(buffer: bytearray, machine: int, reply_id: int) -> list[int]:
    """Take complete packets for this unit out of the buffer and return their second bytes.

    Packets are framed by the high bit (first byte 0, second byte 1). Anything that
    does not carry this model's reply signature and machine number is dropped.
    """
    found: list[int] = []
    wanted = _first_byte(machine, reply_id)
    while buffer:
        if buffer[0] & 0x80:
            del buffer[0]  # an orphaned second byte
            continue
        if len(buffer) < 2:
            break
        if not buffer[1] & 0x80:
            del buffer[0]  # not a packet start
            continue
        first, second = buffer[0], buffer[1]
        del buffer[:2]
        if first == wanted:
            found.append(second)
    if len(buffer) > MAX_BUFFER:
        del buffer[:-MAX_BUFFER]
    return found


class KramerTwoByteClient(KramerClient):
    """Keeps one connection to a 2-byte protocol matrix and tracks its state."""

    def __init__(
        self,
        host: str,
        port: int,
        machine: int,
        spec: TwoByteSpec,
        *,
        audio_levels: tuple[int, ...] = (),
        refresh_interval: float = 60.0,
        retry_interval: float = 10.0,
        reconnect_delay: float = 5.0,
        request_timeout: float = 1.5,
    ) -> None:
        """Initialise the client (nothing connects until async_start)."""
        super().__init__(
            host,
            port,
            machine,
            spec.inputs,
            spec.outputs,
            supports_modes=False,
            supports_lock=False,
            audio_levels=audio_levels,
            refresh_interval=refresh_interval,
            retry_interval=retry_interval,
            reconnect_delay=reconnect_delay,
            request_timeout=request_timeout,
        )
        self._spec = spec
        self.audio_supported = False
        self.identity_checked = True
        self._identity_done = True
        self._collector: tuple[list[int], int, asyncio.Future] | None = None
        self._waiter: asyncio.Future | None = None

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_route(self, output: int, input_number: int) -> None:
        """Route an input to one output (output 0 = all outputs; input 0 = disconnect, where allowed)."""
        spec = self._spec
        if not 0 <= output <= spec.outputs:
            raise KramerError(f"Output {output} is out of range")
        if not 0 <= input_number <= spec.inputs:
            raise KramerError(f"Input {input_number} is out of range")
        if input_number == 0:
            if not spec.has_off:
                raise KramerError("This unit cannot disconnect an output")
            value = spec.off_all_command if output == 0 else spec.off_command_base + output
        elif output == 0:
            if spec.has_all:
                value = 20 + input_number
            else:
                # No "all" command on this model: send each output in turn, one command at a time.
                for each in range(1, spec.outputs + 1):
                    await self.async_set_route(each, input_number)
                return
        else:
            value = spec.route_value(output, input_number)
        await self._command(0x80 | value)
        self._apply_route(output, input_number)
        self._notify()

    async def async_set_audio_route(self, output: int, input_number: int) -> None:
        """Audio always follows video on these units."""
        await self.async_set_route(output, input_number)

    async def async_set_level(self, output: int, level: int) -> None:
        """Set one output's audio level (dB)."""
        if not self._audio_levels:
            raise KramerError("This unit has no audio level setting")
        if not 1 <= output <= self._outputs:
            raise KramerError(f"Output {output} is out of range")
        if level not in self._audio_levels or level not in LEVEL_STEPS:
            raise KramerError(f"Level {level} dB is not available")
        code = LEVEL_BASE + (output - 1) * 4 + LEVEL_STEPS.index(level)
        await self._command(code)
        self.levels = {**self.levels, output: level}
        self._notify()

    async def async_set_mode(self, mode: int) -> None:
        """Not available on these units."""
        raise KramerError("This unit has no routing modes")

    async def async_set_lock(self, locked: bool) -> None:
        """Not available on these units."""
        raise KramerError("This unit has no panel lock")

    # ------------------------------------------------------------------
    # Request / reply plumbing
    # ------------------------------------------------------------------

    async def _command(self, code: int) -> None:
        """Send one command and wait for the unit's success reply."""
        async with self._lock:
            writer = self._writer
            if writer is None or writer.is_closing():
                raise KramerConnectionError("Not connected")
            for attempt in range(2):
                future: asyncio.Future = asyncio.get_running_loop().create_future()
                self._waiter = future
                try:
                    writer.write(bytes([_first_byte(self._machine), code]))
                    await writer.drain()
                    reply = await asyncio.wait_for(future, self._request_timeout)
                except TimeoutError:
                    _LOGGER.debug("No reply to command %02X (attempt %s of 2)", code, attempt + 1)
                    continue
                except OSError as err:
                    raise KramerConnectionError(str(err)) from err
                finally:
                    self._waiter = None
                if reply == SUCCESS:
                    return
                self._record_error("Unit reported: command not accepted")
                raise KramerCommandError("Unit reported: command not accepted")
        self._record_error("No reply from the unit")
        raise KramerTimeoutError("No reply from the unit")

    async def _collect(self, count: int) -> list[int]:
        """Ask for the full status and gather its packets."""
        async with self._lock:
            writer = self._writer
            if writer is None or writer.is_closing():
                raise KramerConnectionError("Not connected")
            for attempt in range(2):
                future: asyncio.Future = asyncio.get_running_loop().create_future()
                packets: list[int] = []
                self._collector = (packets, count, future)
                try:
                    writer.write(bytes([_first_byte(self._machine), STATUS_REQUEST]))
                    await writer.drain()
                    return await asyncio.wait_for(future, self._request_timeout)
                except TimeoutError:
                    _LOGGER.debug("Incomplete status reply (%s of %s packets, attempt %s)", len(packets), count, attempt + 1)
                    continue
                except OSError as err:
                    raise KramerConnectionError(str(err)) from err
                finally:
                    self._collector = None
        self._record_error("No reply from the unit")
        raise KramerTimeoutError("No reply from the unit")

    def _fail_pending(self, error: Exception) -> None:
        for future in (self._waiter, self._collector[2] if self._collector else None):
            if future and not future.done():
                future.set_exception(error)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Read packets until the connection closes."""
        buffer = bytearray()
        while True:
            chunk = await reader.read(64)
            if not chunk:
                raise KramerConnectionError("Connection closed by the other end")
            buffer.extend(chunk)
            for second in _extract(buffer, self._machine, self._spec.reply_id):
                self._handle_packet(second)

    def _handle_packet(self, second: int) -> None:
        """Hand a packet to whoever is waiting for it, or treat it as an announcement."""
        self.last_reply = datetime.now(UTC)
        if second in (SUCCESS, FAILURE):
            waiter = self._waiter
            if waiter and not waiter.done():
                waiter.set_result(second)
            return
        collector = self._collector
        if collector and not collector[2].done():
            collector[0].append(second)
            if len(collector[0]) >= collector[1]:
                collector[2].set_result(list(collector[0]))
            return
        self._announce(second)

    def _announce(self, second: int) -> None:
        """Apply a packet the unit sent on its own (front-panel use)."""
        spec = self._spec
        if self._audio_levels and LEVEL_BASE <= second < LEVEL_BASE + spec.outputs * 4:
            index = second - LEVEL_BASE
            self.levels = {**self.levels, index // 4 + 1: LEVEL_STEPS[index % 4]}
            self._notify()
            return
        if second & 0xE0 != 0x80:
            _LOGGER.debug("Ignoring packet %02X", second)
            return
        value = second & 0x1F
        route = spec.decode_route(value)
        if route:
            self._apply_route(*route)
            self._notify()
        else:
            # ALL, OFF and anything else: read the whole state again rather than guess.
            self._refresh_event.set()

    async def _refresh(self) -> None:
        """Read the whole state with one status request."""
        spec = self._spec
        packets = await self._collect(spec.status_packets)
        routes: dict[int, int] = {}
        levels = dict(self.levels)
        for position, second in enumerate(packets[: spec.outputs], start=1):
            value = second & 0x1F
            route = spec.decode_route(value)
            if route and route[0] == position:
                routes[position] = route[1]
            elif spec.has_off and spec.off_status_base + 1 <= value <= spec.off_status_base + spec.outputs:
                routes[position] = 0  # an output that is switched off shows as the base + its position
            elif route:
                routes[route[0]] = route[1]
        if spec.levels:
            for second in packets[spec.outputs :]:
                if LEVEL_BASE <= second < LEVEL_BASE + spec.outputs * 4:
                    index = second - LEVEL_BASE
                    levels[index // 4 + 1] = LEVEL_STEPS[index % 4]
        if len(routes) != spec.outputs:
            raise KramerCommandError("Unexpected status reply")
        self.routes = routes
        self.audio_routes = {}
        self.levels = levels
        self._ready.set()
        if not self.available:
            self._set_available(True)
        else:
            self._notify()

    async def async_stop(self) -> None:
        """Stop and close the connection."""
        await super().async_stop()
        with contextlib.suppress(Exception):
            self._fail_pending(KramerConnectionError("Stopped"))
