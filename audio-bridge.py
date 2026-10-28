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
audio-bridge.py - bidirectional radio audio over UDP, for operating a radio remotely: voice,
FreeDV, or any other program. Supports raw PCM or Opus.  Command-line version (see audio-bridge-gui.py
for the graphical front end, which uses this file as its engine).

  SERVER (next to the radio):
      capture  : radio RX audio    -> sent to client
      playback : audio from client -> radio TX audio input

  CLIENT (where FreeDV runs):
      playback : RX audio from server -> virtual cable A  (FreeDV reads RX from here)
      capture  : virtual cable B      -> sent to server   (FreeDV writes TX modem here)

Audio is mono. Only the server needs a reachable UDP port; the client sends
keepalives so NAT/firewalls on the client side need no configuration.

The receiver identifies PCM vs Opus per packet, so --codec only selects what
THIS end transmits. --rate and --frame-ms must match on both ends.

There is NO authentication or encryption. Use a VPN (WireGuard, Tailscale, ...)
if you go beyond your own LAN.

Requires:  pip install sounddevice opuslib
           plus the native libopus (Linux: apt install libopus0 libportaudio2,
           macOS: brew install opus, Windows: opus.dll next to this script)

Examples:
  python audio-bridge.py list
  python audio-bridge.py server --codec opus --in-dev "USB Audio CODEC" --out-dev "USB Audio CODEC"
  python audio-bridge.py client --codec opus --host 192.168.1.50 --out-dev "CABLE Input" --in-dev "CABLE-B Output"
