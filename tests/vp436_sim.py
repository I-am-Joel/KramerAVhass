"""A small simulated VP-436 for the tests.

It behaves as the real unit was seen to: every reply starts with CR LF, a set is echoed and followed by
"DONE", a value out of range is refused with the range ("Y-1-44-[0~8]"), an unknown function with
"Y-2-[??]", picture settings are kept for each input, standby ignores input changes and key presses,
and the firmware banner is printed for "INFO".
"""

from __future__ import annotations

import asyncio
import re

from custom_components.kramer.vp436_defs import (
    BY_TYPE_FN,
    CONTROLS,
    FACTORY_RESET,
    PER_INPUT_KEYS,
    SIGNAL_FN,
)

BANNER = (
    "\r\n\r\n##############################\r\n##  Compiler Version : 750\r\n"
    "##  Jun 14 2011 / 17:57:24\r\n##  VP437-XL-{version}  / 10262\r\n##############################\r\n"
)


def _default(control) -> int:
    if control.options:
        return 1 if control.key == "input" else 0 if control.key != "power" else 1
    return max(control.minimum, min(50, control.maximum))


class Vp436Sim:
    """A fake VP-436 listening on localhost."""

    def __init__(self, version: str = "V6.24", hdcp: bool = False) -> None:
        """Set up the state."""
        self.version = version
        self.hdcp = hdcp
        self.state: dict[str, int] = {}
        self.per_input: dict[int, dict[str, int]] = {}
        self.reset_state()
        self.signal = True
        self.log: list[str] = []
        self.keys: list[int] = []
        self.silent = False
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    def reset_state(self) -> None:
        """Put every setting back to its default (a factory reset)."""
        self.state = {c.key: _default(c) for c in CONTROLS}
        self.state["input"] = 6
        self.per_input = {
            number: {key: 10 * number + i for i, key in enumerate(PER_INPUT_KEYS)} for number in range(1, 8)
        }
        self.state.update(self.per_input[6])

    async def start(self) -> int:
        """Start listening."""
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Stop."""
        for writer in self.writers:
            writer.close()
        if self.server:
            self.server.close()

    def say(self, text: str) -> None:
        """Send text to every connected client, as an announcement."""
        for writer in self.writers:
            if not writer.is_closing():
                writer.write(text.encode("ascii"))

    def push(self, set_type: int, fn: int, value: int) -> None:
        """Announce a change made on the front panel."""
        control = BY_TYPE_FN[(set_type, fn)]
        self._store(control.key, value)
        self.say(f"\r\nZ {set_type} {fn} {value}\r\n")

    def _store(self, key: str, value: int) -> None:
        if key == "input":
            self.per_input[self.state["input"]] = {k: self.state[k] for k in PER_INPUT_KEYS}
            self.state["input"] = value
            self.state.update(self.per_input[value])
        else:
            self.state[key] = value

    async def handle(self, reader, writer) -> None:
        """Answer one line at a time."""
        self.writers.append(writer)
        buffer = b""
        while True:
            try:
                data = await reader.read(64)
            except ConnectionError:
                return
            if not data:
                return
            buffer += data
            while b"\r" in buffer:
                line, buffer = buffer.split(b"\r", 1)
                text = line.decode("ascii").strip()
                if not text or self.silent:
                    continue
                self.log.append(text)
                writer.write(self.reply(text).encode("ascii"))
                await writer.drain()

    def reply(self, text: str) -> str:  # noqa: C901
        """Return what the unit sends back for one line."""
        if text.upper() == "INFO":
            return BANNER.format(version=self.version)
        match = re.fullmatch(r"Y (\d) (\d+)(?: (-?\d+))?", text)
        if not match:
            return "\r\nY-0-[xx]      ( Key Command )\r\n"
        command_type, fn = int(match.group(1)), int(match.group(2))
        value = None if match.group(3) is None else int(match.group(3))
        if command_type == 0:
            if not self.standby:
                self.keys.append(fn)
                if fn == 37:
                    self.state["output_volume"] = min(100, self.state["output_volume"] + 1)
                if fn == 35:
                    self.state["output_volume"] = max(0, self.state["output_volume"] - 1)
            return f"\r\nZ 0 {fn}\r\n"
        if command_type == 2 and fn == SIGNAL_FN:
            return f"\r\nZ 2 {fn} {0 if self.signal else 2}\r\n"
        if (command_type, fn) == FACTORY_RESET[:2] and value == 1:
            self.reset_state()
            return f"\r\nZ {command_type} {fn} {value}\r\n"
        control = BY_TYPE_FN.get((command_type, fn))
        if control is None or (control.optional and not self.hdcp):
            return f"\r\nY-{command_type}-[??]\r\n"
        if command_type == control.get_type:
            return f"\r\nZ {command_type} {fn} {self.state[control.key]}\r\n"
        if value is None:
            return f"\r\nY-{command_type}-[??]-[xx]\r\n"
        if control.key == "power" and value == 2:  # reboot
            return "\r\nZ 6 0 2\r\n"
        low, high = (control.minimum, control.maximum) if not control.options else (min(control.options), max(control.options))
        if control.key == "input":
            low = 0
        if not low <= value <= high:
            return f"\r\nY-{command_type}-{fn}-[{low}~{high}]\r\n"
        if control.key == "power":
            self.state["power"] = value
            if value == 1:
                self.state["freeze"] = 0
                self.state["panel_lock"] = 0
                return "\r\nZ 6 0 1\r\nDONE\r\n"
            return "\r\nZ 6 0 0\r\n"
        if control.key == "input" and self.standby:
            return f"\r\nZ {command_type} {fn} {value}\r\nDONE\r\n"  # accepted but ignored in standby
        self._store(control.key, value)
        return f"\r\nZ {command_type} {fn} {value}\r\nDONE\r\n"

    @property
    def standby(self) -> bool:
        """Return whether the simulated unit is in standby."""
        return self.state["power"] == 0
