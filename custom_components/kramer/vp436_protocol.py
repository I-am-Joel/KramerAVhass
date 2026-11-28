"""Client for the Kramer VP-436 / VP-437XL ASCII protocol, reached over TCP through a serial bridge.

Serial settings 9600 baud, 8 data bits, no parity, 1 stop bit. A command is

    Y <type> <function> [value] CR        (the fields are separated by single spaces)

and the unit answers "Z <type> <function> <value>" on a line of its own. Types: 0 key press,
1 set / 2 get a level or option, 3 set / 4 get the input, picture size or output resolution,
6 set / 7 get power or an on/off switch.

Behaviour seen on a real VP-436 (firmware V6.24), which this client allows for:
 * every reply starts with CR LF; a set is echoed with its own type and is usually followed by a
   line "DONE" (which sometimes arrives before the echo, or not at all);
 * a value outside the range is refused with a line that names the range, for example
   "Y-1-44-[0~8]", and an unknown function with "Y-2-[??]"; nothing is clamped;
 * commands need to be sent one at a time (three or more in one write overflow its buffer);
 * the unit announces changes made on the front panel or remote ("Z 3 0 <input>" for an input
   button, "Z 6 1/2/3 <value>" for freeze, blank and mute, "Z 6 4 <value>" for the panel lock and
   "Z 6 0 1" when it wakes), but not volume buttons, menu changes or a change of signal;
 * the settings in levels (colour, brightness and so on) are kept for each kind of input;
 * in standby it still accepts and stores levels, options, picture size, resolution, blank and
   mute, but ignores input changes and every remote key except power, and its freeze and panel
   lock are cleared when it wakes;
 * after a resolution change the output takes about 2.3 seconds to settle, after a reboot it is
   silent for about 5 seconds, after a factory reset for about 8, and unplugging or plugging
   in an HDMI source can leave it silent for about 5 seconds;
 * "INFO" followed by CR (not a Y command) prints a banner that holds the firmware version.

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from .protocol import (
    QUARANTINE_AFTER,
    KramerCommandError,
    KramerConnectionError,
    KramerTimeoutError,
    _close,
    _enable_keepalive,
    say_name,
)
from .vp436_defs import (
    BY_KEY,
    BY_TYPE_FN,
    CONTROLS,
    FACTORY_RESET,
    FACTORY_RESET_SETTLE_SECONDS,
    GET_LEVEL,
    INPUT_KEY,
    KEY_BUTTONS,
    KEY_TYPE,
    PER_INPUT_KEYS,
    POWER_KEY,
    POWER_ON_SETTLE_SECONDS,
    REBOOT,
    REBOOT_SETTLE_SECONDS,
    RESOLUTION_KEY,
    RESOLUTION_SETTLE_SECONDS,
    SET_TYPES,
    SIGNAL_FN,
    VOLUME_KEYS,
    Vp436Control,
)
from .vp729_defs import KIND_NUMBER

_LOGGER = logging.getLogger(__name__)

_REPLY = re.compile(r"^Z\s+(\d)\s+(\d+)(?:\s+(-?\d+))?\s*$")
_RANGE_ERROR = re.compile(r"^Y-(\d)-(\d+)-\[(-?\d+)~(-?\d+)\]")
_UNKNOWN_ERROR = re.compile(r"^Y-(\d)-(?:\d+-)?\[\?\?\]")
_FIRMWARE = re.compile(r"\b(V\d+\.\d+)\b")

KEY_NAMES = {number: name for _key, name, number, _icon in KEY_BUTTONS}


PACING_GAP = 0.05  # a short pause after each answer, before the next command
DONE_WAIT = 0.15  # how long to wait for the "DONE" that usually follows a set
MAX_HOLD_WAIT = 30.0
INFO_WAIT = 1.5  # how long the banner is waited for


class Vp436CommandError(KramerCommandError):
    """The unit refused a command (out of range or not known)."""

    def __init__(self, message: str, *, unknown: bool = False) -> None:
        """Keep whether the command was not recognised at all."""
        self.unknown = unknown
        super().__init__(message)


def parse_lines(buffer: bytearray) -> list[str]:
    """Remove complete lines (ended by CR or LF) from the buffer and return them, tidied."""
    lines: list[str] = []
    while True:
        match = re.search(rb"[\r\n]", buffer)
        if match is None:
            return lines
        raw = bytes(buffer[: match.start()])
        del buffer[: match.end()]
        text = raw.decode("ascii", errors="replace").strip()
        if text:
            lines.append(text)


async def async_probe_vp436(host: str, port: int, timeout: float = 5.0) -> int:
    """Connect and read the power state (1 on, 0 standby); return it.

    Raises KramerConnectionError if the connection fails and
    KramerTimeoutError if connected but the unit stays silent.
    """
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError) as err:
        raise KramerConnectionError(f"Cannot connect to {host}:{port}: {err}") from err
    try:
        writer.write(b"Y 7 0\r")
        await writer.drain()
        buffer = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise KramerTimeoutError("No reply from the unit")
            try:
                chunk = await asyncio.wait_for(reader.read(256), remaining)
            except TimeoutError as err:
                raise KramerTimeoutError("No reply from the unit") from err
            if not chunk:
                raise KramerConnectionError("Connection closed by the other end")
            buffer.extend(chunk)
            for line in parse_lines(buffer):
                match = _REPLY.match(line)
                if match and match.group(1) == "7" and match.group(2) == "0" and match.group(3) is not None:
                    return int(match.group(3))
    except OSError as err:
        raise KramerConnectionError(str(err)) from err
    finally:
        await _close(writer)


class Vp436Client:
    """Keeps one connection to a VP-436 / VP-437XL and tracks its settings."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        refresh_interval: float = 60.0,
        retry_interval: float = 10.0,
        reconnect_delay: float = 5.0,
        request_timeout: float = 3.0,
    ) -> None:
        """Initialise the client (nothing connects until async_start)."""
        self._host = host
        self._port = port
        self._refresh_interval = refresh_interval
        self._retry_interval = retry_interval
        self._reconnect_delay = reconnect_delay
        self._request_timeout = request_timeout

        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[int, int, asyncio.Future] | None = None
        self._heard: deque[str] = deque(maxlen=4)  # what the device sent while a request waited
        self._strikes: dict[str, int] = {}  # reads that got no answer, in a row
        self.quarantined: set[str] = set()  # reads left out because they never get an answer
        self.unmatched: dict[str, str] = {}  # what the device said instead, by request
        self._listeners: list[Callable[[], None]] = []
        self._refresh_event = asyncio.Event()
        self._ready = asyncio.Event()
        self._done = asyncio.Event()
        self._banner = asyncio.Event()
        self._hold_until = 0.0
        self._full_refresh = True
        self._dirty: set[str] = set()
        self._dirty_signal = False
        self._set_seq: dict[str, int] = {}

        self.available = False
        self.values: dict[str, int] = {}
        self.unsupported: set[str] = set()
        self.signal: int | None = None
        self.firmware: str | None = None
        self.last_reply: datetime | None = None
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @property
    def host(self) -> str:
        """Return the bridge's address."""
        return self._host

    def value(self, key: str) -> int | None:
        """Return the last known value of a setting."""
        return self.values.get(key)

    @property
    def standby(self) -> bool:
        """Return whether the device reports that it is in standby."""
        return self.available and self.values.get(POWER_KEY) == 0

    @property
    def signal_detected(self) -> bool | None:
        """Return whether a signal is present on the input in use (None until read)."""
        return None if self.signal is None else self.signal == 0

    def supports(self, key: str) -> bool:
        """Return whether the device has given a value for this setting (an optional one is created only then)."""
        return key in self.values and key not in self.unsupported

    def function_available(self, control: Vp436Control) -> bool:
        """Return whether the device is answering, has given a value, and takes this setting in its present state."""
        if not self.available or not self.supports(control.key):
            return False
        return not (control.standby_blocked and self.standby)

    def keys_available(self) -> bool:
        """Return whether key presses work (the device ignores them in standby)."""
        return self.available and not self.standby

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
            _LOGGER.debug("VP-436 %s:%s available=%s", self._host, self._port, available)
            self._notify()

    def _record_error(self, message: str) -> None:
        self.last_error = message
        self.last_error_time = datetime.now(UTC)

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
        self._full_refresh = True
        self._refresh_event.set()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set(self, control: Vp436Control, value: int) -> None:
        """Set a setting. The unit's echo shows the value it applied; if only the newest of several fast changes."""
        self._check_value(control, value)
        what = f"setting {say_name(control.name)}"
        if control.kind == KIND_NUMBER:
            what += f" to {value}"
        elif control.options is not None:
            what += f" to {control.options[value]}"
        seq = self._set_seq[control.key] = self._set_seq.get(control.key, 0) + 1
        async with self._lock:
            if self._set_seq.get(control.key) != seq:
                return  # replaced by a newer value while waiting its turn
            reply = await self._send(control.set_type, control.fn, value, what=what)
            applied = value if reply is None else reply
            self.values[control.key] = applied
            self._after_change(control.key)
            if control.key == RESOLUTION_KEY:
                self._settle(RESOLUTION_SETTLE_SECONDS)
            elif control.key == POWER_KEY:
                if value == 1:
                    self._settle(POWER_ON_SETTLE_SECONDS)
                self.async_request_refresh()  # woke up or went to standby: read it all again
            self._notify()
        if applied != value:
            message = f"The device did not accept that value (it kept {applied}) ({what})"
            self._record_error(message)
            raise Vp436CommandError(message)

    @staticmethod
    def _check_value(control: Vp436Control, value: int) -> None:
        """Refuse a value the device would refuse, so it is never sent."""
        if control.kind == KIND_NUMBER:
            if not control.minimum <= value <= control.maximum:
                raise Vp436CommandError(f"{control.name} can be {control.minimum} to {control.maximum}")
        elif control.options is not None and value not in control.options:
            raise Vp436CommandError(f"{value} is not a choice for {control.name}")

    async def async_press(self, number: int) -> None:
        """Send a key press (the same as the key on the remote control)."""
        async with self._lock:
            await self._send(KEY_TYPE, number, None, retry=False, what=f"pressing {KEY_NAMES.get(number, f'key {number}')}")
        if number in VOLUME_KEYS:
            self._mark_dirty("output_volume")  # the volume buttons are not announced

    async def async_reboot(self) -> None:
        """Restart the device (it is silent for about 5 seconds)."""
        await self._slow_command(REBOOT, REBOOT_SETTLE_SECONDS, "restarting the device")

    async def async_factory_reset(self) -> None:
        """Restore every setting to its factory default (about 8 seconds)."""
        await self._slow_command(FACTORY_RESET, FACTORY_RESET_SETTLE_SECONDS, "restoring the factory settings")

    async def _slow_command(self, command: tuple[int, int, int], settle: float, what: str) -> None:
        set_type, fn, value = command
        async with self._lock:
            await self._send(set_type, fn, value, retry=False, what=what)
            self._settle(settle)
        self.async_request_refresh()

    def _settle(self, seconds: float) -> None:
        self._hold_until = max(self._hold_until, time.monotonic() + seconds)

    def _mark_dirty(self, *keys: str, signal: bool = False) -> None:
        self._dirty.update(keys)
        self._dirty_signal = self._dirty_signal or signal
        self._refresh_event.set()

    def _after_change(self, key: str) -> None:
        """Read again whatever a change can alter."""
        if key == INPUT_KEY:
            self._mark_dirty(*PER_INPUT_KEYS, signal=True)  # each kind of input has its own picture settings

    async def _send(
        self,
        set_type: int,
        fn: int,
        value: int | None,
        *,
        retry: bool = True,
        timeout: float | None = None,
        what: str | None = None,
    ) -> int | None:
        """Send one command and return the value in its answer; the caller holds the lock.

        A read or a set that did not get an answer is sent once more (both are safe to repeat);
        a key press never is.
        """
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            try:
                return await self._send_once(set_type, fn, value, timeout, what)
            except KramerTimeoutError:
                if attempt + 1 >= attempts:
                    raise
        return None  # pragma: no cover

    async def _send_once(
        self, command_type: int, fn: int, value: int | None, timeout: float | None, what: str | None = None
    ) -> int | None:
        suffix = f" ({what})" if what else ""
        command = f"Y {command_type} {fn}" + ("" if value is None else f" {value}")
        delay = self._hold_until - time.monotonic()
        if delay > 0:
            await asyncio.sleep(min(delay, MAX_HOLD_WAIT))
        writer = self._writer
        if writer is None or writer.is_closing():
            raise KramerConnectionError("Not connected")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending = (command_type, fn, future)
        self._heard.clear()
        self._done.clear()
        try:
            writer.write(f"{command}\r".encode("ascii"))
            await writer.drain()
            reply = await asyncio.wait_for(future, timeout or self._request_timeout)
        except TimeoutError as err:
            self.unmatched[what or command] = " | ".join(self._heard) or "(nothing)"
            self._record_error(f"No reply to {command}{suffix}")
            raise KramerTimeoutError(f"No reply to {command}{suffix}") from err
        except Vp436CommandError as err:
            if suffix:
                raise Vp436CommandError(f"{err}{suffix}", unknown=err.unknown) from err
            raise
        except OSError as err:
            raise KramerConnectionError(str(err)) from err
        finally:
            self._pending = None
        if command_type in SET_TYPES:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._done.wait(), DONE_WAIT)
        await asyncio.sleep(PACING_GAP)
        return reply

    async def _read_control(self, control: Vp436Control) -> None:
        """Read one setting and record the value (or that this device does not have it); the caller holds the lock."""
        try:
            value = await self._send(control.get_type, control.fn, None, what=f"reading {say_name(control.name)}")
        except Vp436CommandError as err:
            if err.unknown:
                self.unsupported.add(control.key)
                self.values.pop(control.key, None)
                if not control.optional:
                    _LOGGER.debug("The device does not know %s", control.name)
            return
        if value is None:
            return
        self.unsupported.discard(control.key)
        if self.values.get(control.key) != value:
            self.values[control.key] = value
            self._notify()

    async def _read_signal(self) -> None:
        """Read the signal indicator (0 while a signal is present); the caller holds the lock."""
        try:
            value = await self._send(GET_LEVEL, SIGNAL_FN, None, what="reading the signal state")
        except Vp436CommandError:
            return
        if value is not None and self.signal != value:
            self.signal = value
            self._notify()

    async def _read_firmware(self) -> None:
        """Ask for the banner that holds the firmware version (best effort); the caller holds the lock."""
        writer = self._writer
        if writer is None or writer.is_closing():
            raise KramerConnectionError("Not connected")
        self._banner.clear()
        writer.write(b"INFO\r")
        await writer.drain()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._banner.wait(), INFO_WAIT)
        await asyncio.sleep(0.3)  # the rest of the banner

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
        self._full_refresh = True
        try:
            while True:
                try:
                    await self._work()
                except KramerTimeoutError:
                    _LOGGER.debug("Unit is not answering; will retry")
                    self._set_available(False)

                interval = self._refresh_interval if self.available else self._retry_interval
                self._refresh_event.clear()
                if not self._dirty and not self._dirty_signal and not self._full_refresh:
                    waiter = asyncio.create_task(self._refresh_event.wait())
                    try:
                        await asyncio.wait({reader_task, waiter}, timeout=interval, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        waiter.cancel()
                    if not self._refresh_event.is_set() and not reader_task.done():
                        self._full_refresh = True  # the interval ran out
                if reader_task.done():
                    reader_task.result()
                    raise KramerConnectionError("Connection closed")
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader_task
            pending = self._pending
            if pending and not pending[2].done():
                pending[2].set_exception(KramerConnectionError("Connection closed"))
            await _close(writer)
            self._writer = None

    async def _work(self) -> None:
        """Do whatever reading is due: a full refresh, or just the changed settings."""
        if self._full_refresh:
            self._full_refresh = False
            self._dirty.clear()
            self._dirty_signal = False
            await self._refresh()
            return
        while self._dirty or self._dirty_signal:
            keys, self._dirty = sorted(self._dirty), set()
            read_signal, self._dirty_signal = self._dirty_signal, False
            async with self._lock:
                for key in keys:
                    await self._read_control(BY_KEY[key])
                if read_signal:
                    await self._read_signal()
            self._notify()

    async def _refresh(self) -> None:
        """Read every setting (the power state first, so a device that is just waking is noticed)."""
        timeouts = 0
        answered = False  # the device has answered something in this refresh, so it is alive
        suspects: list[str] = []  # silent reads from before the first answer: they count once the device proves alive
        async with self._lock:
            for control in sorted(CONTROLS, key=lambda c: c.key != POWER_KEY):  # the power state first
                if control.key != POWER_KEY and control.key in self.unsupported:
                    continue
                if self._strikes.get(control.key, 0) >= QUARANTINE_AFTER:
                    if control.key not in self.quarantined:
                        self.quarantined.add(control.key)
                        _LOGGER.warning(
                            "The device did not answer a read of %s %d times in a row: leaving it out of the readings",
                            control.name,
                            QUARANTINE_AFTER,
                        )
                    continue
                try:
                    await self._read_control(control)
                    timeouts = 0
                    for silent in suspects:
                        self._strikes[silent] = self._strikes.get(silent, 0) + 1
                    suspects.clear()
                    answered = True
                    self._strikes.pop(control.key, None)
                    self.quarantined.discard(control.key)
                except KramerTimeoutError:
                    timeouts += 1
                    if answered:
                        self._strikes[control.key] = self._strikes.get(control.key, 0) + 1
                    else:
                        suspects.append(control.key)
                    if timeouts >= 3:
                        raise
            try:
                await self._read_signal()
            except KramerTimeoutError:
                _LOGGER.debug("The device did not answer the signal read")
            if self.firmware is None:
                await self._read_firmware()
        if not self.values:
            raise KramerTimeoutError("No reply from the unit")
        self._set_available(True)
        self._ready.set()
        self._notify()

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Read lines until the connection closes."""
        buffer = bytearray()
        while True:
            chunk = await reader.read(256)
            if not chunk:
                raise KramerConnectionError("Connection closed by the other end")
            buffer.extend(chunk)
            for line in parse_lines(buffer):
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        """Hand a line to whoever is waiting for it, or treat it as a change made elsewhere."""
        _LOGGER.debug("Received %r", line)
        self.last_reply = datetime.now(UTC)
        pending = self._pending
        if pending and not pending[2].done():
            self._heard.append(line)
        if line == "DONE":
            self._done.set()
            return
        if line.startswith("##"):
            if "VP4" in line and (found := _FIRMWARE.search(line)):
                self.firmware = found.group(1)
                self._banner.set()
                self._notify()
            return
        if "[xx]" in line:
            return  # the unit's list of command types, sent when it cannot make sense of a line
        error = _RANGE_ERROR.match(line)
        if error:
            if pending and not pending[2].done() and pending[0] == int(error.group(1)) and pending[1] == int(error.group(2)):
                message = f"The device accepts {error.group(3)} to {error.group(4)} for that setting"
                pending[2].set_exception(Vp436CommandError(message))
            return
        error = _UNKNOWN_ERROR.match(line)
        if error:
            if pending and not pending[2].done() and pending[0] == int(error.group(1)):
                pending[2].set_exception(Vp436CommandError("The device does not recognise that command", unknown=True))
            return
        match = _REPLY.match(line)
        if not match:
            return
        reply_type, fn = int(match.group(1)), int(match.group(2))
        value = None if match.group(3) is None else int(match.group(3))
        answered = bool(pending and not pending[2].done() and pending[0] == reply_type and pending[1] == fn)
        if answered:
            pending[2].set_result(value)
        self._apply(reply_type, fn, value, answered=answered)

    def _apply(self, reply_type: int, fn: int, value: int | None, *, answered: bool) -> None:
        """Record a value the device reported or announced."""
        if value is None:
            return
        if reply_type == GET_LEVEL and fn == SIGNAL_FN:
            if self.signal != value:
                self.signal = value
                self._notify()
            return
        control = BY_TYPE_FN.get((reply_type, fn))
        if control is None:
            return
        if answered or self.values.get(control.key) == value:
            return  # our own command's answer is handled by the caller; nothing new
        self.values[control.key] = value
        if control.key == POWER_KEY:
            self.async_request_refresh()  # woke up or went to standby: read it all again
        else:
            self._after_change(control.key)
        self._notify()
