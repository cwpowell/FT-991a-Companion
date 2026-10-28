#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""keying-daemon.py - keys a transmitter from far away, with the timing kept exactly.

It runs on the radio's computer and drives one control line (DTR by default) of a serial port, such as
the second USB serial port of an FT-991A (set the radio's menu 060 PC KEYING to DTR).

Two kinds of request arrive over UDP, and both end up on the same line:

  Key events (default port 6790). A paddle or a key at the operator's end is turned into "down" and
  "up" events, each stamped with the sender's own clock. The daemon plays them back after a fixed delay
  (the jitter buffer, --buffer-ms), so the spacing of the dots and dashes is the spacing the operator
  made, whatever the network did in between. This is how RemoteRig keeps its timing. Each packet repeats
  the last few events, so a lost packet is repaired by the next one, and packets keep arriving while the
  key is down, so that a broken connection is noticed.

  Text (default port 6789, the port cwdaemon uses). The daemon makes the Morse itself, with the usual
  timing, and understands cwdaemon's escape codes: ESC 0 reset, ESC 2 <wpm> speed, ESC 4 abort and
  ESC h <token> (the token is sent back when the following text has finished). So a program that talks to
  cwdaemon talks to this one, and only one program has to own the serial line.

The key is let go, and the daemon says why, when: packets stop while the key is down (--dead-man), the key
has been down too long (--max-down), an abort packet arrives, key events arrive while text is being sent
(the operator's hand wins), or the daemon stops. The line is low when the port is opened.

    python3 keying-daemon.py --device /dev/ttyUSB1 --bind 100.x.y.z
    python3 keying-daemon.py --null --verbose            # no serial port: just show what it would do

Packet layout for key events (big-endian):  b"KL", version (1), type (1 events, 2 ping, 3 abort),
token (16 bits, 0 = none), sequence (32 bits), the sender's clock when the packet was sent (ms, 32
bits), count (8 bits), then count times: time of the event on the sender's clock (ms, 32 bits) and its
state (8 bits, 1 = down, 0 = up). A ping has the same layout and may repeat recent events, and an abort
has no events. EVERY packet carries the sender's clock, so the daemon learns how long packets take
before the first element arrives: a sender should send a ping every 100 ms or so while the keyer is in
use, and while the key is down. The daemon learns the delay from the fastest packets of the last 10 s,
and keeps it fixed while keying is going on, so that the spacing of the elements cannot be disturbed.
"""
import argparse
import collections
import heapq
import itertools
import signal
import socket
import struct
import sys
import threading
import time

MAGIC, VERSION = b"KL", 1
T_EVENTS, T_PING, T_ABORT = 1, 2, 3
HEAD = struct.Struct(">2sBBHIIB")
EVENT = struct.Struct(">IB")
ESC = "\x1b"
OFFSET_WINDOW = 10.0      # seconds of packets the clock offset is learnt from
IDLE_FOR = 1.5            # the offset is only refreshed after a real pause (the end of an over), never inside a word
CLOCK_JUMP_MS = 2000      # a sender clock that goes back by this much has restarted

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....",
    "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.",
    "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
    "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", ":": "---...", ";": "-.-.-.",
    "'": ".----.", '"': ".-..-.", "@": ".--.-.", ")": "-.--.-",
    "=": "-...-", "*": ".-.-.", "<": "...-.-", "(": "-.--.", "!": "...-.", "&": ".-...", ">": "-...-.-",
}


def morse_events(text, wpm):
    """Text as (seconds from the start, down or up) events and the total time. A dot lasts 1.2/wpm
    seconds, a dash three dots, the gap inside a letter one dot, between letters three, between words
    seven."""
    dot = 1.2 / max(4, wpm)
    t, events, first = 0.0, [], True
    for word in text.upper().split():
        if not first:
            t += 4 * dot                        # the word gap: 3 dots were already added after the last letter
        first = False
        for ch in word:
            pattern = MORSE.get(ch)
            if not pattern:
                continue
            for el in pattern:
                events.append((t, True))
                t += dot if el == "." else 3 * dot
                events.append((t, False))
                t += dot
            t += 2 * dot                        # with the dot above: three between letters
    return events, t


class NullKeyLine:
    """A line that keys nothing and remembers what it was told (for tests, and for --null)."""

    def __init__(self, log_path=None, verbose=False):
        self.log, self.state, self.verbose = [], False, verbose
        self.file = open(log_path, "w", buffering=1) if log_path else None

    def set(self, state):
        self.state = bool(state)
        stamp = time.perf_counter()
        self.log.append((stamp, self.state))
        if self.file:
            self.file.write(f"{stamp:.6f} {int(self.state)}\n")
        if self.verbose:
            print(f"{stamp:.3f} key {'DOWN' if state else 'up'}", flush=True)

    def close(self):
        self.set(False)
        if self.file:
            self.file.close()


class SerialKeyLine:
    """The DTR (or RTS) line of a serial port. The port is opened with the line low, so that opening
    it cannot key the radio."""

    def __init__(self, device, line="dtr"):
        import serial
        self.line = line.lower()
        if self.line not in ("dtr", "rts"):
            raise ValueError("the line must be dtr or rts")
        self.ser = serial.serial_for_url(device, do_not_open=True)
        self.ser.dtr = False                    # these are applied when the port is opened
        self.ser.rts = False
        self.ser.open()
        self.state = False
        self.set(False)

    def set(self, state):
        self.state = bool(state)
        setattr(self.ser, self.line, self.state)

    def close(self):
        try:
            self.set(False)
        finally:
            self.ser.close()


class Engine:
    """Plays timed key events on a line. Events from the network are held for the jitter buffer; text
    is timed locally. A safety loop lets the key go if anything looks wrong."""

    def __init__(self, line, buffer_s=0.15, max_down=3.0, dead_man=0.8, log=None):
        self.line, self.buffer, self.max_down, self.dead_man = line, buffer_s, max_down, dead_man
        self.log = log or (lambda msg: None)
        self.cv = threading.Condition()
        self.queue, self.counter = [], itertools.count()
        self.down_since, self.held_by = None, None          # when the key went down, and who put it down
        self.last_packet = time.perf_counter()
        self.offset, self.last_t, self.last_sent = None, None, None
        self.samples = collections.deque()                  # (arrival, how long the packet took), for the offset
        self.last_activity = 0.0                            # when a key event was last queued or played
        self.text_end = 0.0                                  # when the text that is queued will have been sent
        self.timers = []
        self.stats = dict(packets=0, pings=0, events=0, duplicates=0, late=0, max_late_ms=0.0, forced_up=0, bad=0)
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # ---- the line ---------------------------------------------------------------------------------
    def _set(self, state, source):
        if bool(state) == bool(self.line.state):
            return
        self.line.set(state)
        self.down_since = time.perf_counter() if state else None
        self.held_by = source if state else None

    def _force_up(self, why):
        if self.line.state:
            self.line.set(False)
            self.down_since, self.held_by = None, None
            self.stats["forced_up"] += 1
            self.log(f"key released: {why}")

    # ---- key events from the network ------------------------------------------------------------------
    @staticmethod
    def _newer(a, b):
        return a != b and ((a - b) & 0xFFFFFFFF) < 0x80000000

    def _sample(self, now, sent_ms):
        """Learn how long packets take from the sender's clock in every packet. The estimate is the
        fastest packet of the last few seconds, and it is only refreshed while the line is idle: a change
        in the middle of keying would move later elements against earlier ones."""
        if sent_ms is None:
            return
        if self.last_sent is not None and ((self.last_sent - sent_ms) & 0xFFFFFFFF) < 0x80000000 \
                and (self.last_sent - sent_ms) & 0xFFFFFFFF > CLOCK_JUMP_MS:
            self.samples.clear()                            # the sender restarted: start afresh
            self.offset, self.last_t = None, None
        self.last_sent = sent_ms
        self.samples.append((now, now - sent_ms / 1000.0))
        while self.samples and now - self.samples[0][0] > OFFSET_WINDOW:
            self.samples.popleft()
        busy = bool(self.queue) or self.line.state or now - self.last_activity < IDLE_FOR
        if self.offset is None or not busy:
            self.offset = min(d for _, d in self.samples)

    def feed(self, events, now=None, sent_ms=None):
        """Events are (milliseconds on the sender's clock, down?). Old ones (repeats) are dropped."""
        now = time.perf_counter() if now is None else now
        with self.cv:
            self.last_packet = now
            self.stats["packets"] += 1
            self._sample(now, sent_ms if sent_ms is not None else (events[-1][0] if events else None))
            fresh = [e for e in events if self.last_t is None or self._newer(e[0], self.last_t)]
            self.stats["duplicates"] += len(events) - len(fresh)
            if not fresh:
                return
            self._drop_source("text")                       # the operator's hand wins over a text message
            if self.line.state and self.held_by == "text":
                self._force_up("key events arrived while text was being sent")
            for t_ms, state in sorted(fresh, key=lambda e: (e[0] - fresh[0][0]) & 0xFFFFFFFF):
                sec = t_ms / 1000.0
                play_at = sec + self.offset + self.buffer
                if play_at < now:                           # it came too late for the buffer: play it now
                    late = now - play_at
                    self.stats["late"] += 1
                    self.stats["max_late_ms"] = max(self.stats["max_late_ms"], late * 1000)
                    play_at = now
                heapq.heappush(self.queue, (play_at, next(self.counter), bool(state), "events"))
                self.stats["events"] += 1
                self.last_t = t_ms
            self.last_activity = now
            self.cv.notify()

    def ping(self, now=None, sent_ms=None):
        with self.cv:
            now = time.perf_counter() if now is None else now
            self.last_packet = now
            self.stats["pings"] += 1
            self._sample(now, sent_ms)

    # ---- text -------------------------------------------------------------------------------------------
    def text(self, text, wpm, reply=None):
        """Key a text message; reply is called when it has finished."""
        events, total = morse_events(text, wpm)
        if not events:
            if reply:
                reply()
            return
        with self.cv:
            start = max(time.perf_counter() + 0.02, self.text_end)
            for dt, state in events:
                heapq.heappush(self.queue, (start + dt, next(self.counter), state, "text"))
            self.text_end = start + total
            if reply:
                timer = threading.Timer(max(0.0, self.text_end - time.perf_counter()), reply)
                timer.daemon = True
                self.timers.append(timer)
                timer.start()
            self.cv.notify()

    def _drop_source(self, source):
        self.queue = [e for e in self.queue if e[3] != source]
        heapq.heapify(self.queue)
        if source == "text":
            self.text_end = 0.0
            for t in self.timers:
                t.cancel()
            self.timers.clear()

    def abort(self, source=None):
        """Stop at once: everything (a network abort) or only the text (cwdaemon's ESC 4)."""
        with self.cv:
            if source is None:
                self.queue.clear()
                self.text_end = 0.0
                for t in self.timers:
                    t.cancel()
                self.timers.clear()
                self._force_up("abort")
            else:
                self._drop_source(source)
                if self.held_by == source:
                    self._force_up(f"{source} aborted")
            self.cv.notify()

    # ---- the player and the safety loop ------------------------------------------------------------------
    def _run(self):
        with self.cv:
            while self.running:
                now = time.perf_counter()
                self._watch(now)
                if self.queue:
                    wait = self.queue[0][0] - now
                    if wait <= 0:
                        _at, _n, state, source = heapq.heappop(self.queue)
                        self._set(state, source)
                        self.last_activity = now
                        continue
                    if wait < 0.003:                         # the last moments: spin, for accuracy
                        self.cv.release()
                        try:
                            end = self.queue[0][0] if self.queue else now
                            while time.perf_counter() < end:
                                pass
                        finally:
                            self.cv.acquire()
                        continue
                    self.cv.wait(min(wait - 0.002, 0.05))
                else:
                    self.cv.wait(0.05)

    def _watch(self, now):
        if not self.line.state:
            return
        if self.down_since is not None and now - self.down_since > self.max_down:
            self._force_up(f"the key had been down for more than {self.max_down:g} s")
        elif self.held_by == "events" and now - self.last_packet > self.dead_man:
            self._force_up(f"no packets for {self.dead_man:g} s while the key was down")

    def shutdown(self):
        with self.cv:
            self.running = False
            self.queue.clear()
            for t in self.timers:
                t.cancel()
            self._force_up("the daemon is stopping")
            self.cv.notify()
        self.thread.join(timeout=1.0)
        self.line.close()


class EventListener(threading.Thread):
    """Reads key-event packets from a UDP socket and hands them to the engine."""

    def __init__(self, engine, host, port, token=0, allow=None, log=None):
        super().__init__(daemon=True)
        self.engine, self.token, self.allow, self.log = engine, token, allow, log or (lambda m: None)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        self.stopping = False

    def parse(self, data):
        """Returns (type, sender's clock, events) or None for a packet that is not ours or is damaged."""
        if len(data) < HEAD.size:
            return None
        magic, version, kind, token, _seq, sent_ms, count = HEAD.unpack_from(data)
        if magic != MAGIC or version != VERSION or kind not in (T_EVENTS, T_PING, T_ABORT):
            return None
        if self.token and token != self.token:
            return None
        if len(data) != HEAD.size + count * EVENT.size:
            return None
        events = [EVENT.unpack_from(data, HEAD.size + i * EVENT.size) for i in range(count)]
        return kind, sent_ms, [(t, bool(s)) for t, s in events]

    def run(self):
        while not self.stopping:
            try:
                data, addr = self.sock.recvfrom(1500)
            except socket.timeout:
                continue
            except OSError:
                return
            if self.allow and addr[0] not in self.allow:
                self.engine.stats["bad"] += 1
                continue
            parsed = self.parse(data)
            if parsed is None:
                self.engine.stats["bad"] += 1
                continue
            kind, sent_ms, events = parsed
            if kind == T_ABORT:
                self.engine.abort()
            elif not events:
                self.engine.ping(sent_ms=sent_ms)
            else:
                self.engine.feed(events, sent_ms=sent_ms)

    def stop(self):
        self.stopping = True
        self.sock.close()


class TextListener(threading.Thread):
    """Understands cwdaemon's UDP requests: text, and ESC 0, ESC 2, ESC 4 and ESC h."""

    def __init__(self, engine, host, port, wpm=24, allow=None):
        super().__init__(daemon=True)
        self.engine, self.default_wpm, self.wpm, self.allow = engine, wpm, wpm, allow
        self.reply_token = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(0.2)
        self.stopping = False

    def handle(self, data, addr):
        text = data.decode("ascii", "ignore")
        if text.startswith(ESC):
            code, arg = text[1:2], text[2:].strip()
            if code == "0":
                self.wpm = self.default_wpm
            elif code == "2" and arg.isdigit() and 5 <= int(arg) <= 60:
                self.wpm = int(arg)
            elif code == "4":
                self.engine.abort("text")
            elif code == "h":
                self.reply_token = "h" + arg
            return                                           # anything else is ignored
        text = text.strip()
        if not text:
            return
        token, self.reply_token = self.reply_token, None
        reply = None
        if token:
            def reply(token=token, addr=addr):
                try:
                    self.sock.sendto((token + "\r\n").encode(), addr)
                except OSError:
                    pass
        self.engine.text(text, self.wpm, reply)

    def run(self):
        while not self.stopping:
            try:
                data, addr = self.sock.recvfrom(1500)
            except socket.timeout:
                continue
            except OSError:
                return
            if self.allow and addr[0] not in self.allow:
                continue
            self.handle(data, addr)

    def stop(self):
        self.stopping = True
        self.sock.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="the serial port, for example /dev/ttyUSB1 (the radio's second port)")
    ap.add_argument("--line", default="dtr", choices=("dtr", "rts"), help="the control line that keys (default dtr)")
    ap.add_argument("--null", action="store_true", help="no serial port: key nothing, and say what would happen")
    ap.add_argument("--bind", default="0.0.0.0", help="the address to listen on: use your Tailscale address")
    ap.add_argument("--port", type=int, default=6790, help="UDP port for key events (default 6790)")
    ap.add_argument("--text-port", type=int, default=6789, help="UDP port for cwdaemon-style text (default 6789; 0 = off)")
    ap.add_argument("--buffer-ms", type=int, default=150, help="the jitter buffer (default 150)")
    ap.add_argument("--max-down", type=float, default=3.0, help="let the key go after this many seconds down (default 3)")
    ap.add_argument("--dead-man", type=float, default=0.8, help="let the key go if no packet comes for this long while it is down")
    ap.add_argument("--token", type=int, default=0, help="a number (1 to 65535) that packets must carry; 0 = none")
    ap.add_argument("--allow", default="", help="only accept packets from these addresses (comma separated)")
    ap.add_argument("--wpm", type=int, default=24, help="the speed of text until a request changes it")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    if not a.null and not a.device:
        ap.error("give --device (or --null to try it without a serial port)")
    allow = {x.strip() for x in a.allow.split(",") if x.strip()} or None
    say = (lambda m: print(time.strftime("%H:%M:%S"), m, flush=True))
    line = NullKeyLine(verbose=a.verbose) if a.null else SerialKeyLine(a.device, a.line)
    engine = Engine(line, a.buffer_ms / 1000, a.max_down, a.dead_man, log=say)
    listeners = [EventListener(engine, a.bind, a.port, a.token, allow, say)]
    if a.text_port:
        listeners.append(TextListener(engine, a.bind, a.text_port, a.wpm, allow))
    for t in listeners:
        t.start()
    say(f"keying on {'nothing (--null)' if a.null else a.device + ' ' + a.line.upper()}; "
        f"events on UDP {a.port}, text on UDP {a.text_port or 'off'}, buffer {a.buffer_ms} ms")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.wait(1.0):
            pass
    finally:
        for t in listeners:
            t.stop()
        engine.shutdown()
        say(f"stopped. {engine.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
