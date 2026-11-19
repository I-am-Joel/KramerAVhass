"""Tests for the Kramer integration."""

import asyncio
import sys

sys.path.insert(0, "custom_components/kramer")
import protocol as p


class Sim:
    """A tiny simulated unit."""

    def __init__(s, machine=1, n=16):
        """Set up the object."""
        s.m = machine
        s.routes = dict.fromkeys(range(1, n + 1), 1)
        s.mode = 0
        s.lock = 0
        s.writers = []

    async def handle(s, r, w):
        """Handle."""
        s.writers.append(w)
        buf = bytearray()
        while True:
            d = await r.read(64)
            if not d:
                return
            buf.extend(d)
            for k in p.parse_packets(buf):
                if k.machine != s.m:
                    continue
                i = k.instruction
                if i == 1:
                    if k.output == 0:
                        s.routes = dict.fromkeys(s.routes, k.input)
                    else:
                        s.routes[k.output] = k.input
                    w.write(p.build_packet(1, k.input, k.output, s.m, True))
                elif i == 5:
                    w.write(
                        p.build_packet(5, 0x00 if False else 0, 0, s.m, True)[:0]
                        + p.build_packet(5, 0, s.routes[k.output], s.m, True)
                    )
                elif i == 6:
                    w.write(p.build_packet(6, 0, s.routes[k.output], s.m, True))
                elif i == 8:
                    s.mode = k.output if k.output < 2 else s.mode
                    w.write(p.build_packet(8, 0, s.mode, s.m, True))
                elif i == 11:
                    w.write(p.build_packet(11, 0, s.mode, s.m, True))
                elif i == 30:
                    s.lock = k.input
                    w.write(p.build_packet(30, k.input, 0, s.m, True))
                elif i == 31 and not getattr(s, "nolock", False):
                    w.write(p.build_packet(31, 0, s.lock, s.m, True))
                await w.drain()

    def panel(s, o, i):
        """Panel."""
        s.routes[o] = i
        for w in s.writers:
            w.write(b"\x41\x80"[:0] + p.build_packet(1, i, o, s.m, True))


async def main():
    """Run the protocol checks."""
    sim = Sim()
    srv = await asyncio.start_server(sim.handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    await p.async_probe("127.0.0.1", port, 1)
    try:
        await p.async_probe("127.0.0.1", port, 2, timeout=1)
        assert 0
    except p.KramerTimeoutError:
        pass
    c = p.KramerClient(
        "127.0.0.1", port, 1, 16, 16, reconnect_delay=0.2, refresh_interval=0.5
    )
    ev = []
    c.add_listener(lambda: ev.append(1))
    await c.async_start()
    assert await c.async_wait_ready(5)
    assert c.common_input == 1 and c.mode == 0 and c.locked is False
    await c.async_set_route(3, 5)
    assert c.routes[3] == 5 and c.common_input is None
    await c.async_set_route(0, 2)
    assert c.common_input == 2
    await c.async_set_route(4, 0)
    assert c.routes[4] == 0
    await c.async_set_mode(1)
    assert sim.mode == 1
    try:
        await c.async_set_mode(3)
        assert 0
    except p.KramerCommandError:
        pass
    assert c.mode == 1
    await c.async_set_lock(True)
    assert sim.lock == 1 and c.locked
    sim.panel(7, 9)
    await asyncio.sleep(0.1)
    assert c.routes[7] == 9
    # drop connection -> recovery
    for w in sim.writers:
        w.close()
    sim.writers.clear()
    sim.routes[1] = 11
    await asyncio.sleep(1.5)
    assert c.available and c.routes[1] == 11, (c.available, c.routes)
    # server gone -> unavailable
    srv.close()
    for w in sim.writers:
        w.close()
    await asyncio.sleep(0.5)
    assert not c.available
    await c.async_stop()
    print("ALL OK")  # noqa: T201
    sim2 = Sim(n=1)
    sim2.nolock = True
    srv2 = await asyncio.start_server(sim2.handle, "127.0.0.1", 0)
    port2 = srv2.sockets[0].getsockname()[1]
    c2 = p.KramerClient(
        "127.0.0.1", port2, 1, 8, 1, supports_modes=False, reconnect_delay=0.2
    )
    await c2.async_start()
    assert await c2.async_wait_ready(10)
    assert c2.available and c2.lock_supported is False and c2.routes == {1: 1}
    await c2.async_set_route(1, 5)
    assert c2.routes[1] == 5 and c2.common_input == 5
    await c2.async_stop()
    print("812-style OK")  # noqa: T201


asyncio.run(main())
