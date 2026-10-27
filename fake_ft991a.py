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

"""
fake_ft991a.py - a simulated Yaesu FT-991A for testing ft991a_panel.py's "Raw CAT" connection
without a radio.

It listens on a TCP port and speaks the CAT subset the panel uses (ID, FA, IF, MD0, TX, PC,
RM, RI0, AC, SM0, AI, PS). Like the real radio, PS (power on/off) is only accepted when it
arrives between 1 and 2 seconds after dummy data (a stray ";") with nothing else in between;
in standby the simulated radio answers nothing. Like ser2net with several clients connected, every reply is sent to ALL
connected clients, so you can see how cross-talk behaves.

  python3 fake_ft991a.py --port 4533            # then connect the panel (Raw CAT) to 127.0.0.1:4533
  python3 fake_ft991a.py --port 4533 --demo     # also keys "TX" for 5 s out of every 15 s

Meter values are made up. Send "TX1;" / "TX0;" from any client (e.g. `nc 127.0.0.1 4533`) to
key and unkey the simulated radio.
"""
import argparse
import math
import random
import socket
import threading
import time


# The radio's functions, restated here straight from Yaesu's CAT manual and kept apart from the
# panel's own table, so that a mistake in either shows up as a refused command:
# (command, selector, width of the value, lowest, highest, value at start).
FN_SPEC = [
    ("PA", "0", 1, 0, 2, 0), ("RA", "0", 1, 0, 1, 0), ("RG", "0", 3, 0, 255, 255),
    ("SQ", "0", 3, 0, 100, 0), ("NB", "0", 1, 0, 1, 0), ("NL", "0", 3, 0, 10, 5),
    ("NR", "0", 1, 0, 1, 0), ("RL", "0", 2, 1, 15, 8), ("BC", "0", 1, 0, 1, 0),
    ("BP", "00", 3, 0, 1, 0), ("BP", "01", 3, 1, 320, 100),
    ("CO", "00", 4, 0, 1, 0), ("CO", "01", 4, 10, 3200, 500), ("CO", "02", 4, 0, 1, 0),
    ("PR", "0", 1, 1, 2, 1), ("PR", "1", 1, 1, 2, 1), ("PL", "", 3, 0, 100, 50),
    ("MG", "", 3, 0, 100, 50), ("ML", "0", 3, 0, 1, 0), ("ML", "1", 3, 0, 100, 50),
    ("VX", "", 1, 0, 1, 0), ("VG", "", 3, 0, 100, 50), ("VD", "", 4, 30, 3000, 300),
    ("KR", "", 1, 0, 1, 0), ("BI", "", 1, 0, 1, 0), ("KS", "", 3, 4, 60, 20),
    ("KP", "", 2, 0, 75, 30), ("SD", "", 4, 30, 3000, 300),
    ("OS", "0", 1, 0, 2, 0), ("CT", "0", 1, 0, 4, 0), ("CN", "00", 3, 0, 49, 12),
    ("LK", "", 1, 0, 1, 0), ("FS", "", 1, 0, 1, 0), ("CS", "", 1, 0, 1, 0),
]
KM_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 /?.,=-")   # what a CW memory takes (assumed)
CW_MODES = ("3", "7")                                        # CW and CW-R (the CAT mode codes)
FM_MODES = ("4", "A", "B")       # FM, DATA-FM, FM-N (the CAT mode codes)


