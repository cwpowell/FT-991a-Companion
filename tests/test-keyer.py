#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
# SPDX-License-Identifier: GPL-3.0-or-later
# (The full GPL notice is in the file LICENSE.)
"""Tests for the keyer in ft991a-panel.py: the iambic A and B decisions, real bursts and the sidetone's timing, the
straight key, the MIDI handler with its timing and clock, and serial paddle lines (pyserial's loop:// port).
They need no window and touch no radio.

    python3 tests/test-keyer.py          ("WRONG" marks a failure; it needs pyserial)
"""
import importlib.util, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, filename))
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
fp = _load("ft991a_panel", "ft991a-panel.py")
import importlib.util, sys, threading, time
ok = lambda c: "OK" if c else "WRONG"
class FakeSender:
    def __init__(self): self.edges, self.aborts = [], 0
    def edge(self, down, at=None): self.edges.append((time.perf_counter() if at is None else at, bool(down)))
    def abort(self): self.aborts += 1
class FakeTone:
    def __init__(self): self.log = []
    def key(self, down): self.log.append((time.perf_counter(), bool(down)))
def quiet_keyer(mode="Iambic B", wpm=20):
    """a keyer whose thread has stopped: only the decision logic is used"""
    k = fp.Keyer(FakeSender(), None); k.mode = mode; k.set_speed(wpm); k._closed = True; k.queue.put(None); time.sleep(0.15); return k
T = 1000.0                 # an arbitrary time
def press(k, ev, t): k.paddle(ev, T + t)
D, U, A, B = fp.DIT_DOWN, fp.DIT_UP, fp.DAH_DOWN, fp.DAH_UP

print("--- 1. the iambic decisions (20 wpm: dit 60 ms, dah 180 ms, gap 60 ms)")
k = quiet_keyer(); press(k, D, 0)
print("   a dit held:", k.choose_element(None, None, None, T + 0.001), "then", k.choose_element(".", T, T, T + 0.12), ok(k.choose_element(None, None, None, T + 0.001) == "." and k.choose_element(".", T, T, T + 0.12) == "."))
k = quiet_keyer(); press(k, A, 0)
print("   a dah held:", k.choose_element(None, None, None, T + 0.001), "then", k.choose_element("-", T, T, T + 0.24), ok(k.choose_element(None, None, None, T + 0.001) == "-" and k.choose_element("-", T, T, T + 0.24) == "-"))
def squeeze_seq(first_ev, second_ev, mode, n=6):
    k = quiet_keyer(mode); press(k, first_ev, 0); press(k, second_ev, 0.002)
    seq = ""; prev = None; look = heard = None; start = 0.003
    for i in range(n):
        elt = k.choose_element(prev, look, heard, T + start + (0.001 if prev is None else 0))
        seq += elt; look, heard = T + start - 0.003, T + start; prev = elt; start += (0.06 if elt == "." else 0.18) + 0.06
    return seq
for mode in ("Iambic A", "Iambic B"):
    s1 = squeeze_seq(D, A, mode); s2 = squeeze_seq(A, D, mode)
    print(f"   {mode}: squeeze, dit closed first: {s1}   dah closed first: {s2}   ", ok(s1 == ".-.-.-" and s2 == "-.-.-."))
k = quiet_keyer(); press(k, D, 0); press(k, A, 0)
print("   both closed at the same moment: starts with", k.choose_element(None, None, None, T + 0.001), ok(k.choose_element(None, None, None, T + 0.001) == "."))
print("   -- a squeeze let go of during a dit (the difference between A and B)")
for mode, want in (("Iambic A", None), ("Iambic B", "-")):
    k = quiet_keyer(mode); press(k, D, -0.2); press(k, A, -0.19); press(k, U, 0.03); press(k, B, 0.03)    # a dit began at 0; both released at 30 ms
    got = k.choose_element(".", T, T, T + 0.12)
    print(f"      {mode}: the element after it: {got!r} (wanted {want!r})", ok(got == want))
print("   -- a dit tapped during a dah is remembered, in both modes")
for mode in ("Iambic A", "Iambic B"):
    k = quiet_keyer(mode); press(k, A, -0.1); press(k, D, 0.05); press(k, U, 0.09); press(k, B, 0.15)
    got = k.choose_element("-", T - 0.0, T, T + 0.24)
    print(f"      {mode}: after the dah: {got!r}", ok(got == "."))