"""
import argparse
import array
import collections
import ctypes
import errno
import os
import queue
import socket
import struct
import sys
import threading
import time

import sounddevice as sd

if sys.platform == "win32":
    # opuslib finds the DLL by searching PATH, which does not include the
    # script's own folder, so add it. This lets opus.dll sit beside this script.
    _here = os.path.dirname(os.path.abspath(__file__))
    os.environ["PATH"] = _here + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(_here)

try:
    import opuslib
    import opuslib.api.ctl as opus_ctl
    import opuslib.api.encoder as opus_enc
    from opuslib import constants as opus_const
    HAVE_OPUS, OPUS_ERR = True, None
except Exception as exc:  # ImportError, or "Could not find Opus library"
    HAVE_OPUS, OPUS_ERR = False, exc

if HAVE_OPUS:
    try:
        # opus_encoder_ctl() is a C *variadic* function. On Apple Silicon (and some
        # other ARM64 platforms) variadic arguments use a different calling
        # convention, so ctypes must be told the fixed arguments or every ctl call
        # fails with "invalid argument". opuslib does not do this itself.
        opus_enc.libopus_ctl.argtypes = [opus_enc.EncoderPointer, ctypes.c_int]
    except Exception:
        pass

__version__ = ("2026-10-26a --pin-volumes keeps PulseAudio volumes up; --wait also recovers when the "
               "audio devices disappear; listen-only (rx_only) option; waits for missing audio "
               "devices; strict-clock pacing, Opus options")

# Let waiting threads (notably the packet-sending clock) get the interpreter lock quickly.
# The default 5 ms can delay a send tick enough to cause back-to-back packets, which some
# tunnels drop.
sys.setswitchinterval(0.0005)

MAGIC = 0xFD
T_PCM, T_HELLO, T_OPUS = 0, 1, 2
HDR = struct.Struct("!BBI")  # magic, type, sequence
PEER_TIMEOUT = 5.0  # no packets for this long = link considered down
OPUS_RATES = (8000, 12000, 16000, 24000, 48000)
OPUS_FRAME_MS = (5, 10, 20, 40, 60)

OPUS_BW = {"nb": "NARROWBAND", "wb": "WIDEBAND", "swb": "SUPERWIDEBAND",
           "fb": "FULLBAND"}
OPUS_SIGNALS = ("music", "voice", "auto")

DEFAULTS = dict(port=5800, bind="0.0.0.0", rate=48000, frame_ms=10, jitter_ms=80,
                codec="pcm", bitrate=64000, opus_bw="nb", opus_signal="music", complexity=5, cbr=False, fec=False,
                loss=5, dump_play=None, dump_capture=None, out_channels=2,
                in_gain_db=0.0, out_gain_db=0.0, pace=True, rx_only=False)


def make_args(**kw):
    """Build an argparse-style settings object (used by the GUI)."""
    d = dict(DEFAULTS, host=None, in_dev=None, out_dev=None)
    d.update(kw)
    return argparse.Namespace(**d)


def check_args(a):
    """Return an error message string if the settings are unusable, else None."""
    if a.rate <= 0 or a.frame_ms <= 0 or (a.rate * a.frame_ms) % 1000:
        return "Sample rate x frame size must give a whole number of samples."
    if a.codec == "opus":
        if not HAVE_OPUS:
            return (f"Opus is unavailable: {OPUS_ERR}\n\nInstall with: pip install "
                    "opuslib (and the native libopus / opus.dll).")
        if a.rate not in OPUS_RATES:
            return f"Opus sample rate must be one of {OPUS_RATES}."
        if a.frame_ms not in OPUS_FRAME_MS:
            return f"Opus frame size must be one of {OPUS_FRAME_MS} ms."
        if not 6000 <= a.bitrate <= 510000:
            return "Opus bitrate must be between 6 and 510 kbps."
        if getattr(a, "opus_bw", "nb") not in OPUS_BW:
            return f"Opus bandwidth must be one of {tuple(OPUS_BW)}."
        if getattr(a, "opus_signal", "music") not in OPUS_SIGNALS:
            return f"Opus signal type must be one of {OPUS_SIGNALS}."
    elif a.rate * a.frame_ms // 1000 * 2 > 1400:
        return ("A PCM frame this large will not fit in one UDP packet. "
                "Lower the sample rate or frame size, or use Opus.")
    return None


def parse_dev(s):
    if s is None:
        return None
    if isinstance(s, int):
        return s
    return int(s) if s.isdigit() else s


class PulsePinner(threading.Thread):
    """Keep the PulseAudio / PipeWire volumes of the radio's sound card, and of this program's own
    streams, at a set percentage and unmuted.

    Something keeps turning them down: a desktop mixer, FreeDV, the volume that PulseAudio
    remembers for each program, or the sound card appearing again at another level when the radio
    is switched on. Each correction is logged, which also shows when it happens. It manages
    only PulseAudio devices (PipeWire included, through its PulseAudio server); it needs the
    pactl command and does nothing, with a note, when that is missing."""

    def __init__(self, node, percent, interval=10.0, log=print):
        super().__init__(daemon=True, name="pulse-pinner")
        self.percent, self.interval, self.log = int(percent), max(1.0, float(interval)), log
        self.sink = self._pulse_name(node.out_dev)      # the radio's playback device (transmit audio)
        self.source = self._pulse_name(node.in_dev)     # the radio's capture device (receive audio)
        self.stop_event = threading.Event()
        self.broken = False

    @staticmethod
    def _pulse_name(dev):
        if dev is None:
            return None
        try:
            info = sd.query_devices(dev)
            if "pulse" in sd.query_hostapis(info["hostapi"])["name"].lower():
                return info["name"]
        except Exception:
            pass
        return None

    def _pactl(self, *args):
        import subprocess
        try:
            out = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5)
        except FileNotFoundError:
            if not self.broken:
                self.broken = True
                self.log("pin-volumes: the pactl command was not found, so the volumes are not managed")
            return None
        except Exception as exc:
            self.log(f"pin-volumes: pactl {' '.join(args[:2])} failed: {exc}")
            return None
        return out.stdout if out.returncode == 0 else None

    @staticmethod
    def _percents(text):
        import re
        return [int(v) for v in re.findall(r"/\s*(\d+)%\s*/", text or "")]

    def _fix(self, what, kind, ident, volume_text, mute_text):
        pcts = self._percents(volume_text)
        if pcts and min(pcts) != self.percent:
            self._pactl(f"set-{kind}-volume", ident, f"{self.percent}%")
            self.log(f"pin-volumes: {what} volume was {min(pcts)}%, set to {self.percent}%")
        if mute_text and "yes" in mute_text.lower():
            self._pactl(f"set-{kind}-mute", ident, "0")
            self.log(f"pin-volumes: {what} was muted, unmuted")

    def _device(self, kind, name, what):
        self._fix(what, kind, name, self._pactl(f"get-{kind}-volume", name),
                  self._pactl(f"get-{kind}-mute", name))

    def _streams(self, kind, header, what):
        """This program's own playback or capture streams, found by process id."""
        import re
        text = self._pactl("list", f"{kind}s")
        if not text:
            return
        for block in re.split(rf"(?m)^{header} #", text)[1:]:
            ident = block.split("\n", 1)[0].strip()
            m = re.search(r'application\.process\.id = "(\d+)"', block)
            if not m or int(m.group(1)) != os.getpid():
                continue
            vol = re.search(r"(?m)^\s*Volume:.*$", block)
            mute = re.search(r"(?m)^\s*Mute:.*$", block)
            self._fix(what, kind, ident, vol.group(0) if vol else "", mute.group(0) if mute else "")

    def apply(self):
        if self.sink:
            self._device("sink", self.sink, "the radio's playback")
        if self.source:
            self._device("source", self.source, "the radio's capture")
        self._streams("sink-input", "Sink Input", "this program's playback stream")
        self._streams("source-output", "Source Output", "this program's capture stream")

    def run(self):
        if not (self.sink or self.source):
            self.log("pin-volumes: the radio's devices are not PulseAudio devices, so there is "
                     "nothing to manage")
            return
        while not self.stop_event.is_set() and not self.broken:
            self.apply()
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()