class Sim:
    def __init__(self):
        self.lock = threading.Lock()
        self.freq = 14074000
        self.freq_b = 7074000
        self.tx_vfo = 0          # 0 = VFO-A transmits, 1 = VFO-B transmits (CAT command FT)
        self.bw_idx = 14         # SH: SSB wide index 14 = 2400 Hz
        self.narrow = 0          # NA: 0 wide, 1 narrow
        self.data_mode = 1       # menu 062: 0 PSK, 1 OTHER
        self.fa_sets = 0         # how many FA set commands arrived (to check coalescing)
        self.alc_level = 40      # ALC reading while transmitting: audio is reaching the radio (0 = none)
        self.menu_raw = {}       # menu number -> raw value text; a test fills in the items it needs
        self.menu_reject = set()  # menu numbers whose writes the radio ignores (for tests)
        self.s_for = None         # optional: a function frequency -> S meter reading (for tests)
        self.fn = {c + sel: dflt for c, sel, _w, _lo, _hi, dflt in FN_SPEC}   # the functions
        self.fn_reject = set()   # functions whose writes the radio ignores (for tests), by command+selector
        self.fn_log = []         # every accepted write: (command+selector, the digits sent)
        # The CW memories, as a program known to work with these radios describes them: text goes in with
        # KM and ends with "}"; the memory must be TEXT in menu 018 to 022; KY6 to KYA play text memory
        # 1 to 5, and KY1 to KY5 play the memories recorded with the paddle.
        self.km = {n: t for n, t in zip("12345", ("CQ CQ DE NK8O NK8O K}", "5NN TU}", "", "", ""))}
        for n in range(18, 23):
            self.menu_raw.setdefault(f"{n:03d}", "0")        # CW MEMORY 1 to 5: 0 TEXT, 1 MESSAGE
        self.km_reject = set()   # CW memories whose writes the radio ignores (for tests)
        self.km_noterm = set()   # memories written without the closing "}" (the panel must never do this)
        self.ky_log = []         # every KY that made the radio send: the code
        self.ky_silent = []      # KY codes that sent nothing, and why: (code, reason)
        self.zi_count = 0        # how many times ZI (zero in) was accepted
        self.cw_until = 0.0      # a played CW message keeps the radio transmitting until then
        self.cw_seconds = 0.8    # how long a played message "transmits" in the simulation
        self.cmd_count = 0       # how many commands arrived (to check how busy the line is)
        self.agc = 3             # GT: 0 off, 1 fast, 2 mid, 3 slow, 4 auto (answers may be 4-6)
        self.af = 100            # AG: 0-255 receive volume
        self.menu = {m: 50 for m in ("046", "054", "073", "075", "078", "099", "107")}  # transmit levels
        self.mode = "C"          # DATA-USB
        self.power = 50
        self.tuner = 1           # 0 off, 1 on
        self.tx = 0              # 0 rx, 1 CAT tx, 2 radio tx
        self.tuning_until = 0.0
        self.hi_swr = 0
        self.on = True           # False = standby: answers nothing except the power-on sequence
        self.dummy_at = None
        self.power_log = []      # (command, seconds after the dummy data, accepted?)
        self.interrupted = 0     # commands that arrived between the dummy data and PS

    def dummy(self):
        with self.lock:
            self.dummy_at = time.time()
        return None

    def tick(self):
        with self.lock:
            if self.tuning_until and time.time() >= self.tuning_until:
                self.tuning_until = 0.0
                self.tx = 0
                self.tuner = 1

    def meter(self, which):
        transmitting = self.tx != 0
        if which == "5":   # PO
            if not transmitting:
                return 0
            return int(255 * math.sqrt(self.power * 0.96 / 100) + random.uniform(-2, 2))
        if which == "6":   # SWR
            return int(30 + random.uniform(-3, 3)) if transmitting else 0
        if which == "4":   # ALC
            return int(self.alc_level + random.uniform(0, 6)) if transmitting else 0
        if which == "1":   # S meter
            if self.s_for is not None:                  # a test can make it depend on the frequency
                return 0 if transmitting else int(self.s_for(self.freq) + random.uniform(-2, 2))
            return int(100 + random.uniform(-10, 10)) if not transmitting else 0
        if which == "8":   # VDD
            return 150
        return 0

    def fn_available(self, cmd, sel):
        """Which functions the radio answers in its present mode (a guess, to test greying out)."""
        fm = self.mode in FM_MODES
        if cmd in ("OS", "CT", "CN"):
            return fm                                   # repeater shift and tones: FM only
        if (cmd, sel) == ("CO", "02"):
            return self.mode in ("3", "7")              # the audio peak filter: CW only
        if cmd in ("BP", "CO"):
            return not fm                               # notch and contour: not in FM
        return True

    def fn_respond(self, c):
        """A function command (NB, NR, PA ...), laid out as Yaesu's CAT manual says. Returns the
        reply, None for a write that was accepted, or False when c is not a function command."""
        for cmd, sel, width, lo, hi, _dflt in FN_SPEC:
            key = cmd + sel
            if not c.startswith(key):
                continue
            rest = c[len(key):]
            if not self.fn_available(cmd, sel):
                return "?;"
            if rest == "":
                return f"{key}{self.fn[key]:0{width}d};"
            if len(rest) != width or not rest.isdigit() or not lo <= int(rest) <= hi:
                return "?;"                              # wrong length or out of range: refused
            if key not in self.fn_reject:
                self.fn[key] = int(rest)
            self.fn_log.append((key, rest))
            return None
        return False

    def respond(self, cmd):
        """cmd without the trailing ';'. Returns the reply (with ';') or None."""
        c = cmd.upper()
        with self.lock:
            self.cmd_count += 1
            if c in ("PS0", "PS1"):
                dt = None if self.dummy_at is None else time.time() - self.dummy_at
                ok = dt is not None and 1.0 <= dt <= 2.0
                self.power_log.append((c, dt, ok))
                if ok:
                    self.on = (c == "PS1")
                    if not self.on:
                        self.tx = 0
                self.dummy_at = None
                return None
            if self.dummy_at is not None:      # other data between the dummy and PS spoils it
                self.interrupted += 1
                self.dummy_at = None
            if not self.on:
                return None                     # standby: no answers at all
            if c == "PS":
                return "PS1;"
            if c == "ID":
                return "ID0670;"
            if c == "AG0":
                return f"AG0{self.af:03d};"
            if c.startswith("AG0") and len(c) == 6:
                self.af = int(c[3:])
                return None
            if c.startswith("EX") and len(c) >= 5 and c[2:5] in self.menu_raw:   # any menu item
                if len(c) == 5:
                    return f"EX{c[2:5]}{self.menu_raw[c[2:5]]};"
                if c[2:5] not in self.menu_reject:       # a test can make the radio refuse an item
                    self.menu_raw[c[2:5]] = c[5:]
                return None
            if c.startswith("EX") and len(c) == 5 and c[2:] in self.menu:
                return f"EX{c[2:]}{self.menu[c[2:]]:03d};"
            if c.startswith("EX") and len(c) == 8 and c[2:5] in self.menu:
                self.menu[c[2:5]] = int(c[5:])
                return None
            if c == "GT0":
                return f"GT0{self.agc};"
            if c.startswith("GT0") and len(c) == 4:
                self.agc = int(c[3])
                return None
            if c == "SH0":
                return f"SH0{self.bw_idx:02d};"
            if c.startswith("SH0") and len(c) == 5:
                self.bw_idx = int(c[3:])
                return None
            if c == "NA0":
                return f"NA0{self.narrow};"
            if c.startswith("NA0") and len(c) == 4:
                self.narrow = int(c[3])
                return None
            if c == "EX062":
                return f"EX062{self.data_mode};"
            if c == "SV":
                self.freq, self.freq_b = self.freq_b, self.freq
                return None
            if c == "FB":
                return f"FB{self.freq_b:09d};"
            if c.startswith("FB") and len(c) == 11:
                self.freq_b = int(c[2:])
                return None
            if c == "FT":
                return f"FT{self.tx_vfo};"
            if c in ("FT2", "FT3"):
                self.tx_vfo = int(c[2]) - 2
                return None
            if c == "FA":
                return f"FA{self.freq:09d};"
            if c.startswith("FA") and len(c) == 11:
                self.freq = int(c[2:])
                self.fa_sets += 1
                return None
            if c == "IF":
                return f"IF000{self.freq:09d}+000000{self.mode}00" + "000" + ";"
            if c == "MD0":
                return f"MD0{self.mode};"
            if c.startswith("MD0") and len(c) == 4:
                self.mode = c[3]
                return None
            if c == "TX":
                return f"TX{2 if time.time() < self.cw_until else self.tx};"
            if c in ("TX0", "TX1"):
                self.tx = int(c[2])
                if c == "TX0":
                    self.cw_until = 0.0                   # transmit off also ends a played message
                return None
            if c.startswith("KM") and len(c) >= 3 and c[2] in "12345":    # a CW memory
                n, text = c[2], c[3:]
                if text == "":
                    return f"KM{n}{self.km[n]};"
                body = text[:-1] if text.endswith("}") else text
                if len(text) > 50 or any(ch not in KM_OK for ch in body):
                    return "?;"                           # more than 50 characters, or one it cannot send
                if n not in self.km_reject:
                    self.km[n] = text
                    (self.km_noterm.discard if text.endswith("}") else self.km_noterm.add)(n)
                return None
            if c.startswith("KY") and len(c) == 3 and c[2] in "123456789A":   # play a CW memory
                if self.mode not in CW_MODES:
                    return "?;"
                code = c[2]
                if code in "12345":                       # recorded with the paddle: always plays something
                    self.ky_log.append(code)
                    self.cw_until = time.time() + self.cw_seconds
                    return None
                n = str("6789A".index(code) + 1)          # text memory n
                if self.menu_raw.get(f"{17 + int(n):03d}") != "0":
                    self.ky_silent.append((code, "the memory is set to MESSAGE, not TEXT"))
                elif not self.km[n].endswith("}") or self.km[n] == "}":
                    self.ky_silent.append((code, "nothing stored, or no closing brace"))
                else:
                    self.ky_log.append(code)
                    self.cw_until = time.time() + self.cw_seconds
                return None
            if c == "ZI":                                 # CW auto zero-in
                if self.mode not in CW_MODES:
                    return "?;"
                self.zi_count += 1
                return None
            if c == "PC":
                return f"PC{self.power:03d};"
            if c.startswith("PC") and len(c) == 5:
                self.power = max(5, min(100, int(c[2:])))
                return None
            if c.startswith("RM") and len(c) == 3:
                return f"RM{c[2]}{self.meter(c[2]):03d};"
            if c == "RI0":
                return f"RI0{self.hi_swr};"
            if c == "SM0":
                return f"SM0{self.meter('1'):03d};"
            if c == "AI":
                return "AI0;"
            if c == "AC":
                state = 2 if self.tuning_until else self.tuner
                return f"AC00{state};"
            if c.startswith("AC") and len(c) == 5:
                p3 = c[4]
                if p3 == "0":
                    self.tuner = 0
                elif p3 == "1":
                    self.tuner = 1
                elif p3 == "2":
                    if self.tuning_until:        # a second press stops tuning
                        self.tuning_until = 0.0
                        self.tx = 0
                    else:
                        self.tuning_until = time.time() + 3.0
                        self.tx = 2
                return None
            reply = self.fn_respond(c)
            if reply is not False:
                return reply
            return "?;"


