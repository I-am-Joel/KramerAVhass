"""A small simulated SP-11D for the tests.

It behaves as the real unit was seen to in the probes: four-byte messages ending in the address
byte (0x98, or 0x99 with DIP switch 8 on), reads answered with first byte 0x60, writes echoed
with 0x61 carrying the value actually applied (out of range ignored), signed values carried in
bit 6 of the address byte, recalls announced with 0x62, a store announced as a recall of the
preset in use, an output standard change that restarts the unit and announces a recall, and a
default command that zeroes every preset and announces a recall of preset 1.
"""

from __future__ import annotations

import asyncio

RANGES: dict[int, tuple[int, int]] = {
    0: (0, 4),
    1: (0, 6),
    3: (0, 15),
    4: (0, 15),
    8: (-16, 15),
    9: (-1, 1),
    18: (0, 2),
    20: (0, 2),
    21: (0, 5),
    22: (0, 1),
    23: (0, 1),
    25: (-32, 31),
    26: (0, 1),
}
for _fn in (2, 5, 6, 7, 10, 11, 12, 13, 14, 15, 16, 17, 24):
    RANGES[_fn] = (-100, 100)

PER_PRESET = range(19)  # parameters 0 to 18 are stored in each preset


def _encode(first: int, param: int, value: int, address: int) -> bytes:
    unsigned = value & 255
    return bytes([first, 0x80 | param, 0x80 | (unsigned & 127), address | (0x40 if unsigned & 128 else 0)])


class Sp11dSim:
    """A fake unit listening on localhost."""

    def __init__(self, address: int = 0x98) -> None:
        """Set up the state."""
        self.address = address
        self.presets = [dict.fromkeys(PER_PRESET, 0) for _ in range(16)]
        self.presets[0].update({1: 1, 5: 2, 14: -6})
        self.live: dict[int, int] = dict.fromkeys(range(29), 0)
        self.live[20] = 2
        self.live[21] = 2
        self.live[28] = -33
        self.current = 0
        self.load(0)
        self.restart_seconds = 0.2
        self.silent = False
        self.deaf: set[tuple[int, int]] = set()  # (instruction, parameter) never answered
        self.busy = False  # a front-panel entry is waiting for a number
        self.server: asyncio.AbstractServer | None = None
        self.writers: list[asyncio.StreamWriter] = []
        self.log: list[tuple[int, int, int, int]] = []

    def load(self, preset: int) -> None:
        """Make a preset live."""
        self.current = preset
        self.live.update(self.presets[preset])
        self.live[27] = preset

    async def start(self) -> int:
        """Start listening; return the port."""
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Close the server and every connection."""
        for writer in list(self.writers):
            writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    def push(self, param: int, value: int) -> None:
        """Change a value as if on the front panel and announce it."""
        self.live[param] = value
        for writer in self.writers:
            writer.write(_encode(0x61, param, value, self.address))

    def announce_recall(self, writer: asyncio.StreamWriter | None = None) -> None:
        """Announce a recall of the preset in use."""
        for w in [writer] if writer else self.writers:
            w.write(_encode(0x62, 0, 0, self.address)[:2] + bytes([0x80 | self.current, self.address]))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(64)
                if not chunk:
                    break
                buffer.extend(chunk)
                while len(buffer) >= 4:
                    message = bytes(buffer[:4])
                    del buffer[:4]
                    if not self.silent:
                        await self._message(message, writer)
        except ConnectionError:
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    async def _message(self, message: bytes, writer: asyncio.StreamWriter) -> None:
        first, param_byte, value_byte, address_byte = message
        if address_byte & 0xBF != self.address:
            return  # the unit only answers its own address
        instruction = first & 63
        param = param_byte & 127
        value = (((address_byte >> 6) & 1) << 7) | (value_byte & 127)
        value = value - 256 if value >= 128 else value
        self.log.append((instruction, param, value, address_byte))
        if (instruction, param) in self.deaf:
            return
        if self.busy:
            writer.write(_encode(0x50, 0, 0, self.address))  # the unit's busy answer to any command
            return
        if instruction == 32:
            if param <= 28:
                writer.write(_encode(0x60, param, self.live[param], self.address))
        elif instruction == 33:
            low, high = RANGES.get(param, (0, 0))
            if param in RANGES and low <= value <= high:
                self.live[param] = value
            writer.write(_encode(0x61, param, self.live.get(param, 0), self.address))
            if param == 21 and self.live[21] == value:
                await writer.drain()
                await asyncio.sleep(self.restart_seconds)
                self.load(self.current)
                self.announce_recall(writer)
        elif instruction == 34:
            number = value_byte & 127
            if number < 16:
                self.load(number)
                self.announce_recall(writer)
        elif instruction == 35:
            number = value_byte & 127
            if number < 16:
                self.presets[number] = {p: self.live[p] for p in PER_PRESET}
                self.announce_recall(writer)
        elif instruction == 0 and param == 1:
            self.presets = [dict.fromkeys(PER_PRESET, 0) for _ in range(16)]
            self.load(0)
            self.announce_recall(writer)
        elif instruction == 61:
            what = param
            reply = (1, 2) if what == 1 else (2, 0) if what == 3 else None
            if reply:
                writer.write(bytes([0x7D, 0x80 | reply[0], 0x80 | reply[1], self.address]))
        await writer.drain()
