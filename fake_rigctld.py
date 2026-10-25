#!/usr/bin/env python3
"""
fake_rigctld.py - a stand-in for Hamlib's rigctld (plain-text protocol) for testing
ft991a_panel.py without a radio.

  python3 fake_rigctld.py --port 4532            # then run the panel against 127.0.0.1:4532
  python3 fake_rigctld.py --port 4532 --demo     # also keys "PTT" for 5 s out of every 15 s

It answers the commands the panel uses (f m t T l L u U G) in rigctld's format. Meter values are
made up. Several clients may connect at once; each gets only its own replies, like rigctld.
Send "T 1" / "T 0" from any client (for example `nc 127.0.0.1 4532`) to key and unkey it.
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
        self.freq = 14074055
        self.mode = "PKTUSB"
        self.width = 3000
        self.ptt = 0
        self.rfpower = 0.75
        self.tuner = 0
        self.tune_until = 0.0
        self.split = 0
        self.af = 0.4
        self.agc = 3
        self.tx_vfo = "VFOA"

    def tick(self):
        with self.lock:
            if self.tune_until and time.time() >= self.tune_until:
                self.tune_until = 0.0
                self.ptt = 0
                self.tuner = 1

    def watts(self):
        return self.rfpower * 100 * 0.96 + random.uniform(-0.8, 0.8) if self.ptt else 0.0

    def respond(self, line):
        parts = line.split()
        if not parts:
            return ""
        cmd, args = parts[0], parts[1:]
        with self.lock:
            if cmd == "f":
                return f"{self.freq}\n"
            if cmd == "F" and args:
                self.freq = int(float(args[0]))
                return "RPRT 0\n"
            if cmd == "m":
                return f"{self.mode}\n{self.width}\n"
            if cmd == "M" and args:
                self.mode = args[0]
                return "RPRT 0\n"
            if cmd == "t":
                return f"{self.ptt}\n"
            if cmd == "T" and args:
                self.ptt = int(args[0])
                return "RPRT 0\n"
            if cmd == "l" and args:
                what = args[0]
                if what == "AGC":
                    return f"{self.agc}\n"
                if what == "AF":
                    return f"{self.af:g}\n"
                if what == "RFPOWER":
                    return f"{self.rfpower:g}\n"
                if what == "RFPOWER_METER_WATTS":
                    return f"{self.watts():.1f}\n"
                if what == "RFPOWER_METER":
                    return f"{self.watts() / 100:.3f}\n"
                if what == "SWR":
                    return f"{(1.0 + random.uniform(0, 0.15)) if self.ptt else 1:.2f}\n"
                if what == "ALC":
                    return f"{random.uniform(0, 0.04) if self.ptt else 0:.2f}\n"
                if what == "STRENGTH":
                    return f"{-12 + random.uniform(-2, 2):.0f}\n"
                return "RPRT -11\n"
            if cmd == "L" and len(args) >= 2:
                if args[0] == "AGC":
                    self.agc = int(float(args[1]))
                    return "RPRT 0\n"
                if args[0] == "AF":
                    self.af = max(0.0, min(1.0, float(args[1])))
                    return "RPRT 0\n"
                if args[0] == "RFPOWER":
                    self.rfpower = max(0.05, min(1.0, float(args[1])))
                    return "RPRT 0\n"
                return "RPRT -11\n"
            if cmd == "u" and args:
                if args[0] == "TUNER":
                    return f"{self.tuner}\n"
                return "RPRT -11\n"
            if cmd == "U" and len(args) >= 2:
                if args[0] == "TUNER":
                    self.tuner = int(args[1])
                    return "RPRT 0\n"
                return "RPRT -11\n"
            if cmd == "s":
                return f"{self.split}\n{self.tx_vfo}\n"
            if cmd == "S" and len(args) >= 2:
                self.split, self.tx_vfo = int(args[0]), args[1]
                return "RPRT 0\n"
            if cmd == "G" and args and args[0] == "TUNE":
                if self.tune_until:
                    self.tune_until = 0.0
                    self.ptt = 0
                else:
                    self.tune_until = time.time() + 3.0
                    self.ptt = 1
                return "RPRT 0\n"
            return "RPRT -11\n"


def serve(sim, port, host="127.0.0.1"):
    def handle(conn):
        buf = ""
        try:
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                buf += data.decode("ascii", "ignore")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    reply = sim.respond(line.strip())
                    if reply:
                        conn.sendall(reply.encode())
        except OSError:
            pass
        finally:
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
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()

    def ticker():
        while True:
            sim.tick()
            time.sleep(0.05)

    threading.Thread(target=ticker, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser(description="Simulated rigctld for testing the panel")
    ap.add_argument("--port", type=int, default=4532)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--demo", action="store_true", help="key PTT for 5 s out of every 15 s")
    a = ap.parse_args()
    sim = Sim()
    serve(sim, a.port, a.host)
    print(f"Simulated rigctld on {a.host}:{a.port}  (Ctrl-C to stop)")
    try:
        t0 = time.time()
        while True:
            time.sleep(0.2)
            if a.demo:
                phase = (time.time() - t0) % 15
                with sim.lock:
                    if not sim.tune_until:
                        sim.ptt = 1 if phase < 5 else 0
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
