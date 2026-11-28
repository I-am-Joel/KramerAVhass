"""A small simulated VP-729 for the tests.

It behaves as the real unit was seen to: it echoes the command, replies
"Z <type> <fn> <value>" then a prompt, answers ERR 3 for a function that is
not available in the current state, echoes an out-of-range set as success but
ignores it, and pushes "Z 0 <fn> <value>" when a value changes on its own.
"""

from __future__ import annotations

import asyncio
import time

from custom_components.kramer.vp729_defs import (
    BY_FN,
    CONTROLS,
    PROFILE_RECALL_FN,
    PROFILE_SAVE_FN,
    limits,
)

FIRMWARE = "VTB 1.26h    Audio 2.30"


def _default(control) -> int:
    if control.options:
        return min(control.options)
    if control.minimum is not None:
        return max(control.minimum, min(0, control.maximum))
    return 0


class Vp729Sim:
    """A fake unit listening on localhost."""

    def __init__(self) -> None:
        """Set up the state."""
        self.state: dict[int, int | str] = {}
        for control in CONTROLS:
            if control.readable and control.fn != 76:
                self.state[control.fn] = _default(control)
        self.state[76] = FIRMWARE
        self.state[27] = 2  # aspect: follow output
        self.state[25] = 11
        self.state[103] = 102
        self.state[104] = 98
        self.state[91] = 1
        self.log: list[str] = []
        self.silent_for = 0.0
        self.deaf: set[str] = set()  # command lines that are never answered
        self.garble_next = False
        self.lag = 0.0  # seconds before an accepted value shows in a read
        self.save_delay = 0.0  # seconds before a profile save is acknowledged
        self.recall_silence = 0.0  # seconds of silence after a profile recall
        self.mute_until = 0.0
        self.pending_values: dict[int, tuple[float, int]] = {}
        self.dropped: list[str] = []
        self.min_gap = 0.0  # a command this soon after the last is garbled, as the real unit does
        self.last_command_time = 0.0
        self.too_fast = 0  # commands that arrived before the previous prompt was sent
        self.server: asyncio.AbstractServer | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self.port = 0

    async def start(self) -> int:
        """Listen on a free port and return it."""
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Close everything."""
        for writer in self._writers:
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    # ---- availability rules seen on the real unit --------------------------------

    def available(self, fn: int) -> bool:
        """Return whether a function can be read or set in the current state."""
        s = self.state
        if fn == 27 and s[36] == 1 and s[37] in (1, 2):
            return False
        if fn in (30, 31):
            return s[27] == 5
        if fn == 28:
            return s[27] == 5 and s[30] != 0
        if fn == 29:
            return s[27] == 5 and s[31] != 0
        if fn == 32:
            return s[27] != 5 and s[36] == 0
        if fn == 33:
            return s[32] == 11
        if fn in (34, 35):
            return s[32] == 11 and s[33] > 0
        if fn == 36:
            return s[32] == 0
        if fn in (37, 44):
            return s[36] == 1
        if fn == 39:
            return s[36] == 1 and s[37] == 0
        if fn in (40, 41):
            return s[36] == 1 and s[37] == 0
        if fn == 42:
            return s[36] == 1 and (s[37] != 0 or s[39] == 4)
        if fn == 43:
            return s[36] == 1 and s[37] == 0 and s[39] == 4
        if fn == 45:
            return s[36] == 1 and s[44] == 1
        if fn == 54:
            return s[53] == 1
        if 60 <= fn <= 67:
            return s[56] == 1
        if fn == 70:
            return s[56] == 2
        return True

    def in_range(self, fn: int, value: int) -> bool:
        """Return whether a value is acceptable for a function."""
        control = BY_FN[fn]
        if control.options is not None:
            return value in control.options
        low, high = limits(control, self.state.get(25))
        return low <= value <= high

    # ---- connection -----------------------------------------------------------------

    def push(self, fn: int, value: int) -> None:
        """Change a value as if somebody used the front panel, and tell everyone."""
        self.state[fn] = value
        for writer in self._writers:
            writer.write(f"Z 0 {fn} {value}\r\n>".encode())

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)
        try:
            while True:
                raw = await reader.readuntil(b"\r")
                line = raw.decode().strip()
                if line in self.deaf:
                    self.dropped.append(line)
                    continue
                if time.monotonic() < self.mute_until:
                    self.dropped.append(line)  # the unit is busy: no echo, no reply
                    continue
                if self.silent_for:
                    await asyncio.sleep(self.silent_for)
                now = time.monotonic()
                if self.min_gap and now - self.last_command_time < self.min_gap:
                    self.last_command_time = now
                    self.too_fast += 1
                    self.log.append(line)
                    writer.write(b"ERR 1\r\n>")
                    await writer.drain()
                    continue
                self.last_command_time = now
                self.log.append(line)
                writer.write(f"{line}\r\n".encode())  # the echo
                answer = self._answer(line)
                if self.save_delay and line.startswith(f"Y 0 {PROFILE_SAVE_FN} "):
                    await asyncio.sleep(self.save_delay)
                writer.write(answer.encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if writer in self._writers:
                self._writers.remove(writer)

    def _answer(self, line: str) -> str:
        if self.garble_next:
            self.garble_next = False
            return "ERR 1\r\n>"
        parts = line.split()
        if len(parts) < 3 or parts[0] != "Y" or parts[1] not in ("0", "1"):
            return "ERR 1\r\n>"
        setting = parts[1] == "0"
        try:
            fn = int(parts[2])
        except ValueError:
            return "ERR 1\r\n>"
        value = int(parts[3]) if len(parts) > 3 else None
        if setting and fn in (PROFILE_SAVE_FN, PROFILE_RECALL_FN):
            if fn == PROFILE_RECALL_FN and self.recall_silence:
                self.mute_until = time.monotonic() + self.recall_silence
            return f"Z 0 {fn} {value}\r\n>"
        if self.state.get(91) == 0 and fn != 91 and fn in BY_FN:
            return "ERR 1\r\n>"  # in standby only the power function answers
        control = BY_FN.get(fn)
        if control is None:
            return "ERR 2\r\n>"
        if not setting:
            if not control.readable:
                return "ERR 5\r\n>"
            if not self.available(fn):
                return "ERR 3\r\n>"
            due = self.pending_values.get(fn)
            if due and time.monotonic() >= due[0]:
                self.state[fn] = due[1]
                del self.pending_values[fn]
            return f"Z 1 {fn} {self.state[fn]}\r\n>"
        if not control.writable:
            return "ERR 6\r\n>"
        if not control.readable:  # a key: no value
            return f"Z 0 {fn}\r\n>" if value is None else f"Z 0 {fn} {value}\r\n>"
        if not self.available(fn):
            return "ERR 3\r\n>"
        if value is None:
            value = 0
        if fn == 48 and value in (111, -111):
            self.state[48] = max(-100, min(24, self.state[48] + (1 if value > 0 else -1)))
        elif self.in_range(fn, value):
            if fn == 54 and value % 2:
                value += 1  # the unit works in 2 ms steps
            if self.lag:
                self.pending_values[fn] = (time.monotonic() + self.lag, value)
            else:
                self.state[fn] = value
        return f"Z 0 {fn} {value}\r\n>"