print("   -- iambic B's dit memory delay: a squeeze released EARLY in a dah adds no dit, LATE adds one")
for t_release, want in ((0.05, None), (0.12, ".")):
    k = quiet_keyer("Iambic B"); press(k, A, -0.2); press(k, D, -0.19); press(k, U, t_release); press(k, B, t_release)
    got = k.choose_element("-", T - 0.001, T, T + 0.24)
    print(f"      released {int(t_release * 1000)} ms into the dah (the delay is 33% of dah+gap = 79 ms): {got!r}", ok(got == want))

print("--- 2. a real burst: the edges, and when the sidetone sounds")
def burst(mode, events, wait, wpm=40):
    snd, tone = FakeSender(), FakeTone(); k = fp.Keyer(snd, tone); k.mode = mode; k.set_speed(wpm)
    t0 = time.perf_counter()
    for at, ev in events: 
        threading.Timer(at, lambda e=ev: k.paddle(e)).start()
    time.sleep(wait); k.close(); return snd, tone, t0, k
snd, tone, t0, k = burst("Iambic B", [(0.05, D), (0.25, U)], 0.8)
downs = [t for t, d in snd.edges if d]; ups = [t for t, d in snd.edges if not d]
print("   a dit held 200 ms at 40 wpm (dit 30 ms, gap 30 ms):", len(downs), "dits;", "starts", [round((x - downs[0]) * 1000, 1) for x in downs], "ms; each lasts", sorted({round((u - d) * 1000, 1) for d, u in zip(downs, ups)}), "ms", ok(len(downs) == 4 and all(abs((downs[i + 1] - downs[i]) * 1000 - 60) < 0.5 for i in range(3)) and all(abs((u - d) * 1000 - 30) < 0.5 for d, u in zip(downs, ups))))
heard = [t for t, d in tone.log if d]
print("   the sidetone came on at the same moments (worst difference from the edge times):", round(max(abs(h - d) for h, d in zip(heard, downs)) * 1000, 2), "ms", ok(len(heard) == 4 and max(abs(h - d) for h, d in zip(heard, downs)) < 0.004))
snd, tone, t0, k = burst("Iambic B", [(0.05, D), (0.055, A), (0.55, U), (0.555, B)], 1.3)
seq = "".join("." if abs((u - d) * 1000 - 30) < 2 else "-" for (d, u) in zip([t for t, x in snd.edges if x], [t for t, x in snd.edges if not x]))
seqB = seq
print("   a squeeze of 500 ms at 40 wpm sends:", seq, "(alternating)", ok(seq.startswith(".-.-.-")))
snd, tone, t0, k = burst("Iambic A", [(0.05, D), (0.055, A), (0.55, U), (0.555, B)], 1.3)
seqA = "".join("." if abs((u - d) * 1000 - 30) < 2 else "-" for (d, u) in zip([t for t, x in snd.edges if x], [t for t, x in snd.edges if not x]))
print("   the same in iambic A:", seqA, "-> iambic B added exactly one more element (a dit after the last dah) when the squeeze was released:", ok(seqB == seqA + "."))
snd, tone, t0, k = burst("Iambic B", [(0.05, D), (0.45, U)], 1.0)
snd2 = FakeSender(); k2 = fp.Keyer(snd2, FakeTone()); k2.set_speed(20); k2.paddle(D); time.sleep(0.1); k2.abort(); time.sleep(0.3)
print("   an abort in the middle: the burst stops, edges sent after it:", len([1 for t, d in snd2.edges if t > time.perf_counter()]), "| abort passed to the sender:", snd2.aborts, ok(snd2.aborts >= 1)); k2.close()

print("--- 3. a straight key: edges straight through, nothing doubled")
snd, tone = FakeSender(), FakeTone(); k = fp.Keyer(snd, tone); k.straight = True
k.paddle(D, T + 0.00); k.paddle(D, T + 0.01); k.paddle(U, T + 0.10); k.paddle(U, T + 0.11); k.paddle(D, T + 0.30)
print("   edges:", [(round(t - T, 2), d) for t, d in snd.edges], ok([d for t, d in snd.edges] == [True, False, True]), "| sidetone follows:", tone.log[-1][1]); k.release()
print("   release() lets go a key that is down:", ok(snd.edges[-1][1] is False)); k.close()

