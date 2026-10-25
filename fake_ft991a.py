#!/usr/bin/env python3
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
            return int(random.uniform(0, 6)) if transmitting else 0
        if which == "1":   # S meter
            return int(100 + random.uniform(-10, 10)) if not transmitting else 0
        if which == "8":   # VDD
            return 150
        return 0

    def respond(self, cmd):
        """cmd without the trailing ';'. Returns the reply (with ';') or None."""
        c = cmd.upper()
        with self.lock:
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
                return f"TX{self.tx};"
            if c in ("TX0", "TX1"):
                self.tx = int(c[2])
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
