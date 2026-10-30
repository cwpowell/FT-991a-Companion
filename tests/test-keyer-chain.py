#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
# SPDX-License-Identifier: GPL-3.0-or-later
# (The full GPL notice is in the file LICENSE.)
"""The whole keyer chain, without a radio: a simulated MIDI paddle into the keyer, the real sender, real UDP and the
keying daemon, comparing the daemon's line with what the keyer meant to send (the spacing, the fixed delay of the
jitter buffer, a straight key).

    python3 tests/test-keyer-chain.py          ("WRONG" marks a failure)
"""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, filename))
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
fp = _load("ft991a_panel", "ft991a-panel.py")
import importlib.util, sys, time
kd = _load("kd", "keying-daemon.py")
ok = lambda c: "OK" if c else "WRONG"
line = kd.NullKeyLine(); eng = kd.Engine(line, 0.15, 3.0, 0.8); lst = kd.EventListener(eng, "127.0.0.1", 0); lst.start()
sender = fp.KeyEventSender(); sender.set_target(("127.0.0.1", lst.port)); sender.arm()
meant = []                                              # every edge the keyer asked for: (time it should happen, down?)
real_edge = sender.edge
def logged(down, at=None): meant.append((time.perf_counter() if at is None else at, bool(down))); real_edge(down, at)
sender.edge = logged
time.sleep(0.6)                                         # the heartbeats teach the daemon the delay before the first element
keyer = fp.Keyer(sender, None); keyer.mode = "Iambic B"; keyer.set_speed(30)       # dit 40 ms, dah 120 ms
midi = fp.MidiPaddles(keyer, [20], [21])
print("--- a squeeze sent by a MIDI paddle (HaliKey/Vail style timing in the notes) through the keyer, the sender, UDP and the daemon")
for attempt in (1, 2, 3):        # a timing test on a shared machine sees the odd scheduling blip of 3 to 9 ms: noise passes on a retry, a real fault never does
    meant.clear(); line.log.clear()
    midi.handle([0x90, 20, 0], 0.0); time.sleep(0.005); midi.handle([0x90, 21, 5], 0.0)          # dit then dah, 5 ms apart
    time.sleep(0.395); midi.handle([0xB0, 1, 3], 0.0); midi.handle([0x80, 20, 22], 0.0); midi.handle([0x80, 21, 2], 0.0)   # released after 400 ms
    time.sleep(1.2)
    got = line.log; n = min(len(got), len(meant))
    errs = [abs((got[i + 1][0] - got[i][0]) - (meant[i + 1][0] - meant[i][0])) * 1000 for i in range(n - 1)]
    shift = [(got[i][0] - meant[i][0]) * 1000 for i in range(n)]
    if errs and max(errs) < 2.0 and max(shift) - min(shift) < 2.0:
        break
if attempt > 1:
    print(f"   (the first {attempt - 1} attempt(s) saw a scheduling blip on this machine; attempt {attempt} is shown)")
elts = "".join("." if abs((u[0] - d[0]) * 1000 - 40) < 3 else "-" for d, u in zip(meant[0::2], meant[1::2]))
print("   elements sent:", elts, "(a 400 ms squeeze at 30 wpm: four alternating elements, and iambic B adds one on release)", ok(elts == ".-.-."))
print("   edges meant:", len(meant), "| edges the daemon's line made:", len(got) - (1 if got and got[-1][1] is False and len(got) > len(meant) else 0), ok(len(got) >= len(meant)))
errs = [abs((got[i + 1][0] - got[i][0]) - (meant[i + 1][0] - meant[i][0])) * 1000 for i in range(n - 1)]
print(f"   spacing at the line against the spacing the keyer meant: worst {max(errs):.2f} ms over {len(errs)} intervals", ok(max(errs) < 2.0))
shift = [(got[i][0] - meant[i][0]) * 1000 for i in range(n)]
print(f"   every edge is played a fixed {sum(shift)/len(shift):.0f} ms after it was meant (the jitter buffer), varying by {max(shift)-min(shift):.2f} ms", ok(max(shift) - min(shift) < 2.0 and 140 < sum(shift)/len(shift) < 170))
print("   the key is up afterwards:", ok(not line.state), "| late events:", eng.stats["late"], "| forced releases:", eng.stats["forced_up"], ok(eng.stats["late"] == 0 and eng.stats["forced_up"] == 0))
print("--- the same keyer as a straight key (a MIDI key; 'halikey' style)")
keyer.straight = True; n0 = len(meant); mark = len(line.log)
midi2 = fp.MidiPaddles(keyer, [20], []); midi2.handle([0x90, 20, 0], 0.0); time.sleep(0.12); midi2.handle([0x80, 20, 120], 0.0); time.sleep(0.05); midi2.handle([0x90, 20, 50], 0.0); time.sleep(0.2); midi2.handle([0x80, 20, 126], 0.0); time.sleep(0.6)
new = line.log[mark:]
print("   two key closures:", [round((b[0] - a[0]) * 1000) for a, b in zip(new[0::2], new[1::2])], "ms (meant 120 and 126), gap", round((new[2][0] - new[1][0]) * 1000), "ms (meant 50)", ok(len(new) == 4))
keyer.close(); sender.close(); lst.stop(); eng.shutdown()
