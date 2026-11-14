"""Kramer Protocol 2000 client.

One TCP connection (for example to a serial-to-IP bridge such as a Brainboxes
ES-257) is used for everything: commands, status requests and the unsolicited
messages the unit sends when somebody uses the front panel.

This module has no Home Assistant imports so it can be tested on its own.

Packet format (four bytes, from the Kramer "Protocol 2000" document):

    byte 1: 0 D N5..N0   D = 0 to the unit, 1 from the unit; N = instruction
    byte 2: 1 I6..I0     input (or a parameter)
    byte 3: 1 O6..O0     output (or a parameter / a returned value)
    byte 4: 1 OVR x M4..M0   machine number (1 to 15)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

_LOGGER = logging.getLogger(__name__)

# Instructions used (decimal, from the Protocol 2000 table).
INSTR_RESET = 0
INSTR_SWITCH_VIDEO = 1
INSTR_STORE_VIDEO = 3
INSTR_RECALL_VIDEO = 4
INSTR_REQUEST_VIDEO_STATUS = 5
INSTR_REQUEST_AUDIO_STATUS = 6
INSTR_BREAKAWAY = 8
INSTR_REQUEST_BREAKAWAY = 11
INSTR_REQUEST_DEFINED = 15
INSTR_ERROR = 16
INSTR_STORE_AUDIO = 19
INSTR_RECALL_AUDIO = 20
INSTR_IDENTIFY = 61
INSTR_LOCK_PANEL = 30
INSTR_REQUEST_LOCK = 31

# Values of instruction 8 that the VS-162AV accepts (it answers 1 for
# breakaway whichever of VIDEO or AUDIO is selected on the front panel).
MODE_AFV = 0
MODE_BREAKAWAY = 1

# Error codes carried in the output byte of an instruction-16 reply.
# 4 and 5 are "input invalid / valid" notifications, not errors.
_ERROR_CODES = {
    0: "error",
    1: "invalid instruction",
    2: "out of range",
    3: "machine busy",
    6: "receive buffer overflow",
}
_NOTIFY_CODES = (4, 5)

_PACKET_SIZE = 4

# How often to re-check which preset slots are occupied (seconds).
PRESET_SCAN_INTERVAL = 300.0


class KramerError(Exception):
    """Base class for errors from this module."""


class KramerConnectionError(KramerError):
    """The connection to the unit (or its bridge) failed or was closed."""


class KramerTimeoutError(KramerError):
    """The unit did not answer in time."""


class KramerCommandError(KramerError):
    """The unit answered with an error packet."""


@dataclass(frozen=True)
class Packet:
    """One decoded four-byte packet."""

    instruction: int
    from_unit: bool
    input: int
    output: int
    machine: int

    def hex(self) -> str:
        """Return the packet as hex, for logging."""
        return build_packet(self.instruction, self.input, self.output, self.machine, self.from_unit).hex()


@dataclass(frozen=True)
class PresetContents:
    """What a stored setup holds: input per output, for video and audio."""

    video: dict[int, int] = field(default_factory=dict)
    audio: dict[int, int] = field(default_factory=dict)


def build_packet(
    instruction: int,
    input_value: int,
    output_value: int,
    machine: int,
    from_unit: bool = False,
) -> bytes:
    """Build a four-byte packet."""
    first = (instruction & 0x3F) | (0x40 if from_unit else 0)
    return bytes(
        [
            first,
            0x80 | (input_value & 0x7F),
            0x80 | (output_value & 0x7F),
            0x80 | (machine & 0x1F),
        ]
    )


def parse_packets(buffer: bytearray) -> list[Packet]:
    """Remove complete packets from the front of the buffer and return them.

    The first byte of a packet has bit 7 clear and the other three have it
    set, so stray bytes can be discarded to get back in step.
    """
    packets: list[Packet] = []
    while buffer:
        if buffer[0] & 0x80:
            _LOGGER.debug("Discarding stray byte 0x%02x", buffer[0])
            del buffer[0]
            continue
        if len(buffer) < _PACKET_SIZE:
            break
        if not all(b & 0x80 for b in buffer[1:_PACKET_SIZE]):
            _LOGGER.debug("Discarding malformed start byte 0x%02x", buffer[0])
            del buffer[0]
            continue
        first, second, third, fourth = buffer[:_PACKET_SIZE]
        del buffer[:_PACKET_SIZE]
        packets.append(
            Packet(
                instruction=first & 0x3F,
                from_unit=bool(first & 0x40),
                input=second & 0x7F,
                output=third & 0x7F,
                machine=fourth & 0x1F,
            )
        )
    return packets


def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
    """Make a silently dead connection detectable (for example power loss)."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        if hasattr(socket, "TCP_KEEPINTVL"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        if hasattr(socket, "TCP_KEEPCNT"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except OSError as err:
        _LOGGER.debug("Could not set TCP options: %s", err)


