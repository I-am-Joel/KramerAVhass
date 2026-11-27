"""Client for the Kramer SP-11D digital video processor (the SP-11D video processor protocol).

The unit is reached over TCP through a serial bridge (9600 baud, 8 data bits, no parity,
1 stop bit). Every message is four bytes:

    instruction, parameter + 0x80, value + 0x80, address byte

Instructions: 32 read, 33 write, 34 recall a preset, 35 store a preset, 0 reset (video 0, default 1),
61 identify. The address byte is 0x98, or 0x99 with DIP switch 8 on. A setting from -128 to 127 is
sent as an 8-bit number: the low 7 bits in the value byte and bit 7 in bit 6 of the address byte,
so a negative setting is sent with 0xD8 (or 0xD9) as its last byte.

Behaviour seen on a real unit, which this client allows for:
 * a read is answered with first byte 0x60, a write is echoed with 0x61 carrying the value the
   unit actually applied (a value out of range is ignored and the old value is echoed);
 * a change made on the front panel is announced the same way as a write, and a recall (made by
   anyone) is announced with first byte 0x62 and the preset number (0 to 15);
 * a store is announced as a recall of the preset in use;
 * writing the output standard restarts the unit for a few seconds, after which it announces a recall;
 * the default command sets every parameter in every preset to 0 and announces a recall of preset 1;
 * a recall does not change the global parameters (20 to 26).

This module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from .protocol import (
    FRONT_PANEL_BUSY_MESSAGE,
    KramerCommandError,
    KramerConnectionError,
    KramerTimeoutError,
    _close,
    _enable_keepalive,
)
from .sp11d_defs import (
    DETECTED_STANDARD_FN,
    OUTPUT_STANDARD_FN,
    PANEL_LOCK_FN,
    PRESET_COUNT,
    PRESET_FN,
    READ_FNS,
    firmware_text,
    model_code_text,
)

_LOGGER = logging.getLogger(__name__)

ADDRESS_DIP8_OFF = 0x98
ADDRESS_DIP8_ON = 0x99

READ, WRITE, RECALL, STORE, RESET, IDENTIFY = 32, 33, 34, 35, 0, 61
BUSY = 16  # the unit's answer to any command while a front-panel entry is waiting for a number
IDENTIFY_NAME, IDENTIFY_VERSION = 1, 3

RESTART_WAIT_SECONDS = 10.0  # the unit is silent for a few seconds after the output standard changes
DEFAULT_WAIT_SECONDS = 15.0  # the default command takes longer


class Sp11dCommandError(KramerCommandError):
    """The unit did not accept a setting."""


class Sp11dBusyError(Sp11dCommandError):
    """The unit is waiting for a front-panel entry and refuses commands."""


BUSY_MESSAGE = FRONT_PANEL_BUSY_MESSAGE


def frame(first: int, param: int, value: int, address: int) -> bytes:
    """Build one message. The value is 8 bits: bit 7 is carried in bit 6 of the address byte."""
    unsigned = value & 255
    return bytes([first, 0x80 | param, 0x80 | (unsigned & 127), address | (0x40 if unsigned & 128 else 0)])


def decode_value(value_byte: int, address_byte: int) -> int:
    """Return the signed setting carried by a value byte and an address byte."""
    unsigned = (((address_byte >> 6) & 1) << 7) | (value_byte & 127)
    return unsigned - 256 if unsigned >= 128 else unsigned


def parse_frames(buffer: bytearray) -> list[tuple[int, int, int, int]]:
    """Take complete messages off the front of the buffer (resynchronising on stray bytes)."""
    found: list[tuple[int, int, int, int]] = []
    i = 0
    while i < len(buffer):
        if buffer[i] >= 128:
            i += 1  # a message starts with a byte whose top bit is clear
            continue
        if i + 3 >= len(buffer):
            break  # the rest of this message has not arrived yet
        if buffer[i + 1] >= 128 and buffer[i + 2] >= 128 and buffer[i + 3] >= 128:
            found.append((buffer[i], buffer[i + 1], buffer[i + 2], buffer[i + 3]))
            i += 4
        else:
            i += 1
    del buffer[:i]
    return found


async def async_probe_sp11d(host: str, port: int, address: int, timeout: float = 5.0) -> int:
    """Connect and ask which preset is in use; return it (1 to 16).

    Raises KramerConnectionError if the connection fails and
    KramerTimeoutError if connected but the unit stays silent.
    """
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError) as err:
        raise KramerConnectionError(f"Cannot connect to {host}:{port}: {err}") from err
    try:
        writer.write(frame(READ, PRESET_FN, 0, address))
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
            for first, param, value, addr in parse_frames(buffer):
                if first & 63 == READ and param & 127 == PRESET_FN and addr & 0xBF == address:
                    return (value & 127) + 1
    except OSError as err:
        raise KramerConnectionError(str(err)) from err
    finally:
        await _close(writer)


class Sp11dClient:
    """Keeps one connection to an SP-11D and tracks its settings."""

    def __init__(
        self,
        host: str,
        port: int,
        address: int = ADDRESS_DIP8_OFF,
        *,
        refresh_interval: float = 60.0,
        retry_interval: float = 10.0,
        reconnect_delay: float = 5.0,
        request_timeout: float = 1.5,
        busy_interval: float = 3.0,
        force_lock: bool = False,
    ) -> None:
        """Initialise the client (nothing connects until async_start)."""
        self._host = host
        self._port = port
        self._address = address
        self._refresh_interval = refresh_interval
        self._retry_interval = retry_interval
        self._reconnect_delay = reconnect_delay
        self._request_timeout = request_timeout
        self._busy_interval = busy_interval
        self._force_lock = force_lock

        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[int, int | None, asyncio.Future] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._refresh_event = asyncio.Event()
        self._ready = asyncio.Event()
        self._recall_seen = asyncio.Event()
        self._full_refresh = True
        self._dirty: set[int] = set()

        self.available = False
        self.busy = False
        self.last_store: tuple[int, datetime] | None = None  # the preset last stored, and when
        self.values: dict[int, int] = {}
        self.firmware: str | None = None
        self.model_code: str | None = None
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

    def value(self, fn: int) -> int | None:
        """Return the last known value of a parameter."""
        return self.values.get(fn)

    @property
    def preset(self) -> int | None:
        """Return the preset in use, 1 to 16."""
        value = self.values.get(PRESET_FN)
        return None if value is None else (value & 127) + 1

    def function_available(self, fn: int) -> bool:
        """Return whether the unit is answering and has given a value for this parameter."""
        if fn == PANEL_LOCK_FN and self._force_lock:
            return False  # "Panel lock (forced)" is in charge of the lock
        return self.available and fn in self.values

    @property
    def force_lock(self) -> bool:
        """Return whether the integration keeps the front panel locked while connected."""
        return self._force_lock

    def set_force_lock(self, forced: bool) -> None:
        """Turn "Panel lock (forced)" on or off. Turning it off leaves the panel as it is."""
        if self._force_lock != forced:
            self._force_lock = forced
            if forced:
                self.async_request_refresh()  # lock the panel now
            self._notify()

    def _set_busy(self, busy: bool) -> None:
        if self.busy != busy:
            self.busy = busy
            self._notify()

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
            _LOGGER.debug("SP-11D %s:%s available=%s", self._host, self._port, available)
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

    async def async_set(self, fn: int, value: int) -> None:
        """Write a parameter and confirm it from the unit's echo, which shows the value it applied."""
        async with self._lock:
            wanted = int(value)
            if fn == OUTPUT_STANDARD_FN:
                self._recall_seen.clear()
            reply = await self._request(frame(WRITE, fn, wanted, self._address), WRITE, fn)
            applied = decode_value(*reply)
            self.values[fn] = applied
            if fn == OUTPUT_STANDARD_FN:
                await self._wait_for_recall(RESTART_WAIT_SECONDS)
            elif fn in (0, 1):
                self._dirty.add(DETECTED_STANDARD_FN)  # the standard in use may follow
                self._refresh_event.set()
            self._notify()
            if applied != wanted:
                message = f"The device did not accept that value (it kept {applied})"
                self._record_error(message)
                raise Sp11dCommandError(message)

    async def async_recall(self, preset: int) -> None:
        """Recall preset 1 to 16, then read everything again."""
        if not 1 <= preset <= PRESET_COUNT:
            raise Sp11dCommandError(f"There is no preset {preset}")
        async with self._lock:
            self._recall_seen.clear()
            await self._request(
                bytes([RECALL, 0x80, 0x80 | (preset - 1), self._address]), RECALL, None, retry=False
            )
            self.async_request_refresh()

    async def async_store(self, preset: int) -> None:
        """Store the present settings in preset 1 to 16."""
        if not 1 <= preset <= PRESET_COUNT:
            raise Sp11dCommandError(f"There is no preset {preset}")
        async with self._lock:
            await self._request(
                bytes([STORE, 0x80, 0x80 | (preset - 1), self._address]), RECALL, None, retry=False
            )
            self.last_store = (preset, datetime.now(UTC))  # the unit acknowledged it
            self._notify()

    async def async_reset_defaults(self) -> None:
        """Send the unit's default command: every parameter in every preset becomes 0."""
        async with self._lock:
            self._recall_seen.clear()
            self._send_bytes(bytes([RESET, 0x81, 0x80, self._address]))
            await self._wait_for_recall(DEFAULT_WAIT_SECONDS)
            self.async_request_refresh()

    async def _wait_for_recall(self, seconds: float) -> None:
        """Wait for the unit's recall announcement that follows a restart (it may not come)."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._recall_seen.wait(), seconds)
        self.async_request_refresh()

    async def async_read(self, fn: int) -> int:
        """Read one parameter."""
        async with self._lock:
            return await self._read_locked(fn)

    async def _read_locked(self, fn: int) -> int:
        reply = await self._request(frame(READ, fn, 0, self._address), READ, fn)
        value = decode_value(*reply)
        self.values[fn] = value
        return value

    def _send_bytes(self, data: bytes) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise KramerConnectionError("Not connected")
        writer.write(data)

    async def _request(
        self, data: bytes, instruction: int, key: int | None, *, retry: bool = True
    ) -> tuple[int, int]:
        """Send one message and wait for its answer; returns the (value byte, address byte) of the reply."""
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._pending = (instruction, key, future)
            try:
                self._send_bytes(data)
                return await asyncio.wait_for(future, self._request_timeout)
            except TimeoutError as err:
                if attempt + 1 >= attempts:
                    raise KramerTimeoutError("No reply from the unit") from err
            except Sp11dBusyError as err:
                self._record_error(str(err))
                raise
            finally:
                self._pending = None
        raise KramerTimeoutError("No reply from the unit")  # pragma: no cover

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
                waiting = False
                try:
                    await self._work()
                except Sp11dBusyError:
                    _LOGGER.debug("Unit is waiting for a front-panel entry; keeping the last values and trying again")
                    self._full_refresh = True
                    waiting = True
                except KramerTimeoutError:
                    _LOGGER.debug("Unit is not answering; will retry")
                    self._set_available(False)

                if self.busy:
                    interval = self._busy_interval
                else:
                    interval = self._refresh_interval if self.available else self._retry_interval
                self._refresh_event.clear()
                if waiting or (not self._dirty and not self._full_refresh):
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
        """Do whatever reading is due: a full refresh, or just the changed parameters."""
        if self._full_refresh:
            self._full_refresh = False
            self._dirty.clear()
            await self._refresh()
            return
        while self._dirty:
            fn = min(self._dirty)
            self._dirty.discard(fn)
            async with self._lock:
                await self._read_locked(fn)
            self._notify()

    async def _refresh(self) -> None:
        """Read every parameter (and, once per connection, the unit's identity)."""
        timeouts = 0
        async with self._lock:
            for fn in READ_FNS:
                try:
                    await self._read_locked(fn)
                    timeouts = 0
                except KramerTimeoutError:
                    timeouts += 1
                    if timeouts >= 3:
                        raise
            if self.firmware is None:
                await self._read_identity()
            if self._force_lock and self.values.get(PANEL_LOCK_FN) != 1:
                await self._lock_panel()
        if not self.values:
            raise KramerTimeoutError("No reply from the unit")
        self._set_available(True)
        self._ready.set()
        self._notify()

    async def _lock_panel(self) -> None:
        """Lock the front panel (the caller holds the lock); a refusal is only logged."""
        try:
            reply = await self._request(frame(WRITE, PANEL_LOCK_FN, 1, self._address), WRITE, PANEL_LOCK_FN)
        except Sp11dBusyError:
            raise
        except KramerTimeoutError:
            _LOGGER.debug("The unit did not answer the request to lock the panel")
            return
        self.values[PANEL_LOCK_FN] = decode_value(*reply)

    async def _read_identity(self) -> None:
        """Ask the unit its machine name code and software version (best effort)."""
        try:
            high, low = await self._identify(IDENTIFY_NAME)
            self.model_code = model_code_text(high, low)
            high, low = await self._identify(IDENTIFY_VERSION)
            self.firmware = firmware_text(high, low)
        except KramerTimeoutError:
            _LOGGER.debug("The unit did not answer the identify request")

    async def _identify(self, what: int) -> tuple[int, int]:
        return await self._request(bytes([IDENTIFY, 0x80 | what, 0x80, self._address]), IDENTIFY, None)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Read messages until the connection closes."""
        buffer = bytearray()
        while True:
            chunk = await reader.read(256)
            if not chunk:
                raise KramerConnectionError("Connection closed by the other end")
            buffer.extend(chunk)
            for message in parse_frames(buffer):
                self._handle_frame(*message)

    def _handle_frame(self, first: int, param_byte: int, value_byte: int, address_byte: int) -> None:
        """Hand a message to whoever is waiting for it, or treat it as a change made elsewhere."""
        _LOGGER.debug("Received %02X %02X %02X %02X", first, param_byte, value_byte, address_byte)
        if address_byte & 0xBF != self._address:
            return
        self.last_reply = datetime.now(UTC)
        instruction = first & 63
        param = param_byte & 127
        pending = self._pending
        if instruction == BUSY:
            self._set_busy(True)
            if pending and not pending[2].done():
                pending[2].set_exception(Sp11dBusyError(BUSY_MESSAGE))
            return
        self._set_busy(False)
        if instruction == IDENTIFY:
            if pending and pending[0] == IDENTIFY and not pending[2].done():
                pending[2].set_result((param, value_byte & 127))
            return
        if instruction in (READ, WRITE):
            value = decode_value(value_byte, address_byte)
            matched = pending and pending[0] == instruction and pending[1] == param and not pending[2].done()
            if matched:
                pending[2].set_result((value_byte, address_byte))
            elif instruction == WRITE and param < 28 and self.values.get(param) != value:
                # Something else (the front panel) changed it.
                self.values[param] = value
                if param == OUTPUT_STANDARD_FN:
                    self.async_request_refresh()  # the unit restarts and reloads the preset
                self._notify()
            return
        if instruction == RECALL:
            self.values[PRESET_FN] = value_byte & 127
            self._recall_seen.set()
            if pending and pending[0] == RECALL and not pending[2].done():
                pending[2].set_result((value_byte, address_byte))
            else:
                self.async_request_refresh()  # a recall elsewhere: the preset's settings are now live
            self._notify()
