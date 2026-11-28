"""A simulated Kramer VS-162AV/RCA, behaving as the real unit did in the Part A tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "custom_components" / "kramer")
)
import protocol as p  # noqa: E402


class Sim:
    """Routes, mode, lock and 60 stored setups; accepts several clients."""

    def __init__(
        self,
        machine: int = 1,
        outputs: int = 16,
        presets: bool = True,
        lock: bool = True,
        identify: bool = True,
        audio: bool = True,
    ) -> None:
        """Set up the object."""
        self.m = machine
        self.n = outputs
        self.video = dict.fromkeys(range(1, outputs + 1), 1)
        self.audio = dict.fromkeys(range(1, outputs + 1), 1)
        self.mode = 0
        self.lock = 0
        self.has_presets = presets
        self.has_lock = lock
        self.has_identify = identify
        self.has_audio_status = audio
        self.mute = False  # answer nothing at all, as a unit that is switched off behind a bridge
        self.deaf: set[tuple[int, int | None]] = set()  # (instruction, output) never answered; output None = any
        self.busy = False  # answer every command with error 3, as a unit with a flashing front-panel button does
        self.slots: dict[int, dict] = {}
        self.writers: list[asyncio.StreamWriter] = []
        self.log: list[bytes] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

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

    def pkt(self, i: int, a: int, b: int) -> bytes:
        """Pkt."""
        return p.build_packet(i, a, b, self.m, True)

    def error(self, code: int) -> bytes:
        """Error."""
        return self.pkt(16, 0, code)

    async def handle(self, r, w) -> None:
        """Handle."""
        self.writers.append(w)
        buf = bytearray()
        while True:
            try:
                data = await r.read(64)
            except ConnectionError:
                return
            if not data:
                return
            buf.extend(data)
            for k in p.parse_packets(buf):
                if k.machine != self.m:
                    continue
                self.log.append(
                    p.build_packet(k.instruction, k.input, k.output, k.machine)
                )
                if self.mute or (k.instruction, k.output) in self.deaf or (k.instruction, None) in self.deaf:
                    continue
                for out in [self.error(3)] if self.busy else self.reply(k):
                    w.write(out)
                await w.drain()

    def reply(self, k: p.Packet) -> list[bytes]:  # noqa: C901
        """Reply."""
        i = k.instruction
        if i == 1:
            if k.output == 0:
                self.video = dict.fromkeys(self.video, k.input)
                targets = list(self.video)
            else:
                self.video[k.output] = k.input
                targets = [k.output]
            if self.mode == 0:
                for t in targets:
                    self.audio[t] = k.input
            return [self.pkt(1, k.input, k.output)]
        if i == 2:
            targets = list(self.audio) if k.output == 0 else [k.output]
            for t in targets:
                self.audio[t] = k.input
                if self.mode == 0:  # in Audio follows video the real unit moves both
                    self.video[t] = k.input
            return [self.pkt(2, k.input, k.output)]
        if i == 6 and not self.has_audio_status and not k.input:
            return []
        if i in (5, 6):
            table = self.video if i == 5 else self.audio
            if k.input:
                if k.input not in self.slots:
                    return [self.error(0)]
                table = self.slots[k.input]["video" if i == 5 else "audio"]
            return [self.pkt(i, k.input, table[k.output])]
        if i == 8:
            if k.output in (0, 1):
                if k.output == 0 and self.mode == 1:
                    self.audio = dict(self.video)
                self.mode = k.output
            return [self.pkt(8, 0, self.mode)]
        if i == 11:
            return [self.pkt(11, 0, self.mode)]
        if i == 30:
            self.lock = k.input
            return [self.pkt(30, k.input, 0)]
        if i == 31:
            return [self.pkt(31, 0, self.lock)] if self.has_lock else []
        if i == 61 and self.has_identify:
            table = {
                (1, 0): (1, 62),  # number 162
                (1, 1): (65, 86),  # suffix "AV"
                (3, 0): (1, 5),
                (4, 0): (1, 5),
            }
            if k.input == 1 and k.output in (10, 11, 12):
                return [
                    self.pkt(61, 65, 86)
                ]  # the real unit answers prefix questions with "AV" too
            if (k.input, k.output) in table:
                return [self.pkt(61, *table[(k.input, k.output)])]
            return [self.error(0)]
        if i == 15 and self.has_presets:
            return [self.pkt(15, k.input, 1 if k.input in self.slots else 0)]
        if i in (3, 19) and self.has_presets:
            if i == 3 and k.output == 1:
                return [self.error(2)]  # delete is refused by the real unit
            if i == 19 and k.output == 1:
                return [self.error(2)]
            slot = self.slots.setdefault(
                k.input,
                {
                    "video": dict(self.video),
                    "audio": dict(self.audio),
                    "mode": self.mode,
                },
            )
            if i == 3:
                slot.update(
                    video=dict(self.video), audio=dict(self.audio), mode=self.mode
                )
            else:
                slot["audio"] = dict(self.audio)
            return [self.pkt(i, k.input, 0)]
        if i in (4, 20) and self.has_presets:
            if k.input not in self.slots:
                return [self.error(0)]
            slot = self.slots[k.input]
            out: list[bytes] = []
            if i == 4:
                self.video = dict(slot["video"])
            self.audio = dict(slot["audio"])
            out += [self.pkt(59, self.video[o], o) for o in sorted(self.video)]
            out += [self.pkt(60, self.audio[o], o) for o in sorted(self.audio)]
            out.append(self.pkt(59, 127, 1))
            out.append(
                self.pkt(4, k.input, 0)
            )  # even the audio recall is acknowledged as 4
            if i == 4 and slot["mode"] != self.mode:
                self.mode = slot["mode"]
                out.append(self.pkt(8, 0, self.mode))
            return out
        return []

    def panel(self, instruction: int, output: int, value: int) -> None:
        """Simulate a front-panel route being announced."""
        for w in self.writers:
            w.write(self.pkt(instruction, value, output))

    def panel_lock(self, on: bool) -> None:
        """Simulate the VS-808YC announcing a front-panel lock (without the from-the-unit marker)."""
        self.lock = 1 if on else 0
        for w in self.writers:
            w.write(p.build_packet(30, self.lock, 0, self.m, False))

    def panel_store(self, slot: int, delete: bool = False) -> None:
        """Simulate a front-panel store (or delete) of a setup being announced."""
        if delete:
            self.slots.pop(slot, None)
        else:
            self.slots[slot] = {
                "video": dict(self.video),
                "audio": dict(self.audio),
                "mode": self.mode,
            }
        for w in self.writers:
            w.write(self.pkt(3, slot, 1 if delete else 0))

    def panel_recall(self, slot: int) -> None:
        """Simulate a front-panel recall: the unit announces only the recall itself (no route packets)."""
        held = self.slots[slot]
        self.video = dict(held["video"])
        self.audio = dict(held["audio"])
        self.mode = held["mode"]
        for w in self.writers:
            w.write(self.pkt(4, slot, 0))

    def panel_route(self, layer: str, output: int, value: int) -> None:
        """Simulate a front-panel route as the real unit does it (one packet; AFV moves both)."""
        if layer == "video":
            self.video[output] = value
            if self.mode == 0:
                self.audio[output] = value
            self.panel(1, output, value)
        else:
            self.audio[output] = value
            self.panel(2, output, value)
