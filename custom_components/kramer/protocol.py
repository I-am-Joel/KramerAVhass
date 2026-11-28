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
INSTR_SWITCH_AUDIO = 2
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
INSTR_SET_LEVEL = 22  # set (or, from the unit, announce) an output's audio level
INSTR_REQUEST_LEVEL = 25  # ask for an output's audio level
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

FRONT_PANEL_BUSY_MESSAGE = (
    "The device is waiting for an entry on its front panel (a button is flashing) so it cannot accept commands now. "
    "Press the flashing button again to cancel the entry, then try again."
)
BUSY_CODE = 3  # the error code a model answers with while a front-panel entry is waiting (see KramerClient)
QUARANTINE_AFTER = 2  # a refresh read that got no answer this many times in a row is left out
BUSY_INTERVAL = 3.0  # seconds between attempts while the device is waiting for a front-panel entry

_PACKET_SIZE = 4

# How often to re-check which preset slots are filled (seconds).
PRESET_SCAN_INTERVAL = 300.0


class KramerError(Exception):
    """Base class for errors from this module."""


class KramerConnectionError(KramerError):
    """The connection to the unit (or its bridge) failed or was closed."""


class KramerTimeoutError(KramerError):
    """The unit did not answer in time."""


class KramerCommandError(KramerError):
    """The unit answered with an error packet."""


def say_name(name: str) -> str:
    """Put a setting's name in the middle of a sentence ("Brightness" becomes "brightness", "OSD timeout" is kept)."""
    name = name.split(" (")[0]
    return name if name[:2].isupper() else name[:1].lower() + name[1:]


def _outputs_text(output: int) -> str:
    """Say which output(s) in words (output 0 means every output)."""
    return "all outputs" if output == 0 else f"output {output}"


class KramerBusyError(KramerCommandError):
    """The device is waiting for an entry on its front panel and refuses commands."""


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