print("--- 4. MIDI (messages as python-rtmidi hands them over: (message, delta seconds))")
class Cap:
    def __init__(self): self.ev = []
    def paddle(self, e, ts=None): self.ev.append((e, ts))
    def release(self): pass
def rel(cap): b = cap.ev[0][1]; return [(e, round((t - b) * 1000)) for e, t in cap.ev]
cap = Cap(); m = fp.MidiPaddles(cap, [20], [21])
m.handle([0x90, 20, 0], 0.0); time.sleep(0.1); m.handle([0x80, 20, 100], 0.0)       # on time: the device's clock is learnt
time.sleep(0.4)                                                                     # then the computer stalls...
m.handle([0x90, 21, 60], 0.0); m.handle([0x80, 21, 120], 0.0); m.handle([0x90, 20, 30], 0.0)   # ...and these arrive together
print("   messages that arrived together after a stall keep the device's own spacing:", rel(cap), "(wanted 0, 100, 160, 280, 310)", ok([x[1] for x in rel(cap)] == [0, 100, 160, 280, 310]))
cap = Cap(); m = fp.MidiPaddles(cap, [20], [21]); m.handle([0x90, 20, 0], 0.0); time.sleep(0.4); m.handle([0xB0, 1, 3], 0.0); m.handle([0x80, 20, 20], 0.0)
print("   a control change of 3 (0.378 s) plus a note timed 20 ms is a gap of 398 ms:", rel(cap), ok(rel(cap)[1][1] == 398))
cap = Cap(); m = fp.MidiPaddles(cap, [20], [21]); m.handle([0x90, 55, 0], 0.0); m.handle([0x80, 55, 0], 0.0); m.handle([0x80, 20, 0], 0.0)
print("   a note that is not a paddle, and a key up with no key down, are ignored:", ok(cap.ev == []))
cap = Cap(); m = fp.MidiPaddles(cap, [0, 1, 2], []); m.handle([0x90, 2, 0], 0.0); m.handle([0x80, 2, 80], 0.0)
print("   a Vail adapter keying by itself (notes 0, 1, 2 are all the key):", [e for e, _ in cap.ev], ok([e for e, _ in cap.ev] == [fp.DIT_DOWN, fp.DIT_UP]))
cap = Cap(); m = fp.MidiPaddles(cap, [1], [2]); m.handle([0x90, 1, 0], 0.0); m.handle([0x90, 2, 20], 0.0); m.handle([0x80, 1, 30], 0.0); m.handle([0x80, 2, 10], 0.0)
print("   a Vail adapter in passthrough (notes 1 and 2 are the paddles):", [e for e, _ in cap.ev], ok([e for e, _ in cap.ev] == [fp.DIT_DOWN, fp.DAH_DOWN, fp.DIT_UP, fp.DAH_UP]))

print("--- 5. serial lines (pyserial's loop:// port: DTR is read back as DSR and RTS as CTS)")
import serial
def loop(): return serial.serial_for_url("loop://", do_not_open=False)
port = loop(); port.dtr = True; port.rts = True                      # both lines asserted at rest
cap = Cap(); sp = fp.SerialPaddles(cap, port, "DSR", "CTS"); time.sleep(0.2)
print("   lines as they stand when the port opens are the resting state, not a press:", ok(cap.ev == []))
port.dtr = False; time.sleep(0.05); port.dtr = True; time.sleep(0.05); port.rts = False; time.sleep(0.05); port.rts = True; time.sleep(0.1)
print("   this port RESTS HIGH, so a line pulled low is a key closing (and back high, opening):", [e for e, _ in cap.ev], ok([e for e, _ in cap.ev] == [fp.DIT_DOWN, fp.DIT_UP, fp.DAH_DOWN, fp.DAH_UP]))
gaps = [round((cap.ev[i + 1][1] - cap.ev[i][1]) * 1000) for i in range(3)]; print("   spacing of those changes (ms, wanted about 50):", gaps, ok(all(40 <= g <= 70 for g in gaps))); sp.close()
port = loop(); cap = Cap(); sp = fp.SerialPaddles(cap, port, "DSR", "CTS", straight=True); time.sleep(0.1)
port.dtr = False; time.sleep(0.004); port.dtr = True; time.sleep(0.004); port.dtr = False; time.sleep(0.15)
print("   a straight key that bounces (closed, open, closed within 8 ms) is one key down:", [e for e, _ in cap.ev], ok([e for e, _ in cap.ev] == [fp.DIT_DOWN]))
port.dtr = True; time.sleep(0.15); print("   and its release:", [e for e, _ in cap.ev], ok([e for e, _ in cap.ev] == [fp.DIT_DOWN, fp.DIT_UP])); sp.close()
port = serial.serial_for_url("loop://"); port.dtr = False; port.rts = False; cap = Cap(); sp = fp.SerialPaddles(cap, port, "DSR", "CTS"); time.sleep(0.1)
port.dtr = True; time.sleep(0.05); port.dtr = False; time.sleep(0.1)
print("   a port that RESTS LOW works the other way, and just as well:", [e for e, _ in cap.ev], ok([e for e, _ in cap.ev] == [fp.DIT_DOWN, fp.DIT_UP])); sp.close()