def _peak(buf):
    """Peak absolute sample value (0..32768) of an int16 buffer."""
    a = array.array("h")
    a.frombytes(buf)
    return max(max(a), -min(a)) if len(a) else 0


def _apply_gain(buf, gain):
    """Scale an int16 buffer by a linear gain, clipping at full scale."""
    a = array.array("h")
    a.frombytes(buf)
    return array.array("h", [-32768 if v < -32768 else 32767 if v > 32767 else v
                             for v in (int(x * gain) for x in a)]).tobytes()


def _dup_stereo(mono):
    """Duplicate a mono int16 buffer into interleaved L=R stereo."""
    a = array.array("h")
    a.frombytes(mono)
    st = array.array("h", bytes(len(mono) * 2))
    st[0::2] = a
    st[1::2] = a
    return st.tobytes()


class WavDump:
    """Background writer that records mono int16 audio to a WAV file (debugging)."""

    def __init__(self, path, rate):
        import wave
        self.w = wave.open(path, "wb")
        self.w.setnchannels(1)
        self.w.setsampwidth(2)
        self.w.setframerate(rate)
        self.q = queue.SimpleQueue()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def put(self, data):
        self.q.put(data)

    def _run(self):
        while True:
            b = self.q.get()
            if b is None:
                break
            self.w.writeframes(b)

    def close(self):
        self.q.put(None)
        self.t.join(timeout=2)
        self.w.close()


class JitterBuffer:
    """Small FIFO of fixed-size PCM frames with prefill and bounded latency."""

    def __init__(self, prefill, maxframes):
        self.q = collections.deque()
        self.lock = threading.Lock()
        self.prefill = max(1, prefill)
        self.maxframes = maxframes
        self.primed = False
        self.underruns = 0
        self.overflows = 0

    def push(self, frame):
        with self.lock:
            self.q.append(frame)
            while len(self.q) > self.maxframes:
                self.q.popleft()
                self.overflows += 1

    def pop(self):
        with self.lock:
            if not self.primed:
                if len(self.q) >= self.prefill:
                    self.primed = True
                else:
                    return None
            if not self.q:
                self.primed = False
                self.underruns += 1
                return None
            return self.q.popleft()

    def level(self):
        with self.lock:
            return len(self.q)


