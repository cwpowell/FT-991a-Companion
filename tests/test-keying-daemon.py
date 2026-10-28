#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
# SPDX-License-Identifier: GPL-3.0-or-later
# (The full GPL notice is in the file LICENSE.)
"""Tests for keying-daemon.py: Morse timing, the playout of timestamped key events over real UDP with jitter,
reordering and packet loss, every way the key is let go, bad packets, the cwdaemon-style text commands, the
serial line (pyserial's loop:// port) and a real process that receives SIGTERM. Nothing here touches a radio.

    python3 tests/test-keying-daemon.py          (a minute or so; "WRONG" marks a failure)
"""
import importlib.util, random, socket, struct, subprocess, sys, threading, time, signal, os
HERE = os.path.dirname(os.path.abspath(__file__)); DAEMON = os.path.join(os.path.dirname(HERE), "keying-daemon.py")
spec = importlib.util.spec_from_file_location("kd", DAEMON)
kd = importlib.util.module_from_spec(spec); sys.modules["kd"] = kd; spec.loader.exec_module(kd)
ok = lambda c: "OK" if c else "WRONG"
def packet(kind, events, seq=0, token=0, sent=0):
    return kd.HEAD.pack(kd.MAGIC, kd.VERSION, kind, token, seq, sent, len(events)) + b"".join(kd.EVENT.pack(t, int(s)) for t, s in events)
def new(buffer=0.15, max_down=3.0, dead_man=0.8):
    line = kd.NullKeyLine(); logs = []
    return kd.Engine(line, buffer, max_down, dead_man, log=logs.append), line, logs

print("--- 1. the Morse timing")
ev, total = kd.morse_events("PARIS PARIS", 20)
print(f"   'PARIS PARIS' at 20 wpm: {total:.3f} s (standard: 96 dots x 60 ms = 5.760 s)", ok(abs(total - 5.76) < 1e-9), "| starts with a key-down at 0:", ok(ev[0] == (0.0, True)), "| ends with key-up:", ok(ev[-1][1] is False))
dots = [round(b[0] - a[0], 6) for a, b in zip(ev, ev[1:])]
print("   every interval is 1, 3, 4 or 7... dots:", ok(set(round(d / 0.06) for d in dots) <= {1, 3, 5, 7}), sorted(set(round(d / 0.06) for d in dots)))

print("--- 2. text played through the engine keeps its timing (1 dot = 20 ms at 60 wpm)")
eng, line, logs = new(); eng.text("E T", 60); time.sleep(0.6); eng.shutdown()
segs = [(round((b[0] - a[0]) * 1000, 1), a[1]) for a, b in zip(line.log, line.log[1:])]
want = [(20, True), (20, False), (60 + 0, False)]    # E down 20, then up for 20+40+... checked below
downs = [d for d, s in segs if s]; ups = [d for d, s in segs if not s]
print("   key-down times (ms):", downs, "(wanted 20 and 60)", ok(len(downs) >= 2 and abs(downs[0] - 20) < 5 and abs(downs[1] - 60) < 5))
print("   the gap between E and T (ms):", ups[0] if ups else None, "(wanted 20+40+20=80... a letter gap of 3 dots plus the word gap of 4 more = 140)", ok(ups and abs(ups[0] - 140) < 6))

def play(events_ms, jitter_ms=0, loss=0.0, redundancy=6, ping_every=0.1, buffer=0.15, seed=1, lead_ms=400):
    """A sender at the operator's end: events [(ms, state)] sent over real UDP with jitter and loss. Every packet
    carries the sender's clock; heartbeats (pings with the last events) go out every 100 ms from before the
    first element, as the panel will do."""
    rnd = random.Random(seed)
    events_ms = [(t + lead_ms, st) for t, st in events_ms]
    eng, line, logs = new(buffer=buffer)
    lst = kd.EventListener(eng, "127.0.0.1", 0); lst.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); dest = ("127.0.0.1", lst.port)
    start = time.perf_counter() + 0.05
    def send(data): sock.sendto(data, dest)
    def at(when, fn):
        t = threading.Timer(max(0, when - time.perf_counter()), fn); t.daemon = True; t.start()
    for i, (t_ms, st) in enumerate(events_ms):
        recent = [(int(e[0]), e[1]) for e in events_ms[max(0, i - redundancy + 1): i + 1]]
        if rnd.random() >= loss:
            at(start + t_ms / 1000 + rnd.uniform(0, jitter_ms) / 1000, lambda d=packet(kd.T_EVENTS, recent, seq=i, sent=int(t_ms)): send(d))
    last_ms = events_ms[-1][0]; k = 0
    while k * ping_every * 1000 <= last_ms + 400:
        t_ms = int(k * ping_every * 1000)
        so_far = [(int(e[0]), e[1]) for e in events_ms if e[0] <= t_ms][-redundancy:]
        if rnd.random() >= loss:
            at(start + t_ms / 1000 + rnd.uniform(0, jitter_ms) / 1000, lambda d=packet(kd.T_PING, so_far, seq=1000 + k, sent=t_ms): send(d))
        k += 1
    time.sleep(start - time.perf_counter() + last_ms / 1000 + buffer + 0.5)
    lst.stop(); eng.shutdown(); sock.close()
    return line.log, eng.stats, logs, events_ms