print("--- 6. the Monitor: it reports what a device sends, and keys nothing")
reports = []
port = serial.serial_for_url("loop://"); port.dtr = False; port.rts = True
mon = fp.DeviceMonitor(reports.append); mon.start_serial(port); time.sleep(0.05)
print("   at the start it tells where the four lines rest:", reports[0][:95], ok("DSR off" in reports[0] and "CTS ON" in reports[0]))
port.dtr = True; time.sleep(0.03); port.rts = False; time.sleep(0.03); mon.stop()
print("   then every change:", [r.split("   ")[0] for r in reports[1:]], ok([r.split("   ")[0] for r in reports[1:]] == ["Serial: DSR ON", "Serial: CTS off"]))
print("   MIDI messages are described in words:", fp.DeviceMonitor.describe_midi([0x90, 21, 7]), "|", fp.DeviceMonitor.describe_midi([0x80, 20, 0]), "|", fp.DeviceMonitor.describe_midi([0xB0, 1, 30]), "|", fp.DeviceMonitor.describe_midi([0xC0, 8]), ok(fp.DeviceMonitor.describe_midi([0x90, 21, 7]) == "note ON  note 21 velocity 7"))
reports = []; mon = fp.DeviceMonitor(reports.append); mon._midi(([0x90, 2, 5], 0.012), None)
print("   a MIDI message as the library delivers it:", reports, ok("note ON  note 2 velocity 5" in reports[0] and "12 ms" in reports[0]))

print("--- 7. the Vail adapter is told to use MIDI mode (without that it sends keystrokes and nothing reaches the program)")
import rtmidi
fp.MidiSupport.result = (True, "", [], [])           # as if the probe had passed (these tests use a fake MidiOut)
sent_by_fake = []
class FakeOut:
    def get_ports(self): return ["Other MIDI 1", "Vail Adapter MIDI 1", "Vail Adapter MIDI 2"]
    def open_port(self, i): sent_by_fake.append(("open", i))
    def send_message(self, m): sent_by_fake.append(list(m))
    def close_port(self): sent_by_fake.append("closed")
real_out = rtmidi.MidiOut; rtmidi.MidiOut = FakeOut
v = fp.VailAdapterOut(); v.open("Vail Adapter MIDI 1")
print("   the output port with the best matching name was opened:", sent_by_fake[0], ok(sent_by_fake[0] == ("open", 1)))
v.set_state(20, 600, "Passthrough")
print("   set-up at 20 wpm, 600 Hz, passthrough:", sent_by_fake[1:], ok(sent_by_fake[1:] == [[0xB0, 0, 0], [0xB0, 2, 74], [0xB0, 1, 30], [0xC0, 0]]))
del sent_by_fake[:]; v.set_state(30, 700, "Iambic B"); print("   its own keyer, iambic B at 30 wpm, 700 Hz:", sent_by_fake, ok(sent_by_fake == [[0xB0, 0, 0], [0xB0, 2, 77], [0xB0, 1, 20], [0xC0, 8]]))
del sent_by_fake[:]; v.set_speed(40); print("   a new speed (40 wpm):", sent_by_fake, ok(sent_by_fake == [[0xB0, 1, 15]]))
v.close(); rtmidi.MidiOut = real_out
try: fp.VailAdapterOut().open("Nothing like it") if False else None
except Exception: pass
rtmidi.MidiOut = FakeOut
try: fp.VailAdapterOut().open("Zzz"); print("   no matching output port: WRONG")
except RuntimeError as e: print("   no matching output port says so:", str(e)[:70], "OK")
rtmidi.MidiOut = real_out

