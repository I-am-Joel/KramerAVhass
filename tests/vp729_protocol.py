"""Client for the Kramer VP-729 ASCII protocol over TCP.

One TCP connection (the unit's serial-to-Ethernet module accepts one at a time)
is used for commands, reads and the unsolicited messages the unit sends.

Set:   "Y 0 <function> <value>" + CR     reply "Z 0 <function> <value>"
Get:   "Y 1 <function>" + CR             reply "Z 1 <function> <value>"
Error: "ERR <n>" (1 bad command, 2 unknown function, 3 unavailable now,
       4 bad control type, 5 get on set-only, 6 set on get-only,
       7 unavailable parameter, 8 missing argument)

Behaviour seen on a real unit, which this client allows for:
 * the unit echoes each command before answering, and ends with ">";
 * it pushes "Z 0 <function> <value>" when something changes (front panel,
   remote) and delivers a backlog of those when a connection opens;
 * some confirmations arrive late, stuck onto the next command's reply, so
   replies are matched by function number with one command in flight;
 * a set to an out-of-range value is echoed as success but ignored, so every
   set is read back;
 * after a power-on or a profile recall the unit is silent for about ten seconds.

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

from .protocol import (
    KramerCommandError,
    KramerConnectionError,
    KramerTimeoutError,
    _close,
    _enable_keepalive,
)
from .vp729_defs import BY_FN, DEPENDENTS, READABLE, UNAVAILABLE_ERRORS

_LOGGER = logging.getLogger(__name__)

_REPLY = re.compile(r"^Z\s+(\d)\s+(\d+)(?:\s+(.*))?$")
_ERROR = re.compile(r"^ERR\s*(\d+)")
_INT = re.compile(r"^-?\d+$")

FIRMWARE_FN = 76

ERROR_TEXT = {
    1: "bad command",
    2: "unknown function",
    3: "not available at the moment",
    4: "bad control type",
    5: "that function cannot be read",
    6: "that function cannot be set",
    7: "parameter not available",
    8: "missing value",
}


class Vp729CommandError(KramerCommandError):
    """The unit answered with an ERR code."""

    def __init__(self, code: int) -> None:
        """Keep the code with a readable message."""
        self.code = code
        super().__init__(f"Unit reported: {ERROR_TEXT.get(code, f'error {code}')}")


def parse_value(text: str | None) -> int | str | None:
    """Turn a reply value into a number where it is one, else keep the text."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    return int(text) if _INT.match(text) else text


def parse_lines(buffer: bytearray) -> list[str]:
    """Remove complete lines from the buffer and return them, tidied.

    The unit ends lines with CR or CRLF and sends a bare ">" as a prompt, so a
    leading ">" is dropped and empty lines are skipped.
    """
    lines: list[str] = []
    while True:
        match = re.search(rb"[\r\n]", buffer)
        if match is None:
            return lines
        raw = bytes(buffer[: match.start()])
        del buffer[: match.end()]
        text = raw.decode("ascii", errors="replace").lstrip("> ").strip()
        if text:
            lines.append(text)


async def async_probe_vp729(host: str, port: int, timeout: float = 5.0) -> str:
    """Connect and read the firmware revision; return it as text.

    Raises KramerConnectionError if the connection fails and
    KramerTimeoutError if connected but the unit stays silent.
    """
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError) as err:
        raise KramerConnectionError(f"Cannot connect to {host}:{port}: {err}") from err

    try:
        writer.write(f"Y 1 {FIRMWARE_FN}\r".encode("ascii"))
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
                if match and int(match.group(2)) == FIRMWARE_FN and match.group(1) == "1":
                    return (match.group(3) or "").strip()
                error = _ERROR.match(line)
                if error:
                    raise Vp729CommandError(int(error.group(1)))
    except OSError as err:
        raise KramerConnectionError(str(err)) from err
    finally:
        await _close(writer)