def compare(log, events_ms):
    """The states come out in order, and each interval is the interval the operator made."""
    n = len(events_ms)
    in_order = [st for _, st in log][:n] == [st for _, st in events_ms]
    errs = [abs((log[i + 1][0] - log[i][0]) * 1000 - (events_ms[i + 1][0] - events_ms[i][0])) for i in range(n - 1)]
    return in_order, max(errs), sum(1 for e in errs if e > 5), len(errs)

ev_s, _ = kd.morse_events("CQ DE NK8O", 20)
base_events = [(int(round(t * 1000)), s) for t, s in ev_s]
print("--- 3. 'CQ DE NK8O' at 20 wpm over real UDP: the spacing the operator made is the spacing the radio gets")
for label, kw in (("no jitter", dict(jitter_ms=0)), ("jitter 0 to 80 ms (packets reordered)", dict(jitter_ms=80)), ("jitter 0 to 120 ms", dict(jitter_ms=120)),
                  ("2% of packets lost, jitter 0 to 60 ms", dict(jitter_ms=60, loss=0.02)), ("10% of packets lost, jitter 0 to 60 ms", dict(jitter_ms=60, loss=0.10)),
                  ("30% of packets lost (a very bad link)", dict(jitter_ms=60, loss=0.30)), ("jitter 0 to 300 ms (more than the buffer)", dict(jitter_ms=300))):
    log, stats, msgs, evs = play(base_events, **kw)
    in_order, worst, bad, total = compare(log, evs)
    strict = in_order and worst < 6 and bad == 0
    if "30%" in label or "300" in label:
        note = "a bad link, may suffer; the states must still be in order: " + ok(in_order)
    elif "10%" in label:
        note = ok(in_order and bad <= 3)          # a few late events are expected
    else:
        note = ok(strict)
    print(f"   {label:44s} in order={in_order}  worst spacing error {worst:5.1f} ms  ({bad} of {total} off by more than 5 ms)  late {stats['late']}  repeats dropped {stats['duplicates']}   {note}")
print("   key left up at the end:", ok(not log[-1][1]))
print("   (with the sender's clock in every packet and the offset frozen while keying, the first element is as good as the rest)")

print("--- 4. the key is let go when it should be")
eng, line, logs = new(dead_man=0.5); eng.feed([(1000, True)]); time.sleep(0.4)
print("   key is down after the buffer:", line.state, end=" | "); time.sleep(0.9)
print("no packets for 0.5 s: key up:", ok(not line.state), "| why:", logs[-1] if logs else None); eng.shutdown()
eng, line, logs = new(max_down=1.0, dead_man=5.0); eng.feed([(0, True)]); t0 = time.perf_counter()
while line.state is False and time.perf_counter() - t0 < 1: time.sleep(0.01)
t_down = time.perf_counter(); 
for i in range(30): eng.ping(); time.sleep(0.1)
print(f"   pings keep arriving but the key was down too long: released after {line.log[-1][0] - line.log[0][0]:.2f} s (max 1.0)", ok(not line.state and abs(line.log[-1][0] - line.log[0][0] - 1.0) < 0.2), "|", logs[-1] if logs else None); eng.shutdown()
eng, line, logs = new(); eng.feed([(0, True)]); time.sleep(0.4); n = len(eng.queue)
eng.feed([(100, False), (200, True)]); eng.abort(); print("   an abort: key up at once and nothing queued:", ok(not line.state and not eng.queue), "|", logs[-1]); eng.shutdown()
eng, line, logs = new(); eng.text("CQ " * 20, 15); time.sleep(0.5); held = line.state
eng.feed([(5000, True)]); time.sleep(0.05)
print("   key events while text is sending: the text is dropped:", ok(not any(e[3] == "text" for e in eng.queue)), "| text held the key?", held, "|", logs[-1] if logs else None); eng.shutdown()
eng, line, logs = new(); eng.feed([(0, True)]); time.sleep(0.4); eng.shutdown(); print("   the daemon stopping with the key down: key up:", ok(not line.state))
eng, line, logs = new(); eng.feed([(0, True)]); time.sleep(0.4); eng.feed([(0, True)]); eng.feed([(0, True), (10, False)])
print("   a repeated event is dropped, not played twice:", eng.stats["duplicates"], ok(eng.stats["duplicates"] == 2)); eng.shutdown()