async def _close(writer: asyncio.StreamWriter | None) -> None:
    """Close a stream writer, ignoring errors."""
    if writer is None:
        return
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def async_probe(host: str, port: int, machine: int, timeout: float = 5.0) -> None:
    """Check that something answers a status request at host:port.

    Raises KramerConnectionError if the connection fails and
    KramerTimeoutError if connected but the unit stays silent.
    """
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError) as err:
        raise KramerConnectionError(f"Cannot connect to {host}:{port}: {err}") from err

    try:
        writer.write(build_packet(INSTR_REQUEST_VIDEO_STATUS, 0, 1, machine))
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
            for packet in parse_packets(buffer):
                if packet.from_unit and packet.machine == machine and packet.instruction == INSTR_REQUEST_VIDEO_STATUS:
                    return
    except OSError as err:
        raise KramerConnectionError(str(err)) from err
    finally:
        await _close(writer)


class KramerClient:
    """Keeps one connection to a Kramer matrix and tracks its state."""

    def __init__(
        self,
        host: str,
        port: int,
        machine: int,
        inputs: int,
        outputs: int,
        *,
        supports_modes: bool = True,
        preset_slots: int = 0,
        refresh_interval: float = 60.0,
        retry_interval: float = 10.0,
        reconnect_delay: float = 5.0,
        request_timeout: float = 1.5,
    ) -> None:
        """Initialise the client (nothing connects until async_start)."""
        self._host = host
        self._port = port
        self._machine = machine
        self._inputs = inputs
        self._outputs = outputs
        self._supports_modes = supports_modes
        self._preset_slots = preset_slots
        self._refresh_interval = refresh_interval
        self._retry_interval = retry_interval
        self._reconnect_delay = reconnect_delay
        self._request_timeout = request_timeout

        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[Callable[[Packet], bool], asyncio.Future] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._refresh_event = asyncio.Event()
        self._ready = asyncio.Event()

        self.available = False
        self.routes: dict[int, int] = {}
        self.mode: int | None = None
        self.locked: bool | None = None
        self.lock_supported: bool | None = None
        self.preset_defined: dict[int, bool] = {}
        self.identity: dict[str, object] = {}
        self.identity_checked = False
        self._identity_done = False
        self.last_reply: datetime | None = None
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None
        self._last_preset_scan = 0.0

    # ------------------------------------------------------------------
    # Public state helpers
    # ------------------------------------------------------------------

    @property
    def common_input(self) -> int | None:
        """Return the input feeding every output, or None if they differ."""
        if len(self.routes) != self._outputs:
            return None
        values = set(self.routes.values())
        if len(values) == 1:
            return values.pop()
        return None

    @property
    def inputs(self) -> int:
        """Return the number of inputs."""
        return self._inputs

    @property
    def outputs(self) -> int:
        """Return the number of outputs."""
        return self._outputs

    @property
    def preset_slots(self) -> int:
        """Return the number of stored-setup slots (0 = none)."""
        return self._preset_slots

    @property
    def connected_outputs(self) -> int | None:
        """Return how many outputs are connected to an input."""
        if len(self.routes) != self._outputs:
            return None
        return sum(1 for value in self.routes.values() if value > 0)

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for any state change; returns a remover."""
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                _LOGGER.exception("Error in state listener")

    def _set_available(self, available: bool) -> None:
        if self.available != available:
            self.available = available
            _LOGGER.debug("Unit %s:%s available=%s", self._host, self._port, available)
            self._notify()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the background connection task."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def async_wait_ready(self, timeout: float) -> bool:
        """Wait for the first successful full refresh; False on timeout."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def async_stop(self) -> None:
        """Stop the background task and close the connection."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await _close(self._writer)
        self._writer = None

    def async_request_refresh(self) -> None:
        """Ask for a full refresh as soon as possible."""
        self._refresh_event.set()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_route(self, output: int, input_number: int) -> None:
        """Route an input to one output (output 0 = all outputs).

        Input 0 disconnects the output.
        """
        if not 0 <= output <= self._outputs:
            raise KramerError(f"Output {output} is out of range")
        if not 0 <= input_number <= self._inputs:
            raise KramerError(f"Input {input_number} is out of range")
        await self._request(
            build_packet(INSTR_SWITCH_VIDEO, input_number, output, self._machine),
            INSTR_SWITCH_VIDEO,
            lambda p: p.input == input_number and p.output == output,
        )
        self._apply_route(output, input_number)
        self._notify()

    async def async_set_mode(self, mode: int) -> None:
        """Set audio-follow-video (0) or breakaway (1) mode."""
        reply = await self._request(
            build_packet(INSTR_BREAKAWAY, 0, mode, self._machine),
            INSTR_BREAKAWAY,
        )
        self.mode = reply.output
        self._notify()
        if reply.output != mode:
            raise KramerCommandError(f"The unit stayed in mode {reply.output} instead of {mode}")

    async def async_set_lock(self, locked: bool) -> None:
        """Lock or unlock the front panel."""
        value = 1 if locked else 0
        await self._request(
            build_packet(INSTR_LOCK_PANEL, value, 0, self._machine),
            INSTR_LOCK_PANEL,
            lambda p: p.input == value,
        )
        self.locked = locked
        self._notify()

    # ------------------------------------------------------------------
    # Request / reply plumbing
    # ------------------------------------------------------------------

    async def _request(
        self,
        data: bytes,
        instruction: int | tuple[int, ...],
        extra: Callable[[Packet], bool] | None = None,
        attempts: int = 2,
        quiet: bool = False,
    ) -> Packet:
        """Send a packet and wait for the unit's reply to it.

        A quiet request is a question the unit may legitimately not answer
        (feature detection), so a failure is not recorded as an error.
        """
        accepted = (instruction,) if isinstance(instruction, int) else instruction

        def matches(packet: Packet) -> bool:
            if not packet.from_unit:
                return False
            if packet.instruction == INSTR_ERROR:
                return (packet.output & 0x7F) in _ERROR_CODES
            return packet.instruction in accepted and (extra is None or extra(packet))

        async with self._lock:
            for attempt in range(attempts):
                writer = self._writer
                if writer is None or writer.is_closing():
                    raise KramerConnectionError("Not connected")
                future: asyncio.Future = asyncio.get_running_loop().create_future()
                self._pending = (matches, future)
                try:
                    writer.write(data)
                    await writer.drain()
                    reply = await asyncio.wait_for(future, self._request_timeout)
                except TimeoutError:
                    _LOGGER.debug(
                        "No reply to %s (attempt %s of %s)",
                        data.hex(),
                        attempt + 1,
                        attempts,
                    )
                    continue
                except OSError as err:
                    raise KramerConnectionError(str(err)) from err
                finally:
                    self._pending = None
                if reply.instruction == INSTR_ERROR:
                    code = reply.output & 0x7F
                    message = f"Unit reported: {_ERROR_CODES.get(code, code)}"
                    if not quiet:
                        self._record_error(message)
                    raise KramerCommandError(message)
                return reply
        if not quiet:
            self._record_error("No reply from the unit")
        raise KramerTimeoutError("No reply from the unit")

    def _record_error(self, message: str) -> None:
        """Remember the latest problem, for the diagnostic sensors."""
        self.last_error = message
        self.last_error_time = datetime.now(UTC)

    def _fail_pending(self, error: Exception) -> None:
        pending = self._pending
        if pending and not pending[1].done():
            pending[1].set_exception(error)

    # ------------------------------------------------------------------
    # Background task
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Connect, refresh and listen; reconnect forever if it drops."""
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug("Connection to %s:%s lost: %s", self._host, self._port, err)
            self._set_available(False)
            await asyncio.sleep(self._reconnect_delay)

    async def _session(self) -> None:
        """One connection from open to close."""
        _LOGGER.debug("Connecting to %s:%s", self._host, self._port)
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self._host, self._port), 10)
        self._writer = writer
        _enable_keepalive(writer)
        reader_task = asyncio.create_task(self._read_loop(reader))
        try:
            while True:
                try:
                    await self._refresh()
                except KramerTimeoutError:
                    _LOGGER.debug("Unit is not answering; will retry")
                    self._set_available(False)
                except KramerCommandError as err:
                    _LOGGER.warning("Unit rejected a status request: %s", err)
                    self._set_available(False)

                interval = self._refresh_interval if self.available else self._retry_interval
                self._refresh_event.clear()
                waiter = asyncio.create_task(self._refresh_event.wait())
                try:
                    await asyncio.wait(
                        {reader_task, waiter},
                        timeout=interval,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    waiter.cancel()
                if reader_task.done():
                    reader_task.result()
                    raise KramerConnectionError("Connection closed")
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader_task
            self._fail_pending(KramerConnectionError("Connection closed"))
            await _close(writer)
            self._writer = None

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Read packets until the connection closes."""
        buffer = bytearray()
        while True:
            chunk = await reader.read(64)
            if not chunk:
                raise KramerConnectionError("Connection closed by the other end")
            buffer.extend(chunk)
            for packet in parse_packets(buffer):
                self._dispatch(packet)

    def _dispatch(self, packet: Packet) -> None:
        """Hand a packet to whoever is waiting for it, or treat it as an event."""
        _LOGGER.debug("Received %s", packet.hex())
        if packet.machine != self._machine or not packet.from_unit:
            return
        self.last_reply = datetime.now(UTC)
        pending = self._pending
        if pending and not pending[1].done() and pending[0](packet):
            pending[1].set_result(packet)
            return
        self._handle_event(packet)

    def _handle_event(self, packet: Packet) -> None:
        """Handle a message the unit sent on its own (front-panel use)."""
        instruction = packet.instruction
        if instruction == INSTR_SWITCH_VIDEO:
            self._apply_route(packet.output, packet.input)
            self._notify()
        elif instruction == INSTR_BREAKAWAY and packet.input == 0:
            self.mode = packet.output
            self._notify()
        elif instruction == INSTR_LOCK_PANEL:
            self.locked = packet.input == 1
            self._notify()
        elif instruction == INSTR_RESET:
            _LOGGER.debug("Unit sent a reset code; refreshing")
            self._refresh_event.set()
        elif instruction == INSTR_ERROR:
            code = packet.output
            if code in _ERROR_CODES:
                _LOGGER.warning("Unit reported: %s", _ERROR_CODES[code])
            elif code not in _NOTIFY_CODES:
                _LOGGER.debug("Unit sent error packet with code %s", code)
        else:
            _LOGGER.debug("Ignoring packet %s", packet.hex())

    def _apply_route(self, output: int, input_number: int) -> None:
        """Record a route (output 0 means every output)."""
        if output == 0:
            self.routes = dict.fromkeys(range(1, self._outputs + 1), input_number)
        elif 1 <= output <= self._outputs:
            self.routes[output] = input_number

    async def _refresh(self) -> None:
        """Read the whole state: every output, the mode and the lock."""
        routes: dict[int, int] = {}
        for output in range(1, self._outputs + 1):
            reply = await self._request(
                build_packet(INSTR_REQUEST_VIDEO_STATUS, 0, output, self._machine),
                INSTR_REQUEST_VIDEO_STATUS,
            )
            routes[output] = reply.output

        mode = self.mode
        if self._supports_modes:
            try:
                reply = await self._request(
                    build_packet(INSTR_REQUEST_BREAKAWAY, 0, 0, self._machine),
                    INSTR_REQUEST_BREAKAWAY,
                )
                mode = reply.output
            except KramerCommandError:
                _LOGGER.debug("Unit does not report its mode")

        locked = self.locked
        if self.lock_supported is not False:
            try:
                reply = await self._request(
                    build_packet(INSTR_REQUEST_LOCK, 0, 0, self._machine),
                    INSTR_REQUEST_LOCK,
                    quiet=not self.lock_supported,
                )
            except (KramerCommandError, KramerTimeoutError):
                if self.lock_supported:
                    raise
                # Never answered, though the route reads just worked: this unit
                # does not support the lock instructions.
                _LOGGER.debug("Unit does not report its lock state")
                self.lock_supported = False
            else:
                locked = reply.output == 1
                self.lock_supported = True

        self.routes = routes
        self.mode = mode
        self.locked = locked
        self._ready.set()
        if not self.available:
            self._set_available(True)
        else:
            self._notify()
        if not self._identity_done:
            await self._read_identity()
        await self._scan_presets_if_due()

    async def _identify(self, what: int, which: int) -> Packet:
        """Ask the identify instruction one question (see note 13 of the protocol document)."""
        return await self._request(build_packet(INSTR_IDENTIFY, what, which, self._machine), INSTR_IDENTIFY, quiet=True)

    async def _read_identity(self) -> None:
        """Read the unit's name and software versions once per connection, if it answers.

        On the VS-162AV/RCA the prefix questions return the same letters as the
        suffix question, so only the number and the first suffix are asked for.
        """
        info: dict[str, object] = {}
        try:
            reply = await self._identify(1, 0)
            info["name_raw"] = [reply.input, reply.output]
            info["number"] = f"{reply.input:02d}{reply.output:02d}".lstrip("0")
            reply = await self._identify(1, 1)
            info["suffix"] = "".join(chr(b) for b in (reply.input, reply.output) if 32 < b < 127)
            reply = await self._identify(3, 0)
            info["firmware_raw"] = [reply.input, reply.output]
            info["firmware"] = f"{reply.input}.{reply.output}"
            reply = await self._identify(4, 0)
            info["audio_firmware"] = f"{reply.input}.{reply.output}"
        except (KramerTimeoutError, KramerCommandError) as err:
            _LOGGER.debug("Unit did not answer every identity question: %s", err)
        self.identity = info
        self.identity_checked = True
        self._identity_done = True
        self._notify()

    async def _scan_presets_if_due(self, force: bool = False) -> None:
        """Check which preset slots are occupied, at most every few minutes."""
        if self._preset_slots <= 0:
            return
        now = asyncio.get_running_loop().time()
        if not force and self._last_preset_scan and now - self._last_preset_scan < PRESET_SCAN_INTERVAL:
            return
        found: dict[int, bool] = {}
        try:
            for slot in range(1, self._preset_slots + 1):
                found[slot] = await self.async_preset_defined(slot)
        except KramerError as err:
            _LOGGER.debug("Preset scan stopped at preset %s: %s", len(found) + 1, err)
            return
        self._last_preset_scan = now
        self.preset_defined = found
        self._notify()

    # ------------------------------------------------------------------
    # Stored setups (presets)
    # ------------------------------------------------------------------

    async def async_scan_presets(self) -> None:
        """Re-check which slots are occupied now (about a second or two)."""
        await self._scan_presets_if_due(force=True)

    def _check_slot(self, slot: int) -> None:
        if self._preset_slots <= 0:
            raise KramerCommandError("This model has no presets")
        if not 1 <= slot <= self._preset_slots:
            raise KramerCommandError(f"Preset {slot} is out of range (1 to {self._preset_slots})")

    async def async_preset_defined(self, slot: int) -> bool:
        """Ask the unit whether a stored setup exists in a slot."""
        self._check_slot(slot)
        reply = await self._request(
            build_packet(INSTR_REQUEST_DEFINED, slot, 0, self._machine),
            INSTR_REQUEST_DEFINED,
            lambda p: p.input == slot,
        )
        return reply.output == 1

    async def async_read_preset(self, slot: int) -> PresetContents:
        """Read what a stored setup holds (32 requests for 16 outputs)."""
        self._check_slot(slot)
        video: dict[int, int] = {}
        audio: dict[int, int] = {}
        for output in range(1, self._outputs + 1):
            reply = await self._request(
                build_packet(INSTR_REQUEST_VIDEO_STATUS, slot, output, self._machine),
                INSTR_REQUEST_VIDEO_STATUS,
            )
            video[output] = reply.output
            reply = await self._request(
                build_packet(INSTR_REQUEST_AUDIO_STATUS, slot, output, self._machine),
                INSTR_REQUEST_AUDIO_STATUS,
            )
            audio[output] = reply.output
        return PresetContents(video, audio)

    async def async_store_preset(self, slot: int, audio_only: bool = False) -> None:
        """Store the present routing in a slot, overwriting whatever is there.

        Callers must have asked for confirmation first. Storing video saves
        video, audio and mode together; audio-only saves just the audio routes.
        """
        self._check_slot(slot)
        instruction = INSTR_STORE_AUDIO if audio_only else INSTR_STORE_VIDEO
        await self._request(
            build_packet(instruction, slot, 0, self._machine),
            instruction,
            lambda p: p.input == slot,
        )
        self.preset_defined[slot] = True
        self._notify()

    async def async_recall_preset(self, slot: int, audio_only: bool = False) -> None:
        """Recall a stored setup, then re-read everything the unit now holds."""
        if not await self.async_preset_defined(slot):
            raise KramerCommandError(f"Preset {slot} is blank")
        instruction = INSTR_RECALL_AUDIO if audio_only else INSTR_RECALL_VIDEO
        # The unit acknowledges an audio recall with the video recall code.
        await self._request(
            build_packet(instruction, slot, 0, self._machine),
            (INSTR_RECALL_VIDEO, INSTR_RECALL_AUDIO),
            lambda p: p.input == slot,
        )
        await self._refresh()