class Vp729Client:
    """Keeps one connection to a VP-729 and tracks its state."""

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
        self._listeners: list[Callable[[], None]] = []
        self._refresh_event = asyncio.Event()
        self._ready = asyncio.Event()
        self._hold_until = 0.0
        self._full_refresh = True
        self._dirty: set[int] = set()

        self.available = False
        self.values: dict[int, int | str | None] = {}
        self.errors: dict[int, int] = {}
        self.last_reply: datetime | None = None
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @property
    def host(self) -> str:
        """Return the unit's address."""
        return self._host

    @property
    def firmware(self) -> str | None:
        """Return the firmware revision text, if read."""
        value = self.values.get(FIRMWARE_FN)
        return str(value) if value is not None else None

    def value(self, fn: int) -> int | str | None:
        """Return the last known value of a function."""
        return self.values.get(fn)

    def function_available(self, fn: int) -> bool:
        """Return whether the unit is connected and has given a value for this function."""
        return self.available and fn not in self.errors and self.values.get(fn) is not None

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
            _LOGGER.debug("VP-729 %s:%s available=%s", self._host, self._port, available)
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

    async def async_set(self, fn: int, value: int, *, settle: float = 0.0) -> None:
        """Set a function and confirm it by reading it back.

        With `settle` the unit is expected to go quiet for that many seconds
        (power on, profile recall): no read-back is made, the value is taken as
        set, and a full refresh follows once the unit has had time.
        """
        try:
            await self._request(fn, write=True, value=value)
        except KramerTimeoutError:
            if settle:
                raise
            # A late confirmation is still possible, so the read-back decides.
        if settle:
            self.values[fn] = value
            self._settle(settle)
            self._notify()
            self.async_request_refresh()
            return
        got = await self._get(fn)
        if got != value:
            raise Vp729CommandError(1) if got is None else KramerCommandError(
                f"The unit did not accept {value} for function {fn} (it still reads {got})"
            )
        self._after_change(fn)

    async def async_press(self, fn: int, value: int | None = None, *, settle: float = 0.0) -> None:
        """Send a key press or other action (write only)."""
        await self._request(fn, write=True, value=value)
        if settle:
            self._settle(settle)
            self.async_request_refresh()
        else:
            # Auto image, capture and similar can change what the unit reports.
            self._dirty.update(item.fn for item in READABLE if item.fn in (103, 104, 12, 13, 14, 15, 16, 7, 8, 9, 10))
            self._refresh_event.set()

    async def async_read(self, fn: int) -> int | str | None:
        """Read one function now and store it."""
        return await self._read_into_state(fn)

    def _settle(self, seconds: float) -> None:
        self._hold_until = max(self._hold_until, time.monotonic() + seconds)

    def _after_change(self, fn: int) -> None:
        """Re-read everything whose availability depends on this function."""
        self.errors.pop(fn, None)
        dependents = DEPENDENTS.get(fn)
        if dependents:
            self._dirty.update(dependents)
            self._refresh_event.set()
        self._notify()

    async def _request(self, fn: int, *, write: bool, value: int | None = None) -> str | None:
        """Send one command and wait for the reply to that function."""
        if write:
            command = f"Y 0 {fn}" + ("" if value is None else f" {value}")
        else:
            command = f"Y 1 {fn}"
        async with self._lock:
            delay = self._hold_until - time.monotonic()
            if delay > 0:
                await asyncio.sleep(min(delay, 15.0))
            writer = self._writer
            if writer is None or writer.is_closing():
                raise KramerConnectionError("Not connected")
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending = (fn, 0 if write else 1, future)
            try:
                writer.write(f"{command}\r".encode("ascii"))
                await writer.drain()
                reply = await asyncio.wait_for(future, self._request_timeout)
            except TimeoutError as err:
                self._record_error(f"No reply to {command}")
                raise KramerTimeoutError(f"No reply to {command}") from err
            except OSError as err:
                raise KramerConnectionError(str(err)) from err
            finally:
                self._pending = None
        return reply

    async def _get(self, fn: int) -> int | str | None:
        """Read a function, trying again once after a garbled command."""
        for attempt in range(2):
            try:
                reply = await self._request(fn, write=False)
            except Vp729CommandError as err:
                if err.code == 1 and attempt == 0:
                    continue
                raise
            return parse_value(reply)
        return None  # pragma: no cover

    async def _read_into_state(self, fn: int) -> int | str | None:
        """Read a function and record the value, or why it cannot be read."""
        try:
            value = await self._get(fn)
        except Vp729CommandError as err:
            if err.code in UNAVAILABLE_ERRORS:
                if self.errors.get(fn) != err.code:
                    self.errors[fn] = err.code
                    self._notify()
                return None
            _LOGGER.debug("Read of function %s failed: %s", fn, err)
            return None
        changed = self.values.get(fn) != value or fn in self.errors
        self.values[fn] = value
        self.errors.pop(fn, None)
        if changed:
            self._notify()
        return value

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
                if not self._dirty and not self._full_refresh:
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
        """Do whatever reading is due: a full refresh, or just the changed bits."""
        if self._full_refresh:
            self._full_refresh = False
            self._dirty.clear()
            await self._refresh()
            return
        while self._dirty:
            fn = min(self._dirty)
            self._dirty.discard(fn)
            await self._read_into_state(fn)

    async def _refresh(self) -> None:
        """Read every readable function."""
        timeouts = 0
        for control in READABLE:
            try:
                await self._read_into_state(control.fn)
                timeouts = 0
            except KramerTimeoutError:
                timeouts += 1
                if timeouts >= 3:
                    raise
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
        """Hand a line to whoever is waiting for it, or treat it as a pushed change."""
        _LOGGER.debug("Received %r", line)
        self.last_reply = datetime.now(UTC)
        pending = self._pending
        error = _ERROR.match(line)
        if error:
            if pending and not pending[2].done():
                pending[2].set_exception(Vp729CommandError(int(error.group(1))))
            return
        match = _REPLY.match(line)
        if not match:
            return  # the echo of a command, or noise
        control_type, fn, text = int(match.group(1)), int(match.group(2)), match.group(3)
        if pending and not pending[2].done() and pending[0] == fn and pending[1] == control_type:
            pending[2].set_result(text)
        control = BY_FN.get(fn)
        if control is None or not control.readable:
            return  # a key press, profile save/recall or other action the unit announced
        value = parse_value(text)
        if value is None or (fn == 48 and value in (111, -111)):
            return  # no value, or the volume up/down keys rather than a level
        if self.values.get(fn) != value or fn in self.errors:
            self.values[fn] = value
            self.errors.pop(fn, None)
            if control_type == 0:
                self._after_change(fn)  # a push: dependants may have changed
            else:
                self._notify()