print("--- 8. a paddle held for far too long is let go")
snd, tone = FakeSender(), FakeTone(); fp.MAX_BURST = 0.5; k = fp.Keyer(snd, tone); k.set_speed(40); said = []; k.log = said.append
k.paddle(D); time.sleep(1.2); edges = len(snd.edges); time.sleep(0.4)
print("   after 0.5 s (the limit set for this test) the keyer stopped on its own:", "edges", edges, "->", len(snd.edges), "| abort sent:", snd.aborts >= 1, "|", [m[:60] for m in said], ok(len(snd.edges) == edges and snd.aborts >= 1 and any("stuck" in m for m in said)))
fp.MAX_BURST = 30.0; k.close()

print("--- 9. python-rtmidi is a native library that can kill the whole program: it is only ever asked in a separate process first")
ABORT = [sys.executable, "-c", "import os; os.abort()"]
def fresh(command=None, timeout=20): fp.MidiSupport.result = None; fp.MidiSupport.command = command; fp.MidiSupport.timeout = timeout
fresh(); ok_, msg, pin, pout = fp.MidiSupport.check()
print("   the real probe (this program run with --rtmidi-probe):", "worked, input ports" if ok_ else "reported", pin if ok_ else msg[:110], ok(ok_ or ("could not start" in msg and "crashed" not in msg)))
print("   (where the library cannot start, e.g. no ALSA MIDI in a container, the reason is given plainly, with no advice about the Python version)")
fresh(ABORT); ok_, msg, pin, pout = fp.MidiSupport.check()
print("   a library that aborts the process (this test is still running, so the crash stayed in the probe):", ok_, ok(ok_ is False and "code" in msg))
print("      the message:", msg[:150])
print("   it says what still works, and advises (about the Python version only on 3.14 or later):", ok("serial paddles" in msg and ("pip install" in msg)))
print("   the port list is simply empty:", fp.MidiPaddles.ports(), ok(fp.MidiPaddles.ports() == []))
for what, call in (("MidiPaddles.open", lambda: fp.MidiPaddles(Cap(), [20], [21]).open("x")), ("VailAdapterOut.open", lambda: fp.VailAdapterOut().open("x")), ("DeviceMonitor.start_midi", lambda: fp.DeviceMonitor(lambda m: None).start_midi("x"))):
    try: call(); print(f"   {what}: went ahead: WRONG")
    except RuntimeError as exc: print(f"   {what}: refused with the reason: OK ({str(exc)[:55]}...)")
