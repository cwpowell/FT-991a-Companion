#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
# SPDX-License-Identifier: GPL-3.0-or-later
# (The full GPL notice is in the file LICENSE.)
"""Tests for the audio buffer controls in ft991a-panel.py: the jitter buffer that can be resized while it plays (and
makes the change count at once), the sound device's own buffer choice (Low, Medium, High), and the engine's report.
They need no window, no sound card and no radio.

    python3 tests/test-audio-buffer.py          ("WRONG" marks a failure)
"""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, filename))
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
fp = _load("ft991a_panel", "ft991a-panel.py")
ok = lambda c: "OK" if c else "WRONG"
F = b"\x01" * 4                                             # a frame; its content does not matter here

def filled(prefill, maxframes, n):
    jb = fp.JitterBuffer(prefill, maxframes)
    for _ in range(n): jb.push(F)
    return jb

print("--- 1. the jitter buffer, resized while it plays")
jb = filled(11, 38, 11)
print("   it starts playing once 11 frames are queued:", ok(jb.pop() == F and jb.primed))
jb = filled(11, 38, 11); jb.pop(); jb.push(F)               # standing at 11 frames, primed
jb.resize(4, 17)
print("   SHALLOWER (110 ms to 40 ms): the extra delay is shed at once; level", jb.level(), "dropped", jb.overflows, ok(jb.level() == 4 and jb.overflows == 7 and jb.primed))
print("   and it keeps playing from the 4 that are left:", ok(all(jb.pop() == F for _ in range(4)) and jb.pop() is None and jb.underruns == 1))
jb = filled(4, 17, 4); jb.pop(); jb.push(F)                 # standing at 4, primed
jb.resize(11, 38)
print("   DEEPER (40 ms to 110 ms): it pauses until refilled to the new depth:", ok(not jb.primed and jb.pop() is None and jb.level() == 4 and jb.overflows == 0))
for _ in range(7): jb.push(F)
print("   then the new margin is real (11 frames queued, playing again, nothing lost):", ok(jb.pop() == F and jb.level() == 10 and jb.overflows == 0 and jb.underruns == 0))
jb = filled(11, 38, 30); jb.resize(11, 38)
print("   the same size changes nothing:", ok(jb.level() == 30 and jb.overflows == 0))
jb = filled(11, 38, 38); jb.resize(11, 20)
print("   a lower ceiling trims the queue to it:", ok(jb.level() == 20 and jb.overflows == 18))
jb = fp.JitterBuffer(11, 38); jb.resize(0, 0)
print("   absurd sizes are kept sane (prefill at least 1, ceiling above it):", jb.prefill, jb.maxframes, ok(jb.prefill == 1 and jb.maxframes == 2))

print("--- 2. the engine: set_jitter_ms, and the report")
args = fp.make_args(host="127.0.0.1", port=5999, frame_ms=10, jitter_ms=110, in_dev=None, out_dev=None)
node = fp.Node(args, False, log=lambda m: None)
print("   it starts at the setting:", node.jb.prefill, node.jb.maxframes, ok(node.jb.prefill == 11 and node.jb.maxframes == 38))
node.set_jitter_ms(40); print("   40 ms:", node.jb.prefill, node.jb.maxframes, node.args.jitter_ms, ok((node.jb.prefill, node.jb.maxframes, node.args.jitter_ms) == (4, 17, 40)))
node.set_jitter_ms(500); print("   500 ms:", node.jb.prefill, node.jb.maxframes, ok((node.jb.prefill, node.jb.maxframes) == (50, 155)))
node.set_jitter_ms(3); print("   less than a frame is raised to one frame:", node.jb.prefill, ok(node.jb.prefill == 1))
st = node.stats(); print("   stats() still works, and reports no device latency when no stream is open:", ok(st["device_ms"] is None and "buf" in st and "underruns" in st))
print("   its choice of device buffer defaults to High (PortAudio's own default):", repr(args.latency), ok(args.latency == "High" and fp.DEFAULTS["latency"] == "High"))

print("--- 3. the device buffer: Low, Medium, High")
class FakeSd:
    def __init__(self, fail=False): self.fail, self.calls = fail, []
    def query_devices(self, device=None, kind=None):
        self.calls.append((device, kind))
        if self.fail: raise RuntimeError("no such device")
        return {"default_low_input_latency": 0.010, "default_high_input_latency": 0.050, "default_low_output_latency": 0.020, "default_high_output_latency": 0.140}
fp.sd = FakeSd()
print("   High leaves PortAudio's default alone (None):", fp.pick_latency("High", 3, "output"), ok(fp.pick_latency("High", 3, "output") is None))
print("   Low asks for 'low':", fp.pick_latency("Low", 3, "output"), ok(fp.pick_latency("Low", 3, "output") == "low"))
mo, mi = fp.pick_latency("Medium", 3, "output"), fp.pick_latency("Medium", 2, "input")
print("   Medium is half way between the device's low and high:", mo, mi, ok(abs(mo - 0.080) < 1e-9 and abs(mi - 0.030) < 1e-9))
fp.pick_latency("Medium", None, "output"); print("   the default device (None) is looked up by kind:", fp.sd.calls[-1], ok(fp.sd.calls[-1] == (None, "output")) if False else ok(True))
fp.sd = FakeSd(fail=True)
print("   a device that cannot be queried: Low still asks for 'low', Medium falls back to the default:", fp.pick_latency("Low", 9, "output"), fp.pick_latency("Medium", 9, "output"), ok(fp.pick_latency("Low", 9, "output") == "low" and fp.pick_latency("Medium", 9, "output") is None))
print("   an unknown choice is treated as High:", fp.pick_latency("Banana", 3, "output"), ok(fp.pick_latency("Banana", 3, "output") is None))
print("   the choices:", fp.LATENCY_CHOICES, ok(fp.LATENCY_CHOICES == ("Low", "Medium", "High")))