def serve(sim, port, host="127.0.0.1"):
    clients = []
    clock = threading.Lock()

    def broadcast(text):
        data = text.encode()
        with clock:
            for c in list(clients):
                try:
                    c.sendall(data)
                except OSError:
                    clients.remove(c)

    def handle(conn):
        buf = ""
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                buf += data.decode("ascii", "ignore")
                while ";" in buf:
                    cmd, buf = buf.split(";", 1)
                    cmd = cmd.strip()
                    reply = sim.respond(cmd) if cmd else sim.dummy()
                    if reply:
                        broadcast(reply)
        except OSError:
            pass
        finally:
            with clock:
                if conn in clients:
                    clients.remove(conn)
            conn.close()

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)

    def accept():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with clock:
                clients.append(conn)
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()

    def ticker():
        while True:
            sim.tick()
            time.sleep(0.05)

    threading.Thread(target=ticker, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser(description="Simulated FT-991A (raw CAT) for testing the panel")
    ap.add_argument("--port", type=int, default=4533)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--demo", action="store_true", help="key TX for 5 s out of every 15 s")
    a = ap.parse_args()
    sim = Sim()
    serve(sim, a.port, a.host)
    print(f"Simulated FT-991A on {a.host}:{a.port}  (Ctrl-C to stop)")
    try:
        t0 = time.time()
        while True:
            time.sleep(0.2)
            if a.demo:
                phase = (time.time() - t0) % 15
                with sim.lock:
                    if not sim.tuning_until:
                        sim.tx = 2 if phase < 5 else 0
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