class Node:
    """One end of the bridge. start() runs it in background threads; stop() ends it."""

    def __init__(self, args, is_server, log=print):
        self.is_server = is_server
        self.args = args
        self.log = log
        self.rate = args.rate
        self.bs = self.rate * args.frame_ms // 1000  # samples per frame
        self.fb = self.bs * 2  # bytes per PCM frame (int16 mono)
        self.silence = bytes(self.fb)
        # Playback is opened stereo by default with the mono audio copied to both
        # channels. A mono stream is NOT upmixed by PipeWire/PulseAudio when the
        # device is named explicitly, so it would reach the left channel only.
        # Digital gain (linear), adjustable while running via set_gain_db().
        self.in_gain = 10 ** (getattr(args, "in_gain_db", 0.0) / 20)
        self.out_gain = 10 ** (getattr(args, "out_gain_db", 0.0) / 20)
        # Packet pacing: audio callbacks deliver frames unevenly (sometimes two back to
        # back), and unevenly spaced packets were dropped by the path out of a Mac
        # (20% loss for pairs, ~3% for loosely paced). Sending on a strict clock, one
        # packet per frame interval, gave 0% loss in testing.
        self.pace = bool(getattr(args, "pace", True))
        self.out_ch = int(getattr(args, "out_channels", 2) or 2)
        self.silence_out = bytes(self.fb * self.out_ch)
        self.in_dev = parse_dev(args.in_dev)
        self.out_dev = parse_dev(args.out_dev)
        self.codec = args.codec

        self.encoder = None
        self.decoder = None  # created lazily on first Opus packet
        if self.codec == "opus":
            self.encoder = self._make_encoder()

        prefill = max(1, args.jitter_ms // args.frame_ms)
        self.jb = JitterBuffer(prefill, prefill * 3 + 5)

        self.sock = None
        self.peer = None
        self.last_seen = 0.0
        self.stop_evt = threading.Event()
        self.threads = []
        self.in_stream = None
        self.out_stream = None
        self.dump_p = None  # WAV of what is PLAYED to the local output device
        self.dump_c = None  # WAV of what is CAPTURED from the local input device

        self.txq = queue.SimpleQueue()
        self.tx_seq = 0
        self.tx_bytes = 0
        self.expected = None
        self.late_run = 0
        self.late = 0      # packets that arrived out of order (after a later one)
        self.loss_events = 0   # number of separate gaps in the received sequence
        self.max_gap = 0       # longest gap seen, in packets
        self.gap_logs = 0
        self.tx_pkts = 0   # audio packets handed to the network successfully
        self._last_tx = 0.0
        self.tx_tight = 0      # sends less than half a frame after the previous one
        self.tx_slow = 0       # sends more than 1.5 frames after the previous one
        self._gap_max = 0.0    # longest send gap since stats() was last called
        self.tx_err = 0    # packets this end failed to send (local socket errors)
        self.last_tx_err = None
        self.rx_pkts = 0
        self.lost = 0
        self.bad = 0
        self.in_peak = 0   # peak-hold levels; a GUI may reset these to 0
        self.out_peak = 0
        self.last_in_cb = self.last_out_cb = 0.0   # when each audio callback last ran (watchdog)
        self.warned = set()
        self._snap_t = time.time()
        self._snap_bytes = 0

    # ---- opus helpers ----------------------------------------------------
    def _make_encoder(self):
        a = self.args
        enc = opuslib.Encoder(self.rate, 1, opuslib.APPLICATION_AUDIO)
        st = enc.encoder_state

        def ctl(name, req, val):
            try:
                opus_enc.encoder_ctl(st, req, val)
            except Exception as exc:
                raise RuntimeError(f"Could not set Opus {name} to {val}: {exc}. "
                                   "(Try --codec pcm / the PCM option as a "
                                   "workaround.)") from exc

        ctl("bitrate", opus_ctl.set_bitrate, a.bitrate)
        # FreeDV's modem signal lives below ~4 kHz and needs its waveform preserved, so by
        # default tell Opus to spend all its bits below 4 kHz and favour accuracy ("music").
        bw = getattr(a, "opus_bw", "nb")
        ctl("max bandwidth", opus_ctl.set_max_bandwidth,
            getattr(opus_const, "BANDWIDTH_" + OPUS_BW[bw]))
        sig = getattr(a, "opus_signal", "music")
        if sig == "music":
            ctl("signal type", opus_ctl.set_signal, opus_const.SIGNAL_MUSIC)
        elif sig == "voice":
            ctl("signal type", opus_ctl.set_signal, opus_const.SIGNAL_VOICE)
        ctl("complexity", opus_ctl.set_complexity, a.complexity)
        ctl("VBR", opus_ctl.set_vbr, 0 if a.cbr else 1)
        ctl("FEC", opus_ctl.set_inband_fec, 1 if a.fec else 0)
        ctl("packet-loss hint", opus_ctl.set_packet_loss_perc, a.loss if a.fec else 0)
        try:  # read back, to confirm the settings really reached libopus
            got = opus_enc.encoder_ctl(st, opus_ctl.get_bitrate)
            self.log(f"Opus encoder ready: {got} bit/s, bandwidth "
                     f"{getattr(a, 'opus_bw', 'nb')}, signal {getattr(a, 'opus_signal', 'music')}")
        except Exception:
            pass
        return enc

    def _decode_opus(self, payload, fec=False):
        """Decode one packet (or its FEC copy of the previous frame)."""
        try:
            pcm = self.decoder.decode(payload, self.bs, decode_fec=fec)
        except Exception:
            self.bad += 1
            return None
        if len(pcm) != self.fb:
            self.bad += 1
            self._warn_once("size", "WARNING: decoded Opus frame size mismatch - "
                            "check sample rate/frame size match on both ends")
            return None
        return pcm

    def _plc_opus(self):
        """Packet-loss concealment: ask the decoder to extrapolate one frame."""
        try:
            pcm = self.decoder.decode(b"", self.bs)
            if len(pcm) == self.fb:
                return pcm
        except Exception:
            pass
        return self.silence

    def set_gain_db(self, which, db):
        """which: 'in' = capture gain, 'out' = playback gain."""
        g = 10 ** (db / 20)
        if which == "in":
            self.in_gain = g
        else:
            self.out_gain = g

    def _warn_once(self, key, msg):
        if key not in self.warned:
            self.warned.add(key)
            self.log(msg)

    # ---- audio callbacks -------------------------------------------------
    def in_cb(self, indata, frames, t, status):
        self.last_in_cb = time.monotonic()
        data = bytes(indata)
        if self.in_gain != 1.0:
            data = _apply_gain(data, self.in_gain)
        d = self.dump_c
        if d is not None:
            d.put(data)
        p = _peak(data)
        if p > self.in_peak:
            self.in_peak = p
        self.txq.put(data)

    def out_cb(self, outdata, frames, t, status):
        self.last_out_cb = time.monotonic()
        f = self.jb.pop()
        if f is None:
            outdata[:] = self.silence_out
        else:
            if self.out_gain != 1.0:
                f = _apply_gain(f, self.out_gain)
            outdata[:] = _dup_stereo(f) if self.out_ch == 2 else f
            p = _peak(f)
            if p > self.out_peak:
                self.out_peak = p
        d = self.dump_p
        if d is not None:
            d.put(self.silence if f is None else f)

    # ---- network threads -------------------------------------------------
    def peer_active(self):
        if self.peer is None:
            return False
        if self.is_server:
            return (time.time() - self.last_seen) < PEER_TIMEOUT
        return True

    def send_loop(self):
        interval = self.args.frame_ms / 1000
        next_t = time.monotonic()
        while not self.stop_evt.is_set():
            if self.pace:
                # strict clock: one packet per tick; tick slightly faster if frames pile up
                d = next_t - time.monotonic()
                if d > 0:
                    time.sleep(d)
                try:
                    data = self.txq.get_nowait()
                except queue.Empty:
                    data = b""  # nothing ready this tick
                next_t += interval if self.txq.qsize() < 3 else interval * 0.9
                now = time.monotonic()
                if next_t < now - 0.05:  # fell behind (e.g. system stall): restart the clock
                    next_t = now
                if not data:
                    continue
            else:
                data = self.txq.get()
            if data is None or self.stop_evt.is_set():
                break
            if not self.peer_active():
                continue
            if self.encoder is not None:
                typ, payload = T_OPUS, self.encoder.encode(data, self.bs)
            else:
                typ, payload = T_PCM, data
            pkt = HDR.pack(MAGIC, typ, self.tx_seq) + payload
            self.tx_seq = (self.tx_seq + 1) & 0xFFFFFFFF
            self.tx_bytes += len(pkt)
            sent = False
            for _ in range(3):  # retry briefly if the OS send queue is full
                try:
                    self.sock.sendto(pkt, self.peer)
                    sent = True
                    self.tx_pkts += 1
                    now = time.monotonic()
                    if self._last_tx:
                        g = (now - self._last_tx) * 1000
                        if g < 0.5 * self.args.frame_ms:
                            self.tx_tight += 1
                        elif g > 1.5 * self.args.frame_ms:
                            self.tx_slow += 1
                        if g > self._gap_max:
                            self._gap_max = g
                    self._last_tx = now
                    break
                except OSError as exc:
                    self.last_tx_err = exc
                    if self.stop_evt.is_set():
                        break
                    if exc.errno in (errno.ENOBUFS, errno.EAGAIN, errno.EWOULDBLOCK):
                        time.sleep(0.002)
                        continue
                    break
            if not sent and not self.stop_evt.is_set():
                self.tx_err += 1
                self._warn_once("txerr", f"Send error: {self.last_tx_err!r} "
                                         "(further errors are counted in the status line)")

    def hello_loop(self):
        while True:
            try:
                self.sock.sendto(HDR.pack(MAGIC, T_HELLO, 0), self.peer)
            except OSError:
                pass
            if self.stop_evt.wait(1.0):
                break

    def recv_loop(self):
        while not self.stop_evt.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                continue  # e.g. Windows ICMP "connection reset"; ignore
            if len(data) < HDR.size:
                continue
            magic, typ, seq = HDR.unpack_from(data)
            if magic != MAGIC:
                continue
            if self.is_server:
                if addr != self.peer:
                    self.log(f"Client connected from {addr[0]}:{addr[1]}")
                    self.expected = None
                    self.decoder = None  # fresh decoder state for new client
                self.peer = addr
            elif addr != self.peer:
                continue
            self.last_seen = time.time()

            if typ in (T_PCM, T_OPUS):
                self.handle_audio(typ, seq, data[HDR.size:])

    def handle_audio(self, typ, seq, payload):
        if typ == T_PCM:
            if len(payload) != self.fb:
                self.bad += 1
                self._warn_once("size", "WARNING: packet size mismatch - check "
                                "sample rate/frame size match on both ends")
                return
        else:
            if not HAVE_OPUS:
                self._warn_once("noopus", f"Peer is sending Opus but it is unavailable "
                                          f"here: {OPUS_ERR}")
                return
            if self.decoder is None:
                self.decoder = opuslib.Decoder(self.rate, 1)

        self.rx_pkts += 1
        frames = []
        if self.expected is not None:
            diff = (seq - self.expected) & 0xFFFFFFFF
            if diff == 0:
                self.late_run = 0
            elif diff < 50:  # small gap: conceal to keep timing
                self.late_run = 0
                self.lost += diff
                self.loss_events += 1
                if diff > self.max_gap:
                    self.max_gap = diff
                if diff >= 10 and self.gap_logs < 20:
                    self.gap_logs += 1
                    self.log(f"Gap of {diff} packets ({diff * self.args.frame_ms} ms)")
                if typ == T_OPUS:
                    for _ in range(diff - 1):
                        frames.append(self._plc_opus())
                    # recover the frame just before this one from in-band FEC
                    # (falls back to PLC if the stream carries no FEC)
                    frames.append(self._decode_opus(payload, fec=True) or self.silence)
                else:
                    frames.extend([self.silence] * diff)
            elif diff > 0x80000000:  # late / duplicate
                self.late += 1
                self.late_run += 1
                if self.late_run < 30:
                    return
                # Many "late" packets in a row means the sender restarted and its
                # sequence numbers began again: resynchronise instead of discarding.
                self.log("Sender restarted - resynchronising audio")
                self.late_run = 0
                if typ == T_OPUS:
                    self.decoder = opuslib.Decoder(self.rate, 1)
            else:  # large jump forward: just resync
                self.late_run = 0
        self.expected = (seq + 1) & 0xFFFFFFFF

        if typ == T_OPUS:
            pcm = self._decode_opus(payload)
            if pcm is None:
                pcm = self.silence
        else:
            pcm = payload
        frames.append(pcm)
        for f in frames:
            self.jb.push(f)

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        """Open the socket and audio devices and start the worker threads.
        Raises on failure (after cleaning up)."""
        a = self.args
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if self.is_server:
                self.sock.bind((a.bind, a.port))
                self.log(f"Listening on UDP {a.bind}:{a.port}")
            else:
                ip = socket.gethostbyname(a.host)
                self.peer = (ip, a.port)
                self.log(f"Sending to {ip}:{a.port}")
            self.sock.settimeout(0.5)

            if a.dump_play:
                self.dump_p = WavDump(a.dump_play, self.rate)
                self.log(f"Recording audio played to the output device: {a.dump_play}")
            if a.dump_capture:
                self.dump_c = WavDump(a.dump_capture, self.rate)
                self.log(f"Recording audio captured from the input device: {a.dump_capture}")

            kw = dict(samplerate=self.rate, channels=1, dtype="int16",
                      blocksize=self.bs)
            if getattr(a, "rx_only", False):          # listen only: no microphone is opened and
                self.log("Listen-only: not opening an input device, sending no audio")
            else:                                    # nothing but keepalives goes to the other end
                self.in_stream = sd.RawInputStream(device=self.in_dev,
                                                   callback=self.in_cb, **kw)
            okw = dict(kw)
            while True:
                okw["channels"] = self.out_ch
                try:
                    self.out_stream = sd.RawOutputStream(
                        device=self.out_dev, callback=self.out_cb, **okw)
                    break
                except Exception:
                    if self.out_ch == 2:  # device may be mono-only: fall back
                        self.log("Stereo playback not supported by this device; "
                                 "using mono")
                        self.out_ch = 1
                        self.silence_out = bytes(self.fb)
                    else:
                        raise
            if self.in_stream is not None:
                self.in_stream.start()
            self.out_stream.start()
        except Exception:
            self.stop()
            raise
        self.last_in_cb = self.last_out_cb = time.monotonic()

        targets = [self.recv_loop, self.send_loop]
        if not self.is_server:
            targets.append(self.hello_loop)
        for fn in targets:
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self.threads.append(t)
        desc = f"Opus {self.args.bitrate // 1000} kbps" if self.codec == "opus" else "PCM"
        self.log(f"Bridge version {__version__}")
        self.log(f"Audio running: tx={desc}, {self.rate} Hz, {a.frame_ms} ms frames, "
                 f"playback {'stereo (L=R)' if self.out_ch == 2 else 'mono'}")

    def stop(self):
        self.stop_evt.set()
        for s in (self.in_stream, self.out_stream):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
        self.in_stream = self.out_stream = None
        self.txq.put(None)
        for t in self.threads:
            t.join(timeout=2)
        self.threads = []
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        for attr in ("dump_p", "dump_c"):
            d = getattr(self, attr)
            setattr(self, attr, None)
            if d is not None:
                d.close()

    def stats(self):
        """Snapshot of link statistics. tx_kbps is averaged since the last call."""
        now = time.time()
        dt = max(now - self._snap_t, 1e-6)
        kbps = (self.tx_bytes - self._snap_bytes) * 8 / 1000 / dt
        self._snap_t, self._snap_bytes = now, self.tx_bytes
        linked = self.last_seen > 0 and (now - self.last_seen) < PEER_TIMEOUT
        gap_max, self._gap_max = self._gap_max, 0.0
        return dict(tx_tight=self.tx_tight, tx_slow=self.tx_slow, tx_gap_max=gap_max,
                    linked=linked, peer=self.peer if self.last_seen else None,
                    tx_kbps=kbps, rx=self.rx_pkts, lost=self.lost, bad=self.bad,
                    late=self.late, tx_err=self.tx_err, tx_pkts=self.tx_pkts,
                    loss_events=self.loss_events, max_gap=self.max_gap,
                    underruns=self.jb.underruns, dropped=self.jb.overflows,
                    buf=self.jb.level())

    # ---- command-line runner ---------------------------------------------
    def run(self):
        self.start()
        self.monitor()

    def streams_alive(self, stall=3.0):
        """False if an audio stream has stopped calling back, which is what happens when its
        device disappears (for example the radio is switched off)."""
        now = time.monotonic()
        if self.in_stream is not None and now - self.last_in_cb > stall:
            return False
        return self.out_stream is None or now - self.last_out_cb <= stall

    def monitor(self, watchdog=False):
        """Print link statistics until Ctrl-C (the node must already be started). Returns "quit",
        or, with watchdog=True, "stalled" if the audio streams stop (the node is stopped first)."""
        print("Ctrl-C to quit.")
        try:
            tick = 0
            while True:
                time.sleep(1)
                if watchdog and not self.streams_alive():
                    print("The audio streams stopped (the audio devices went away?). "
                          "Restarting and waiting for them.", flush=True)
                    return "stalled"
                tick += 1
                if tick % 5:
                    continue
                s = self.stats()
                print(f"tx={s['tx_kbps']:.0f} kbps (payload+hdr)  rx={s['rx']} "
                      f"txpkts={s['tx_pkts']} lost={s['lost']} late={s['late']} "
                      f"txerr={s['tx_err']} events={s['loss_events']} longest={s['max_gap']} "
                      f"txgap(tight/slow/max)={s['tx_tight']}/{s['tx_slow']}/"
                      f"{s['tx_gap_max']:.0f}ms "
                      f"bad={s['bad']} underruns={s['underruns']} "
                      f"dropped={s['dropped']} buf={s['buf']}")
        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            self.stop()
        return "quit"


def main():
    d = DEFAULTS
    ap = argparse.ArgumentParser(description="FreeDV remote audio bridge")
    ap.add_argument("mode", choices=["server", "client", "list"])
    ap.add_argument("--host", help="client: server hostname/IP")
    ap.add_argument("--port", type=int, default=d["port"])
    ap.add_argument("--bind", default=d["bind"], help="server: bind address")
    ap.add_argument("--rate", type=int, default=d["rate"],
                    help="sample rate, same on both ends (Opus: 8/12/16/24/48 kHz)")
    ap.add_argument("--frame-ms", type=int, default=d["frame_ms"],
                    help="frame size in ms, same on both ends (Opus: 5/10/20/40/60). "
                         "20 ms halves packet overhead at the cost of 10 ms more latency")
    ap.add_argument("--jitter-ms", type=int, default=d["jitter_ms"],
                    help="playout buffer; raise on lossy/WAN links")
    ap.add_argument("--in-dev", help="capture device (index or name substring)")
    ap.add_argument("--out-dev", help="playback device (index or name substring)")
    ap.add_argument("--codec", choices=["pcm", "opus"], default=d["codec"],
                    help="codec used for what this end TRANSMITS")
    ap.add_argument("--bitrate", type=int, default=d["bitrate"],
                    help="Opus bitrate in bit/s (default 64000)")
    ap.add_argument("--opus-bw", choices=tuple(OPUS_BW), default=d["opus_bw"],
                    help="highest audio bandwidth Opus may use: nb 4 kHz (default, best for "
                         "FreeDV), wb 8 kHz, swb 12 kHz, fb 20 kHz")
    ap.add_argument("--opus-signal", choices=OPUS_SIGNALS, default=d["opus_signal"],
                    help="Opus signal type hint (default music: favours waveform accuracy)")
    ap.add_argument("--complexity", type=int, default=d["complexity"],
                    choices=range(0, 11), metavar="0-10",
                    help="Opus encoder complexity (default 5)")
    ap.add_argument("--cbr", action="store_true", help="Opus constant bitrate (default VBR)")
    ap.add_argument("--fec", action="store_true",
                    help="enable Opus in-band FEC (helps most at lower bitrates)")
    ap.add_argument("--loss", type=int, default=d["loss"],
                    help="expected packet loss %% hint for FEC (default 5)")
    ap.add_argument("--out-channels", type=int, choices=(1, 2), default=d["out_channels"],
                    help="playback channels: 2 = stereo with audio on both (default), "
                         "1 = mono")
    ap.add_argument("--wait", action="store_true",
                    help="keep retrying every few seconds until the audio devices exist "
                         "(use this to start the bridge before switching the radio on)")
    ap.add_argument("--pin-volumes", type=int, default=0, metavar="PERCENT",
                    help="PulseAudio / PipeWire: keep the volume of the radio's sound card, and of "
                         "this program's own streams, at PERCENT (for example 100) and unmuted, "
                         "checking every --pin-interval seconds and logging each correction. "
                         "0 = leave the volumes alone (default)")
    ap.add_argument("--pin-interval", type=float, default=10.0, metavar="SECONDS",
                    help="how often --pin-volumes checks (default 10)")
    ap.add_argument("--no-pace", action="store_true",
                    help="disable packet pacing (not recommended: bursts of packets "
                         "can be dropped by the network path)")
    ap.add_argument("--in-gain-db", type=float, default=0.0,
                    help="capture gain in dB (default 0)")
    ap.add_argument("--out-gain-db", type=float, default=0.0,
                    help="playback gain in dB, e.g. -6 to reduce TX drive (default 0)")
    ap.add_argument("--dump-play", metavar="FILE.wav",
                    help="debug: record the audio played to the output device")
    ap.add_argument("--dump-capture", metavar="FILE.wav",
                    help="debug: record the audio captured from the input device")
    a = ap.parse_args()

    if a.mode == "list":
        print(sd.query_devices())
        return
    if a.mode == "client" and not a.host:
        ap.error("client mode needs --host")
    a.pace = not a.no_pace
    err = check_args(a)
    if err:
        ap.error(err)

    tries = 0
    while True:
        node = Node(a, a.mode == "server")
        try:
            node.start()
        except KeyboardInterrupt:
            return
        except Exception as exc:
            if not a.wait:
                raise
            tries += 1
            if tries == 1 or tries % 20 == 0:
                print(f"Waiting for the audio devices ({exc}). Retrying every 3 s; "
                      "Ctrl-C to quit.", flush=True)
            try:
                time.sleep(3)
                sd._terminate()          # make PortAudio re-scan for new devices
                sd._initialize()
            except KeyboardInterrupt:
                return
            except Exception:
                pass
            continue
        tries = 0
        pinner = None
        if a.pin_volumes:
            pinner = PulsePinner(node, a.pin_volumes, a.pin_interval, log=lambda m: print(m, flush=True))
            pinner.start()
        # with --wait, a bridge that is left running also recovers when its devices go away
        # (the radio is switched off) and come back (it is switched on again)
        result = node.monitor(watchdog=a.wait)
        if pinner is not None:
            pinner.stop()
        if result == "quit":
            return
        node.stop()
        try:
            time.sleep(3)
            sd._terminate()
            sd._initialize()
        except KeyboardInterrupt:
            return
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
