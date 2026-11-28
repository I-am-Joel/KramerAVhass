"""Simulated VS-1202YC, VS-5x4 (2-byte protocol) and VS-5x4 (Protocol 2000) for the 0.16.0 tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "custom_components" / "kramer"))
import protocol as p  # noqa: E402

LEVELS = (0, 2, 4, 6)


class _Base:
    """Listening socket plus the list of connected clients."""

    def __init__(self, machine: int = 1) -> None:
        """Set up."""
        self.m = machine
        self.writers: list[asyncio.StreamWriter] = []
        self.server = None
        self.port = 0
        self.log: list[bytes] = []

    async def start(self) -> int:
        """Start."""
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Stop."""
        for w in self.writers:
            w.close()
        if self.server:
            self.server.close()

    def say(self, data: bytes) -> None:
        """Send to every client."""
        for w in self.writers:
            w.write(data)


class TwoByteSim(_Base):
    """A 2-byte unit: kind '1202' or '5x4'. Only one command at a time is accepted."""

    def __init__(self, kind: str, machine: int = 1) -> None:
        """Set up."""
        super().__init__(machine)
        self.kind = kind
        self.n_in, self.n_out = (12, 2) if kind == "1202" else (4, 4) if kind == "sis" else (5, 4)
        self.sig = 0x38 if kind == "1202" else 0x40
        self.routes = dict.fromkeys(range(1, self.n_out + 1), 1)
        self.levels = dict.fromkeys(range(1, 5), 0)

    def pkt(self, second: int) -> bytes:
        """Reply packet."""
        return bytes([self.sig | (self.m - 1), second])

    def value(self, o: int, i: int) -> int:
        """Route value."""
        return 2 * i + o - 2 if self.kind == "1202" else (i - 1) * 4 + o

    def status(self) -> bytes:
        """Status reply."""
        out = b""
        for o in range(1, self.n_out + 1):
            i = self.routes[o]
            v = ((16 if self.kind == "sis" else 20) + o) if i == 0 else self.value(o, i)
            out += self.pkt(0x80 | v)
        if self.kind == "5x4":
            for o in range(1, 5):
                out += self.pkt(0xA4 + (o - 1) * 4 + LEVELS.index(self.levels[o]))
        return out

    async def handle(self, r, w) -> None:
        """Handle."""
        self.writers.append(w)
        buf = bytearray()
        while True:
            data = await r.read(64)
            if not data:
                return
            buf.extend(data)
            while len(buf) >= 2:
                first, second = buf[0], buf[1]
                del buf[:2]
                if first & 7 != self.m - 1 or first & 0x78:
                    continue
                self.log.append(bytes([first, second]))
                if getattr(self, "silent", False):
                    continue
                w.write(self.reply(second))
                await w.drain()

    def reply(self, c: int) -> bytes:  # noqa: C901
        """Reply to a command byte."""
        if c == 0xA1:
            return self.status()
        if c >= 0xA4:
            if self.kind != "5x4" or c > 0xB3:
                return b""
            o, g = divmod(c - 0xA4, 4)
            self.levels[o + 1] = LEVELS[g]
            return self.pkt(0xA2)
        v = c & 0x1F
        if self.kind == "1202":
            if not 1 <= v <= 24:
                return self.pkt(0xA3)
            o = 1 if v % 2 else 2
            self.routes[o] = (v - o + 2) // 2
            return self.pkt(0xA2)
        if self.kind == "sis":
            if 1 <= v <= 16:
                self.routes[(v - 1) % 4 + 1] = (v - 1) // 4 + 1
            elif 21 <= v <= 24:
                self.routes = dict.fromkeys(self.routes, v - 20)
            elif 17 <= v <= 20:
                self.routes[v - 16] = 0
            elif v == 25:
                self.routes = dict.fromkeys(self.routes, 0)
            else:
                return self.pkt(0xA3)
            return self.pkt(0xA2)
        if 1 <= v <= 20:
            self.routes[(v - 1) % 4 + 1] = (v - 1) // 4 + 1
        elif 21 <= v <= 25:
            self.routes = dict.fromkeys(self.routes, v - 20)
        elif 26 <= v <= 29:
            self.routes[v - 25] = 0
        elif v == 30:
            self.routes = dict.fromkeys(self.routes, 0)
        else:
            return self.pkt(0xA3)
        return self.pkt(0xA2)

    def panel_route(self, o: int, i: int) -> None:
        """Announce a front-panel route."""
        self.routes[o] = i
        self.say(self.pkt(0x80 | self.value(o, i)))

    def panel_level(self, o: int, db: int) -> None:
        """Announce a front-panel level change."""
        self.levels[o] = db
        self.say(self.pkt(0xA4 + (o - 1) * 4 + LEVELS.index(db)))

    def panel_all(self, i: int) -> None:
        """Announce ALL (5x4): a value of 21 or more."""
        self.routes = dict.fromkeys(self.routes, i)
        self.say(self.pkt(0x80 | (20 + i)))


class P2000FiveByFour(_Base):
    """A VS-5x4 on Protocol 2000: no range checking, levels via instructions 22 and 25."""

    def __init__(self, machine: int = 1) -> None:
        """Set up."""
        super().__init__(machine)
        self.routes = dict.fromkeys(range(1, 5), 1)
        self.levels = dict.fromkeys(range(1, 5), 0)
        self.busy = 0  # number of requests to answer with a busy error first

    def pkt(self, i: int, a: int, b: int) -> bytes:
        """Reply."""
        return p.build_packet(i, a, b, self.m, True)

    async def handle(self, r, w) -> None:
        """Handle."""
        self.writers.append(w)
        buf = bytearray()
        while True:
            data = await r.read(64)
            if not data:
                return
            buf.extend(data)
            for k in p.parse_packets(buf):
                if k.machine != self.m:
                    continue
                self.log.append(p.build_packet(k.instruction, k.input, k.output, k.machine))
                if self.busy:
                    self.busy -= 1
                    w.write(self.pkt(16, 0, 0))
                else:
                    for out in self.answer(k):
                        w.write(out)
                await w.drain()

    def answer(self, k: p.Packet) -> list[bytes]:  # noqa: C901
        """Answer one packet; unsupported questions get the unit's error frame (machine byte 5)."""
        refused = [p.build_packet(16, 0, 0, 5, True)]
        i = k.instruction
        if i == 1:
            if k.output == 0:
                self.routes = dict.fromkeys(self.routes, k.input)
            else:
                self.routes[k.output] = k.input
            return [self.pkt(1, k.input, k.output)]
        if i == 5:
            return [self.pkt(5, k.input, self.routes[k.output])]
        if i == 22:
            self.levels[k.input] = k.output
            return [self.pkt(22, k.input, k.output)]
        if i == 25:
            return [self.pkt(25, k.input, self.levels[k.input])]
        if i == 61 and (k.input, k.output) == (1, 0):
            return [self.pkt(61, 5, 4)]
        if i == 61 and (k.input, k.output) == (3, 0):
            return [self.pkt(61, 1, 0)]
        if i in (6, 8, 30, 31):
            return refused
        return []

    def panel_level(self, o: int, db: int) -> None:
        """Announce a front-panel level change."""
        self.levels[o] = db
        self.say(self.pkt(22, o, db))

    def panel_route(self, o: int, i: int) -> None:
        """Announce a front-panel route."""
        self.routes[o] = i
        self.say(self.pkt(1, i, o))