fresh([sys.executable, "-c", "import rtmidi_that_is_not_there"]); ok_, msg, _a, _b = fp.MidiSupport.check()
print("   a library that is not installed says so:", msg[:80], ok(not ok_ and "not installed" in msg))
fresh([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1); t0 = time.time(); ok_, msg, _a, _b = fp.MidiSupport.check()
print(f"   a probe that hangs is given up on after {time.time() - t0:.1f} s:", msg[:60], ok(not ok_ and time.time() - t0 < 3))
fresh(); fp.MidiSupport.check(); print("   and the real probe gives an answer again afterwards:", ok(fp.MidiSupport.result is not None))

print("--- 10. reading a key must never drive an output line (opening a port raised DTR and RTS, which keyed a transmitter)")
import serial
p = fp.open_serial_input("loop://")
print("   a port opened to read: DTR", p.dtr, "RTS", p.rts, ok(p.dtr is False and p.rts is False)); p.close()
p = fp.open_serial_input("loop://", drive_dtr=True, drive_rts=False)
print("   with 'drive DTR' switched on, for an interface that needs it for power: DTR", p.dtr, "RTS", p.rts, ok(p.dtr is True and p.rts is False)); p.close()
cap = Cap(); sp = fp.SerialPaddles(cap, "loop://", "DSR", "CTS"); time.sleep(0.15)
print("   the paddle reader opens its own port with both low, and keeps them low while it reads:", sp.port.dtr, sp.port.rts, ok(sp.port.dtr is False and sp.port.rts is False))
sp.port.dtr = True; time.sleep(0.05); sp.port.dtr = False; time.sleep(0.1)
print("   (a port that rests LOW: the line going high is a key closing)", [e for e, _ in cap.ev], ok([e for e, _ in cap.ev] == [fp.DIT_DOWN, fp.DIT_UP])); sp.close()
reports = []; mon = fp.DeviceMonitor(reports.append); mon.start_serial("loop://"); time.sleep(0.15)
print("   the Monitor too:", mon.port.dtr, mon.port.rts, ok(mon.port.dtr is False and mon.port.rts is False), "| at rest it says:", reports[0][34:80])
mon.port.rts = True; time.sleep(0.05); mon.port.rts = False; time.sleep(0.05); mon.port.dtr = True; time.sleep(0.05)
print("   it records which lines closed, in order:", mon.lines_seen, "| changes counted:", mon.count, ok(mon.lines_seen == ["CTS", "DSR"] and mon.count == 3)); mon.stop()
mon = fp.DeviceMonitor(lambda m: None); mon.start_serial("loop://", drive_dtr=True, drive_rts=True)
print("   with both switched on the Monitor drives them:", mon.port.dtr, mon.port.rts, ok(mon.port.dtr and mon.port.rts)); mon.stop()

print("--- 11. the Monitor can listen to every MIDI input at once, and records the notes in order")
import rtmidi
class FakeIn:
    made = []
    def __init__(self, *a, **k): FakeIn.made.append(self); self.cb = None; self.opened = None
    def get_ports(self): return ["Midi Through:Midi Through Port-0 14:0", "Vail Adapter:Vail Adapter MIDI 1 24:0", "HaliKey:HaliKey MIDI 28:0"]
    def open_port(self, i): self.opened = i
    def set_callback(self, cb): self.cb = cb
    def cancel_callback(self): self.cb = None
    def close_port(self): self.opened = None
class FakeOut2:
    def get_ports(self): return ["Vail Adapter:Vail Adapter MIDI 1 24:0"]
    def open_port(self, i): pass
    def send_message(self, m): pass
    def close_port(self): pass
real_in, real_out = rtmidi.MidiIn, rtmidi.MidiOut; rtmidi.MidiIn, rtmidi.MidiOut = FakeIn, FakeOut2
fp.MidiSupport.result = (True, "", [], [])
reports = []; mon = fp.DeviceMonitor(reports.append); mon.start_midi(fp.ALL_MIDI)
opened = [m for m in FakeIn.made if m.opened is not None]
print("   every input was opened:", sorted(m.opened for m in opened), ok(sorted(m.opened for m in opened) == [0, 1, 2]))
halikey = [m for m in opened if m.opened == 2][0]; vail = [m for m in opened if m.opened == 1][0]
halikey.cb(([0x90, 7, 0], 0.0)); halikey.cb(([0x80, 7, 12], 0.01)); vail.cb(([0x90, 2, 0], 0.0)); halikey.cb(([0x90, 9, 4], 0.02))
print("   each message says which port it came from:", [r[:62] for r in reports if r.startswith("MIDI in")][:2], ok(any("[HaliKey:HaliKey MIDI 28:0]" in r for r in reports) and any("[Vail Adapter:Vail Adapter MIDI 1 24:0]" in r for r in reports)))
print("   the notes seen, in the order they first came on:", mon.notes_seen, "| messages:", mon.count, ok(mon.notes_seen == [7, 2, 9] and mon.count == 4))
mon.stop(); print("   stopping closes them all:", ok(all(m.opened is None for m in FakeIn.made)))
reports = []; FakeIn.made.clear(); mon = fp.DeviceMonitor(reports.append); mon.start_midi("HaliKey:HaliKey MIDI 28:0")
print("   one chosen port opens only that one:", [m.opened for m in FakeIn.made if m.opened is not None], ok([m.opened for m in FakeIn.made if m.opened is not None] == [2])); mon.stop()
rtmidi.MidiIn, rtmidi.MidiOut = real_in, real_out

print("--- 12. a MIDI note that is not in the box is said so, not dropped in silence")
said = []; k = type("K", (), {"log": said.append, "paddle": lambda self, *a, **kw: said.append("PADDLE")})(); m = fp.MidiPaddles(k, [20], [21])
m.handle([0x90, 5, 0], 0.0); m.handle([0x80, 5, 20], 0.0)
print("   a note of 5 when the box says 20 and 21:", [x[:90] for x in said], ok(len(said) == 1 and "note 5 is not one of the notes" in said[0] and "Use what was seen" in said[0]))
m.handle([0x90, 20, 0], 0.0); print("   a note that is in the box still keys:", ok("PADDLE" in said))