class _IdentityDone(Exception):
    """Stops the identity questions early (internal)."""


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
        supports_lock: bool = True,
        audio_levels: tuple[int, ...] = (),
        retry_error: bool = False,
        flash_busy: bool = False,
    ) -> None:
        """Initialise the client (nothing connects until async_start).

        With `flash_busy` the model answers every command with error 3 while a front-panel
        button is flashing, so that answer is treated as "busy" and not as a fault.
        """
        self._flash_busy = flash_busy
        self._audio_levels = tuple(audio_levels)
        self._busy_wait = 1.0
        self._retry_error = retry_error
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
        self._pending_what: str | None = None
        self._answers = 0  # replies received since the refresh began (shows the unit is alive)
        self._strikes: dict[str, int] = {}  # refresh reads that got no answer, in a row
        self.quarantined: set[str] = set()  # refresh reads left out because they never get an answer
        self.unmatched: dict[str, str] = {}  # the last packet that was not the answer, by request
        self._listeners: list[Callable[[], None]] = []
        self._refresh_event = asyncio.Event()
        self._ready = asyncio.Event()

        self.available = False
        self.busy = False
        self.busy_supported = flash_busy
        self.routes: dict[int, int] = {}
        self.audio_routes: dict[int, int] = {}
        self.mode: int | None = None
        self.locked: bool | None = None
        self.lock_supported: bool | None = None if supports_lock else False
        self.levels: dict[int, int] = {}
        self.audio_supported: bool | None = None
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
    def common_all(self) -> int | None:
        """Return the input feeding every output for video and audio, or None if anything differs."""
        video = self.common_input
        if video is None:
            return None
        if not self._supports_modes or not self.audio_routes:
            return video
        if len(self.audio_routes) == self._outputs and set(self.audio_routes.values()) == {video}:
            return video
        return None

    def in_step(self, output: int) -> bool | None:
        """Return whether an output's audio comes from the same input as its video (None if unknown)."""
        if not self._supports_modes:
            return None
        video = self.routes.get(output)
        audio = self.audio_routes.get(output)
        if video is None or audio is None:
            return None
        return video == audio

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

    @property
    def refresh_interval(self) -> float:
        """Return the seconds between full refreshes."""
        return self._refresh_interval

    def set_refresh_interval(self, seconds: float) -> None:
        """Change the refresh interval; takes effect straight away."""
        self._refresh_interval = float(seconds)
        self._refresh_event.set()
        self._notify()

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
            what=(
                f"disconnecting the video of {_outputs_text(output)}"
                if input_number == 0
                else f"setting the video of {_outputs_text(output)} to input {input_number}"
            ),
        )
        self._apply_route(output, input_number)
        if self._supports_modes and self.mode == MODE_AFV:
            self._apply_audio_route(output, input_number)
        self._notify()

    async def async_set_audio_route(self, output: int, input_number: int) -> None:
        """Route an input's audio to one output (output 0 = all outputs).

        In Audio follows video mode the unit moves video as well, so callers
        should only use this in breakaway mode.
        """
        if not 0 <= output <= self._outputs:
            raise KramerError(f"Output {output} is out of range")
        if not 0 <= input_number <= self._inputs:
            raise KramerError(f"Input {input_number} is out of range")
        await self._request(
            build_packet(INSTR_SWITCH_AUDIO, input_number, output, self._machine),
            INSTR_SWITCH_AUDIO,
            lambda p: p.input == input_number and p.output == output,
            what=(
                f"disconnecting the audio of {_outputs_text(output)}"
                if input_number == 0
                else f"setting the audio of {_outputs_text(output)} to input {input_number}"
            ),
        )
        self._apply_audio_route(output, input_number)
        if self.mode == MODE_AFV:
            self._apply_route(output, input_number)
        self._notify()

    async def async_set_all_layers(self, output: int, input_number: int) -> None:
        """Route an input to an output for video and, if the unit is in breakaway mode, audio too."""
        await self.async_set_route(output, input_number)
        if self._supports_modes and self.mode != MODE_AFV:
            await self.async_set_audio_route(output, input_number)

    async def async_set_mode(self, mode: int) -> None:
        """Set audio-follow-video (0) or breakaway (1) mode."""
        reply = await self._request(
            build_packet(INSTR_BREAKAWAY, 0, mode, self._machine),
            INSTR_BREAKAWAY,
            what="setting the routing mode to " + ("Audio follows video" if mode == MODE_AFV else "breakaway"),
        )
        self.mode = reply.output
        if self.mode == MODE_AFV:
            # The unit makes audio follow video at once; read it back to be sure.
            self.audio_routes = dict(self.routes)
            self._refresh_event.set()
        self._notify()
        if reply.output != mode:
            raise KramerCommandError(f"The unit stayed in mode {reply.output} instead of {mode}")

    @property
    def audio_levels(self) -> tuple[int, ...]:
        """Return the output levels (dB) the unit can be set to; empty if it has none."""
        return self._audio_levels

    async def async_set_level(self, output: int, level: int) -> None:
        """Set one output's audio level (in dB, one of the model's allowed values)."""
        if not self._audio_levels:
            raise KramerError("This unit has no audio level setting")
        if not 1 <= output <= self._outputs:
            raise KramerError(f"Output {output} is out of range")
        if level not in self._audio_levels:
            raise KramerError(f"Level {level} dB is not available")
        # The unit does no range checking of its own, so only validated values are ever sent.
        await self._request(
            build_packet(INSTR_SET_LEVEL, output, level, self._machine),
            INSTR_SET_LEVEL,
            lambda p: p.input == output,
            what=f"setting the audio level of output {output} to {level} dB",
        )
        self.levels = {**self.levels, output: level}
        self._notify()

    async def async_set_lock(self, locked: bool) -> None:
        """Lock or unlock the front panel."""
        value = 1 if locked else 0
        await self._request(
            build_packet(INSTR_LOCK_PANEL, value, 0, self._machine),
            INSTR_LOCK_PANEL,
            lambda p: p.input == value,
            what="locking the front panel" if locked else "unlocking the front panel",
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
        what: str | None = None,
    ) -> Packet:
        """Send a packet and wait for the unit's reply to it.

        A quiet request is a question the unit may legitimately not answer
        (feature detection), so a failure is not recorded as an error. ``what`` says in
        words what the request was for ("reading the panel lock"), and is added to the
        error messages.
        """
        suffix = f" ({what})" if what else ""
        accepted = (instruction,) if isinstance(instruction, int) else instruction

        def matches(packet: Packet) -> bool:
            if not packet.from_unit:
                return False
            if packet.instruction == INSTR_ERROR:
                return (packet.output & 0x7F) in _ERROR_CODES
            return packet.instruction in accepted and (extra is None or extra(packet))

        async with self._lock:
            total = attempts + (3 if self._retry_error else 0)
            for attempt in range(total):
                writer = self._writer
                if writer is None or writer.is_closing():
                    raise KramerConnectionError("Not connected")
                future: asyncio.Future = asyncio.get_running_loop().create_future()
                self._pending = (matches, future)
                self._pending_what = what
                try:
                    writer.write(data)
                    await writer.drain()
                    reply = await asyncio.wait_for(future, self._request_timeout)
                except TimeoutError:
                    _LOGGER.debug(
                        "No reply to %s (attempt %s of %s)",
                        data.hex(),
                        attempt + 1,
                        total,
                    )
                    continue
                except OSError as err:
                    raise KramerConnectionError(str(err)) from err
                finally:
                    self._pending = None
                if reply.instruction == INSTR_ERROR:
                    code = reply.output & 0x7F
                    if code == 0 and self._retry_error and attempt < total - 1:
                        # Answered while someone is using the front panel: wait and ask again.
                        await asyncio.sleep(self._busy_wait)
                        continue
                    if code == BUSY_CODE and self._flash_busy:
                        self._set_busy(True)
                        if not quiet:
                            self._record_error(FRONT_PANEL_BUSY_MESSAGE)
                        raise KramerBusyError(FRONT_PANEL_BUSY_MESSAGE)
                    message = f"Unit reported: {_ERROR_CODES.get(code, code)}{suffix}"
                    if not quiet:
                        self._record_error(message)
                    raise KramerCommandError(message)
                self._set_busy(False)
                self._answers += 1
                return reply
        if not quiet:
            self._record_error(f"No reply from the unit{suffix}")
        raise KramerTimeoutError(f"No reply from the unit{suffix}")

    async def _read(
        self,
        key: str,
        data: bytes,
        instruction: int | tuple[int, ...],
        extra: Callable[[Packet], bool] | None = None,
        *,
        what: str,
        quiet: bool = False,
    ) -> Packet | None:
        """Ask for one value of the refresh; None if this read is being left out.

        A read that gets no answer QUARANTINE_AFTER refreshes in a row is left out from then on
        (and noted in the log and the diagnostics), so one question the unit never answers cannot
        keep the whole unit unavailable. It only counts when the unit has answered something else
        in the same refresh: a unit that is switched off answers nothing, and must stay unavailable.
        """
        if self._strikes.get(key, 0) >= QUARANTINE_AFTER:
            if key not in self.quarantined:
                self.quarantined.add(key)
                _LOGGER.warning(
                    "The unit did not answer %s %d times in a row: leaving it out of the readings", what, QUARANTINE_AFTER
                )
            return None
        try:
            reply = await self._request(data, instruction, extra, quiet=quiet, what=what)
        except KramerTimeoutError:
            if self._answers and not quiet:
                self._strikes[key] = self._strikes.get(key, 0) + 1
            raise
        self._strikes.pop(key, None)
        self.quarantined.discard(key)
        return reply

    def _set_busy(self, busy: bool) -> None:
        """Note whether the device is waiting for a front-panel entry, telling listeners if that changed."""
        if self.busy != busy:
            self.busy = busy
            if busy:
                # Wake the refresh loop so it starts retrying at the short busy interval.
                self._refresh_event.set()
            self._notify()

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
                except KramerBusyError:
                    _LOGGER.debug("Unit is waiting for a front-panel entry; keeping the last values and trying again")
                except KramerCommandError as err:
                    if self._retry_error and self.available:
                        _LOGGER.debug("Unit is busy or refused a status request: %s", err)
                    else:
                        _LOGGER.warning("Unit rejected a status request: %s", err)
                        self._set_available(False)

                if self.busy:
                    interval = BUSY_INTERVAL
                else:
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
        if packet.machine != self._machine:
            return
        # The VS-808YC announces front-panel lock changes without the "from the unit" marker.
        if not packet.from_unit and packet.instruction != INSTR_LOCK_PANEL:
            return
        self.last_reply = datetime.now(UTC)
        pending = self._pending
        if pending and not pending[1].done():
            if pending[0](packet):
                pending[1].set_result(packet)
                return
            self.unmatched[self._pending_what or "request"] = packet.hex()
        self._handle_event(packet)

    def _handle_event(self, packet: Packet) -> None:
        """Handle a message the unit sent on its own (front-panel use)."""
        instruction = packet.instruction
        if instruction == INSTR_SWITCH_VIDEO:
            self._apply_route(packet.output, packet.input)
            if self._supports_modes and self.mode == MODE_AFV:
                # In Audio follows video the unit announces only the video route.
                self._apply_audio_route(packet.output, packet.input)
            self._notify()
        elif instruction == INSTR_SWITCH_AUDIO:
            self._apply_audio_route(packet.output, packet.input)
            self._notify()
        elif instruction == INSTR_SET_LEVEL and self._audio_levels:
            if 1 <= packet.input <= self._outputs and packet.output in self._audio_levels:
                self.levels = {**self.levels, packet.input: packet.output}
                self._notify()
        elif instruction == INSTR_BREAKAWAY and packet.input == 0:
            previous = self.mode
            self.mode = packet.output
            if previous != self.mode:
                self._refresh_event.set()  # audio may have been re-synchronised
            self._notify()
        elif instruction == INSTR_LOCK_PANEL:
            self.locked = packet.input == 1
            self._notify()
        elif instruction in (INSTR_STORE_VIDEO, INSTR_STORE_AUDIO):
            self._on_panel_store(packet)
        elif instruction in (INSTR_RECALL_VIDEO, INSTR_RECALL_AUDIO):
            # A recalled setup changes routes (and possibly the mode), so read them again.
            _LOGGER.debug("Unit announced a recall of setup %s; refreshing", packet.input)
            self._refresh_event.set()
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

    def _on_panel_store(self, packet: Packet) -> None:
        """Note a setup stored (output 0) or deleted (output 1) from the front panel."""
        slot = packet.input
        if self._preset_slots > 0 and 1 <= slot <= self._preset_slots:
            self.preset_defined = {**self.preset_defined, slot: packet.output == 0}
            self._last_preset_scan = 0.0  # confirm with a fresh scan at the next refresh
            self._notify()
            self._refresh_event.set()

    def _apply_audio_route(self, output: int, input_number: int) -> None:
        """Record an audio route (output 0 means every output)."""
        if output == 0:
            self.audio_routes = dict.fromkeys(range(1, self._outputs + 1), input_number)
        elif 1 <= output <= self._outputs:
            self.audio_routes[output] = input_number

    def _apply_route(self, output: int, input_number: int) -> None:
        """Record a route (output 0 means every output)."""
        if output == 0:
            self.routes = dict.fromkeys(range(1, self._outputs + 1), input_number)
        elif 1 <= output <= self._outputs:
            self.routes[output] = input_number

    async def _refresh(self) -> None:
        """Read the whole state: every output, the mode and the lock."""
        self._answers = 0
        routes: dict[int, int] = {}
        for output in range(1, self._outputs + 1):
            reply = await self._read(
                f"video:{output}",
                build_packet(INSTR_REQUEST_VIDEO_STATUS, 0, output, self._machine),
                INSTR_REQUEST_VIDEO_STATUS,
                what=f"reading the video route of output {output}",
            )
            if reply is not None:
                routes[output] = reply.output
        if not routes and self._outputs:
            raise KramerTimeoutError("No reply from the unit (reading the video routes)")

        audio: dict[int, int] = {}
        if self._supports_modes and self.audio_supported is not False:
            for output in range(1, self._outputs + 1):
                try:
                    reply = await self._read(
                        f"audio:{output}",
                        build_packet(INSTR_REQUEST_AUDIO_STATUS, 0, output, self._machine),
                        INSTR_REQUEST_AUDIO_STATUS,
                        what=f"reading the audio route of output {output}",
                        quiet=self.audio_supported is not True and output == 1,
                    )
                except (KramerCommandError, KramerTimeoutError):
                    if self.audio_supported or output > 1:
                        raise
                    # Never answered, though the video reads just worked: this unit
                    # does not report audio routes separately.
                    _LOGGER.debug("Unit does not report audio routes")
                    self.audio_supported = False
                    audio = {}
                    break
                if reply is not None:
                    audio[output] = reply.output
                    self.audio_supported = True

        mode = self.mode
        if self._supports_modes:
            try:
                reply = await self._request(
                    build_packet(INSTR_REQUEST_BREAKAWAY, 0, 0, self._machine),
                    INSTR_REQUEST_BREAKAWAY,
                    what="reading the routing mode",
                )
                mode = reply.output
            except KramerCommandError:
                _LOGGER.debug("Unit does not report its mode")
            except KramerTimeoutError:
                if not self._answers:
                    raise  # nothing has answered: the unit is not there
                # The unit answers the routes but not this: keep the last mode rather than lose the unit.
                _LOGGER.debug("Unit did not answer the routing mode read; keeping the last mode")

        locked = self.locked
        if self.lock_supported is not False:
            try:
                reply = await self._read(
                    "lock",
                    build_packet(INSTR_REQUEST_LOCK, 0, 0, self._machine),
                    INSTR_REQUEST_LOCK,
                    what="reading the panel lock",
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
                if reply is not None:
                    locked = reply.output == 1
                    self.lock_supported = True

        levels = dict(self.levels)
        if self._audio_levels:
            for output in range(1, self._outputs + 1):
                reply = await self._read(
                    f"level:{output}",
                    build_packet(INSTR_REQUEST_LEVEL, output, 0, self._machine),
                    INSTR_REQUEST_LEVEL,
                    lambda p, o=output: p.input == o,
                    what=f"reading the audio level of output {output}",
                )
                if reply is not None and reply.output in self._audio_levels:
                    levels[output] = reply.output

        self.routes = routes
        self.audio_routes = audio
        self.mode = mode
        self.locked = locked
        self.levels = levels
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
        return await self._request(build_packet(INSTR_IDENTIFY, what, which, self._machine), INSTR_IDENTIFY, quiet=True, what="reading the device identity")

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
            if self._audio_levels:  # the VS-5x4 answers only the model and firmware questions
                reply = await self._identify(3, 0)
                info["firmware_raw"] = [reply.input, reply.output]
                info["firmware"] = f"{reply.input}.{reply.output}"
                raise _IdentityDone
            reply = await self._identify(1, 1)
            info["suffix"] = "".join(chr(b) for b in (reply.input, reply.output) if 32 < b < 127)
            reply = await self._identify(3, 0)
            info["firmware_raw"] = [reply.input, reply.output]
            info["firmware"] = f"{reply.input}.{reply.output}"
            reply = await self._identify(4, 0)
            info["audio_firmware"] = f"{reply.input}.{reply.output}"
        except _IdentityDone:
            pass
        except (KramerTimeoutError, KramerCommandError) as err:
            _LOGGER.debug("Unit did not answer every identity question: %s", err)
        self.identity = info
        self.identity_checked = True
        self._identity_done = True
        self._notify()

    async def _scan_presets_if_due(self, force: bool = False) -> None:
        """Check which preset slots are filled, at most every few minutes."""
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
        """Re-check which slots are filled now (about a second or two)."""
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
            what=f"checking whether preset {slot} is filled",
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
                what=f"reading the video of output {output} in preset {slot}",
            )
            video[output] = reply.output
            reply = await self._request(
                build_packet(INSTR_REQUEST_AUDIO_STATUS, slot, output, self._machine),
                INSTR_REQUEST_AUDIO_STATUS,
                what=f"reading the audio of output {output} in preset {slot}",
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
            what=f"storing preset {slot}",
        )
        self.preset_defined[slot] = True
        self._notify()

    async def async_recall_preset(self, slot: int, audio_only: bool = False) -> None:
        """Recall a stored setup, then re-read everything the unit now holds."""
        if not await self.async_preset_defined(slot):
            raise KramerCommandError(f"Preset {slot} is empty")
        instruction = INSTR_RECALL_AUDIO if audio_only else INSTR_RECALL_VIDEO
        # The unit acknowledges an audio recall with the video recall code.
        await self._request(
            build_packet(instruction, slot, 0, self._machine),
            (INSTR_RECALL_VIDEO, INSTR_RECALL_AUDIO),
            lambda p: p.input == slot,
            what=f"recalling preset {slot}",
        )
        await self._refresh()