print("--- 5. bad packets, a wrong token and an allow-list change nothing")
eng, line, logs = new(); lst = kd.EventListener(eng, "127.0.0.1", 0, token=4242); lst.start(); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); d = ("127.0.0.1", lst.port)
for data in (b"", b"hello", packet(kd.T_EVENTS, [(0, True)], token=1), packet(kd.T_EVENTS, [(0, True)], token=4242)[:-2], packet(9, [], token=4242), kd.HEAD.pack(b"XX", 1, 1, 4242, 0, 0, 0), packet(kd.T_EVENTS, [(0, True)], token=0)):
    s.sendto(data, d)
time.sleep(0.5)
print("   7 bad packets: key never moved:", ok(not line.log), "| counted as bad:", eng.stats["bad"], ok(eng.stats["bad"] == 7))
s.sendto(packet(kd.T_EVENTS, [(0, True)], token=4242), d); time.sleep(0.4); s.sendto(packet(kd.T_ABORT, [], token=4242), d); time.sleep(0.2)
print("   the right token is accepted (key went down), and the abort lets it go:", ok(any(st for _, st in line.log) and not line.state)); lst.stop(); eng.shutdown()
eng, line, logs = new(); lst = kd.EventListener(eng, "127.0.0.1", 0, allow={"10.9.9.9"}); lst.start(); s.sendto(packet(kd.T_EVENTS, [(0, True)]), ("127.0.0.1", lst.port)); time.sleep(0.5)
print("   an address that is not on the allow-list is ignored:", ok(not line.log)); lst.stop(); eng.shutdown(); s.close()

print("--- 6. cwdaemon-style text over UDP")
eng, line, logs = new(); tl = kd.TextListener(eng, "127.0.0.1", 0, wpm=24); tl.start(); c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); c.settimeout(3); d = ("127.0.0.1", tl.port)
c.sendto(b"\x1b260", d); c.sendto(b"\x1bhDONE", d); t0 = time.perf_counter(); c.sendto(b"E T", d)
try: reply = c.recvfrom(100)[0]; took = time.perf_counter() - t0
except socket.timeout: reply, took = None, None
print(f"   ESC 2 60, ESC h DONE, then 'E T': reply {reply!r} after {took and round(took, 2)} s (the message is 14 dots of 20 ms plus the 20 ms start = 0.30 s)", ok(reply == b"hDONE\r\n" and 0.2 < took < 0.5))
c.sendto(b"\x1b210", d); c.sendto(b"\x1bhNEVER", d); c.sendto(b"CQ CQ CQ CQ CQ", d); time.sleep(0.5); c.sendto(b"\x1b4", d); time.sleep(0.2)
try: c.settimeout(1.0); r2 = c.recvfrom(100)[0]
except socket.timeout: r2 = None
print("   an abort in the middle: key up:", ok(not line.state), "| no reply is sent for an aborted message:", ok(r2 is None)); tl.stop(); eng.shutdown(); c.close()

print("--- 7. the serial line (the real pyserial code, on its loop:// test port)")
try:
    import serial
except ImportError:
    serial = None
    print("   pyserial is not installed (pip install pyserial): this part is skipped")
if serial:
    sl = kd.SerialKeyLine("loop://", "dtr")
    print("   just opened: the line is low:", ok(sl.ser.dsr is False))
    sl.set(True); a = sl.ser.dsr; sl.set(False); b = sl.ser.dsr
    print("   DTR on -> the loop reads", a, "; DTR off ->", b, ok(a is True and b is False)); sl.set(True); sl.close()
    eng = kd.Engine(kd.SerialKeyLine("loop://", "dtr"), 0.05); eng.text("E", 60); time.sleep(0.03); mid = eng.line.ser.dsr; time.sleep(0.2)
    print("   an engine keying the serial line: down during the dot:", mid, "| up afterwards:", eng.line.ser.dsr, ok(mid is True and eng.line.ser.dsr is False)); eng.shutdown()

print("--- 8. a real process: SIGTERM with the key down lets it go")
p = subprocess.Popen([sys.executable, "-u", DAEMON, "--null", "--verbose", "--port", "6791", "--text-port", "0", "--bind", "127.0.0.1", "--buffer-ms", "20"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(1.0); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(packet(kd.T_EVENTS, [(0, True)]), ("127.0.0.1", 6791)); time.sleep(0.3)
p.send_signal(signal.SIGTERM); out = p.communicate(timeout=5)[0]
downs = [l for l in out.splitlines() if "key DOWN" in l]; ups = [l for l in out.splitlines() if " key up" in l]
print("   key went down:", ok(bool(downs)), "| went up when stopped:", ok(bool(ups)), "| said why:", ok("the daemon is stopping" in out), "| exit code", p.returncode, ok(p.returncode == 0))
