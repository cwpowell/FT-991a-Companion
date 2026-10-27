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
udpseq.py - minimal UDP loss tester that sends packets the way the bridge does.

PLAIN TEST
  Receiver (server):  python3 udpseq.py recv 5800
  Sender   (Mac):     python3 udpseq.py send <server-address> 5800 --size 1000 --pps 100 --seconds 60
    --burst N     send N packets back to back, then wait N intervals (default 1 = evenly paced)
    --connect     use a connect()ed socket like iperf3 (default: unconnected sendto, like the bridge)

STAGED TEST (finds which bridge behaviour triggers loss; one run, ~2.5 minutes)
  Receiver (server):  python3 udpseq.py recv 5800 --quiet
  Sender   (Mac):     python3 udpseq.py send <server-address> 5800 --stages --device "BlackHole 16ch"
  Stages, 30 s each (change with --stage-seconds):
    0  plain evenly-paced sending, default socket                     (baseline)
    1  socket with a 0.5 s timeout + a thread blocked reading it      (as the bridge's socket)
    2  as 1, but sent in back-to-back pairs                           (bursty timing)
    3  as 1, but each packet is triggered by a live audio-capture callback on --device
       (as the bridge did before pacing)
    4  as 3, plus the bridge's current pacing (packets at least 8 ms apart)
    5  as 3, but sent on a strict 10 ms clock, one packet per tick

BRIDGE MODE: count gaps in the real bridge's traffic (bridge stopped on the receiving end):
  Receiver (server):  python3 udpseq.py recv 5800 --bridge

Run these with the bridge STOPPED on the machine that is receiving.
"""
import argparse
import queue
import socket
import struct
import sys
import threading
import time

STAGE_NAMES = {
    0: "plain paced sending, default socket",
    1: "socket timeout + reader thread (like the bridge)",
    2: "as 1, bursts of 2 packets",
    3: "as 1, triggered by live audio-capture callback (unpaced)",
    4: "as 3, plus the bridge's pacing (min 8 ms between packets)",
    5: "as 3, sent on a strict 10 ms clock",
}


def recv(port, bridge=False, quiet=False):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    s.settimeout(1.0)
    print(f"Listening on UDP {port} ... (Ctrl-C to stop)")
    expected = None
    rx = lost = late = 0
    srx = slost = slate = 0
    stage = None
    st_rx = st_lost = 0
    last = time.time()
    started = None

    def stage_summary(stg, r, l):
        tot = r + l
        pct = 100 * l / tot if tot else 0
        name = STAGE_NAMES.get(stg, "")
        print(f"STAGE {stg}: received {r}, lost {l} -> {pct:.2f}% loss   {name}")

    try:
        while True:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                data = None
            if data is not None and bridge:
                # bridge header: magic 0xFD, type (0 PCM, 1 HELLO, 2 Opus), 32-bit seq
                if len(data) < 6 or data[0] != 0xFD or data[1] == 1:
                    data = None
            if data is not None and len(data) >= 4:
                if started is None:
                    started = time.time()
                    print(f"First packet from {addr[0]}:{addr[1]}")
                seq = struct.unpack("!I", data[2:6] if bridge else data[:4])[0]
                stg = data[4] if (not bridge and len(data) > 4) else 0
                if stage is None:
                    stage = stg
                elif stg != stage:
                    stage_summary(stage, st_rx, st_lost)
                    stage, st_rx, st_lost = stg, 0, 0
                rx += 1
                srx += 1
                st_rx += 1
                if expected is not None:
                    diff = (seq - expected) & 0xFFFFFFFF
                    if diff == 0:
                        pass
                    elif diff < 0x80000000:
                        lost += diff
                        slost += diff
                        st_lost += diff
                    else:
                        late += 1
                        slate += 1
                        continue
                expected = (seq + 1) & 0xFFFFFFFF
            now = time.time()
            if now - last >= 1.0 and started is not None:
                if not quiet:
                    print(f"  rx={srx:4d}  lost={slost:3d}  late={slate:2d}   "
                          f"(total rx={rx} lost={lost} late={late})")
                srx = slost = slate = 0
                last = now
    except KeyboardInterrupt:
        pass
    if stage is not None and (st_rx or st_lost):
        stage_summary(stage, st_rx, st_lost)
    total = rx + lost
    pct = 100 * lost / total if total else 0
    print(f"\nSUMMARY: received {rx}, lost {lost}, late {late}  ->  "
          f"{pct:.2f}% loss of {total} sent")


def make_pkt(seq, stage, size):
    return struct.pack("!IB", seq, stage) + bytes(max(0, size - 5))


class Sender:
    def __init__(self, dest, size):
        self.dest, self.size = dest, size
        self.seq = 0
        self.errors = 0

    def send(self, sock, stage, connect=False):
        pkt = make_pkt(self.seq, stage, self.size)
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        try:
            if connect:
                sock.send(pkt)
            else:
                sock.sendto(pkt, self.dest)
        except OSError as exc:
            self.errors += 1
            if self.errors == 1:
                print("send error:", exc)

    def paced(self, sock, stage, secs, pps, burst=1, connect=False):
        interval = 1.0 / pps
        total = int(pps * secs)
        t0 = time.monotonic()
        n = 0
        while n < total:
            delay = t0 + n * interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            for _ in range(min(burst, total - n)):
                self.send(sock, stage, connect)
                n += 1


def reader_thread(sock):
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                sock.recvfrom(4096)
            except OSError:  # includes the timeout
                pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return stop, t


def capture_driven(snd, sk, q, stage, secs):
    """Send one packet per captured audio frame (token in q), in three timing styles."""
    end = time.monotonic() + secs
    if stage == 5:  # strict clock: one packet per 10 ms tick, a little faster if behind
        next_t = time.monotonic()
        while time.monotonic() < end:
            d = next_t - time.monotonic()
            if d > 0:
                time.sleep(d)
            try:
                q.get_nowait()
                snd.send(sk, stage)
            except queue.Empty:
                pass
            next_t += 0.010 if q.qsize() < 3 else 0.009
            now = time.monotonic()
            if next_t < now - 0.05:
                next_t = now
        return
    last = 0.0
    while time.monotonic() < end:
        try:
            q.get(timeout=0.5)
        except queue.Empty:
            continue
        if stage == 4 and q.qsize() < 4:  # same rule as the bridge's pacing
            wait = last + 0.008 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        snd.send(sk, stage)
        last = time.monotonic()


def send_stages(host, port, size, pps, secs, device):
    dest = (socket.gethostbyname(host), port)
    snd = Sender(dest, size)
    print(f"Staged test to {dest[0]}:{port}: {size}-byte packets at {pps}/s, "
          f"{secs} s per stage")

    def timed_socket():
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.settimeout(0.5)
        return sk, *reader_thread(sk)

    print(f"Stage 0: {STAGE_NAMES[0]}")
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    snd.paced(sk, 0, secs, pps)
    sk.close()

    print(f"Stage 1: {STAGE_NAMES[1]}")
    sk, stop, t = timed_socket()
    snd.paced(sk, 1, secs, pps)
    stop.set(); t.join(); sk.close()

    print(f"Stage 2: {STAGE_NAMES[2]}")
    sk, stop, t = timed_socket()
    snd.paced(sk, 2, secs, pps, burst=2)
    stop.set(); t.join(); sk.close()

    if device is None:
        print("Stages 3-5 skipped (give --device, e.g. --device \"BlackHole 16ch\")")
    else:
        try:
            import sounddevice as sd
        except ImportError:
            print("Stages 3-5 skipped (sounddevice is not installed)")
            sd = None
        if sd is not None:
            for stage in (3, 4, 5):
                print(f"Stage {stage}: {STAGE_NAMES[stage]} ({device})")
                sk, stop, t = timed_socket()
                q = queue.SimpleQueue()
                try:
                    with sd.RawInputStream(device=device, samplerate=48000, channels=1,
                                           dtype="int16", blocksize=480,
                                           callback=lambda indata, frames, ti, st: q.put(1)):
                        capture_driven(snd, sk, q, stage, secs)
                except Exception as exc:
                    print(f"Stage {stage} failed:", exc)
                stop.set(); t.join(); sk.close()
    print(f"Done. Send errors: {snd.errors}. Now press Ctrl-C on the receiver.")


def send_plain(host, port, size, pps, seconds, burst, connect):
    dest = (socket.gethostbyname(host), port)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if connect:
        s.connect(dest)
    snd = Sender(dest, max(size, 5))
    print(f"Sending {int(pps * seconds)} packets of {size} bytes to {dest[0]}:{port} at "
          f"{pps}/s ({'connected' if connect else 'unconnected'}, burst {burst})")
    snd.paced(s, 0, seconds, pps, burst, connect)
    print(f"Done: sent {snd.seq} packets, {snd.errors} send errors")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("recv")
    r.add_argument("port", type=int)
    r.add_argument("--bridge", action="store_true",
                   help="parse the bridge's packet header instead of a plain sequence number")
    r.add_argument("--quiet", action="store_true", help="only print stage and final summaries")
    t = sub.add_parser("send")
    t.add_argument("host")
    t.add_argument("port", type=int)
    t.add_argument("--size", type=int, default=1000)
    t.add_argument("--pps", type=int, default=100)
    t.add_argument("--seconds", type=int, default=60)
    t.add_argument("--burst", type=int, default=1)
    t.add_argument("--connect", action="store_true")
    t.add_argument("--stages", action="store_true", help="run the staged test")
    t.add_argument("--stage-seconds", type=int, default=30)
    t.add_argument("--device", help="audio capture device for stage 3 (name or index)")
    a = ap.parse_args()
    if a.mode == "recv":
        recv(a.port, a.bridge, a.quiet)
    elif a.stages:
        dev = None if a.device is None else (int(a.device) if a.device.isdigit() else a.device)
        send_stages(a.host, a.port, max(a.size, 5), a.pps, a.stage_seconds, dev)
    else:
        send_plain(a.host, a.port, a.size, a.pps, a.seconds, max(a.burst, 1), a.connect)


if __name__ == "__main__":
    sys.exit(main())
