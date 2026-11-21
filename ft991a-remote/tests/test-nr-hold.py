#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
# SPDX-License-Identifier: GPL-3.0-or-later
"""The noise reduction must not learn from the audio while the radio transmits (CW sidetone or silence),
or its noise profile decays and then cannot climb back when receive returns.

    python3 tests/test-nr-hold.py          ("WRONG" marks a failure)
"""
import importlib.util, os, sys, types, time
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
ok = lambda c: "OK" if c else "WRONG"
import numpy as np
try:
    import tkinter
except ImportError:                                    # only the NR class and one method are tested
    for n in ("tkinter", "tkinter.messagebox", "tkinter.simpledialog", "tkinter.ttk"):
        sys.modules[n] = mock.MagicMock()
spec = importlib.util.spec_from_file_location("ft991a_panel", os.path.join(ROOT, "ft991a-panel.py"))
fp = importlib.util.module_from_spec(spec); sys.modules["ft991a_panel"] = fp; spec.loader.exec_module(fp)

rate = 48000; rng = np.random.default_rng(1)
def noise(sec, amp):
    return (rng.normal(0, amp, int(rate * sec))).astype(np.int16).tobytes()
def run(nr, data):
    for i in range(0, len(data), 960): nr.process(data[i:i + 960])
def level(nr): return float(np.mean(nr.noise))

nr = fp.SpectralNR(rate, 10); nr.enabled = True
run(nr, noise(3, 300)); frames = []; nr.capture = frames; run(nr, noise(3, 300)); nr.capture = None
nr.build_profile(frames); base = level(nr)

# 1. transmitting (quiet audio), no hold: the profile falls
a = fp.SpectralNR(rate, 10); a.enabled = True; a.build_profile(frames)
run(a, noise(6, 5)); print("profile falls without a hold:", ok(level(a) < 0.3 * base))
# 2. with hold: it does not
b = fp.SpectralNR(rate, 10); b.enabled = True; b.build_profile(frames); b.hold = True
run(b, noise(6, 5)); print("profile kept with a hold:    ", ok(abs(level(b) - base) < 0.02 * base))
# 3. after release the noise is reduced as at first
b.hold = False; run(b, noise(2, 300))
out = []
for i in range(0, 48000, 960):
    d = noise(0.02, 300); out.append(np.frombuffer(b.process(d), np.int16).std())
print("noise still cut after a transmit:", ok(np.mean(out[10:]) < 0.8 * 300))

# 4. the panel's rule: PTT, Tune, CW keying or a waiting message hold; 1.5 s after, release
class S: pass
def mk(**kw):
    s = S(); s.nr = fp.SpectralNR(rate, 10); s.ptt_down = False; s.tune_deadline = 0.0
    s.NR_HOLD_AFTER = 1.5
    s.cw_window = types.SimpleNamespace(kb=types.SimpleNamespace(
        sender=types.SimpleNamespace(recently=lambda t: kw.get("key", False)),
        link=types.SimpleNamespace(waiting=kw.get("wait", False))))
    return s
upd = fp.FT991aPanel.nr_hold_update if hasattr(fp, "FT991aPanel") else None
if upd is None:
    cls = [v for v in vars(fp).values() if isinstance(v, type) and hasattr(v, "nr_hold_update")][0]; upd = cls.nr_hold_update
for name, kw, snap in (("PTT", {}, {"ptt": True}), ("Tune", {}, {"tuning": True}),
                       ("CW key", {"key": True}, {}), ("CW text waiting", {"wait": True}, {}), ("idle", {}, {})):
    s = mk(**kw); upd(s, snap); print(f"hold {name:16}", ok(s.nr.hold == (name != "idle")))
s = mk(key=True); upd(s, {}); s.cw_window.kb.sender.recently = lambda t: False; upd(s, {})
held = s.nr.hold; s._nr_tx_until = time.time() - 0.1; upd(s, {})
print("held just after, free later: ", ok(held and not s.nr.hold))
