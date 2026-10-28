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
ft991a-panel.py - remote status and control panel for a Yaesu FT-991A.

Two connection types (choose in the window, or with --backend):
  hamlib  Hamlib's rigctld over TCP (default port 4532), using rigctld's plain-text protocol.
          Meters arrive calibrated by Hamlib (watts, SWR ratio).
  cat     Raw Yaesu CAT through a raw TCP serial server such as ser2net. Several programs
          share the serial line and each sees every reply, so the panel parses replies by
          their content. Meters arrive as raw 0-255 values: calibrate them with Cal PO and
          Cal SWR (the panel shows "uncalibrated" raw values until you do).

Shows:
  - VFO A and VFO B frequencies side by side (click VFO B to swap the two VFOs, so that
    B's frequency becomes the one in VFO A, the operating VFO), a Split button, the mode, and whether the radio is receiving, transmitting or tuning
  - frequency entry two ways: type a frequency, or click a digit (100 MHz ... 1 Hz) and use the
    mouse wheel (Up/Down keys, and Left/Right to move along the digits)
  - a C4FM button that switches between FM and C4FM on VHF/UHF
  - a bandwidth slider for SSB, CW and data modes (Raw CAT), and an AGC menu (off, fast, mid,
    slow, auto)
  - a Band pull-down beside the frequency entry: goes to the last frequency and mode used on a band
  - a push-to-talk button just above the log (so it is on every tab): one click (or one press of
    the space bar) to transmit, again to stop, with release safeguards and a time-out
  - a Hide log button, and noise reduction (spectral subtraction) on the audio you listen to
  - a Function tab with the radio's front-panel functions (preamp, attenuator, RF gain, squelch,
    contour, noise blanker, the radio's noise reduction, notches, speech processor, mic gain, monitor,
    VOX, CW keyer, repeater shift and tone, dial lock) and a Menu tab with all 153 of its menu
    items (both need the Raw CAT connection)
  - a separate CW window (button beside the log): a text box and five macros that cwdaemon, running on
    the radio's computer, sends as Morse, with Abort and the radio's CW zero-in
  - Mic level and Volume sliders, the same controls as on the Audio tab
  - an Audio tab with FreeDV-style routing: radio to computer, computer to speaker, mic to
    computer, computer to radio. Local radio: the radio's sound card is on this computer.
    Remote radio: the bridge on the server connects to the same radio-side endpoints (virtual
    cables such as BlackHole, or direct to the speakers and microphone). Volume and Mic level
    sliders and a Mic on/off button. The audio engine is built in (the client side of audio-bridge.py,
    which keeps running on the server) and has the bridge's own options under Advanced. The
    radio's own volume is never touched.
  - an S meter while receiving; power output, SWR and ALC while transmitting, with a power
    peak marker
  - drop-downs for mode and power setting (5, 25, 50, 75, 100 W; 5-50 W on 144/430 MHz)
  - an ATU button that puts the internal antenna tuner in line (ON) or bypasses it (OFF),
    and a Tune button that starts a tuning cycle
  - power on and power off buttons (Raw CAT connection only)

It keys the radio only with the PTT button (or the space bar), and for the tuner's tuning
carrier, which asks for confirmation first. The calibration buttons are hidden until you
press the Calibration button.

Python 3 and tkinter are needed; the Audio tab also needs the sounddevice package (and
opuslib for Opus).   python3 ft991a-panel.py [--host H --port P]

Meter values come from Hamlib's own calibration tables. If your wattmeter disagrees, use
"Cal PO" / "Cal SWR" while transmitting into a dummy load to add correction points.
"""
APP_NAME = "FT-991a Companion"                   # shown in the title bar, --version and the report
__version__ = "1.2.1"      # shown in the title bar; the installers and the report use it too

import argparse
import json
import math
import queue
import re
import socket
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

# ======================================================================================
# AUDIO ENGINE, built in. This is the client side of audio-bridge.py (the same engine
# and wire protocol, so it talks to the bridge running on the server). Nothing here needs a
# separate file: the Audio tab needs only the sounddevice package (and opuslib for Opus).
# ======================================================================================
import array
import collections
import ctypes
import errno
import os
import tempfile
import struct

try:
    import sounddevice as sd
    AUDIO_OK, AUDIO_ERR = True, None
except Exception as _exc:             # sounddevice is not installed, or PortAudio is missing
    sd, AUDIO_OK, AUDIO_ERR = None, False, _exc

def _app_dirs():
    """Folders where a library kept with the program may be: beside the packaged program, inside
    the packaged app, and beside this file. The first one has priority, so a newer opus.dll that
    you drop next to the program wins over the copy built into it."""
    dirs = []
    if getattr(sys, "frozen", False):                  # packaged with PyInstaller
        exe_dir = os.path.dirname(sys.executable)
        dirs += [exe_dir, getattr(sys, "_MEIPASS", None),
                 os.path.join(exe_dir, os.pardir, "Frameworks"),
                 os.path.join(exe_dir, os.pardir, "Resources")]
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    out = []
    for d in dirs:
        if d and os.path.isdir(d) and os.path.abspath(d) not in out:
            out.append(os.path.abspath(d))
    return out


def _find_bundled_opus(dirs=None, platform=None):
    """Path of an Opus library that was shipped with the program, or None."""
    names = {"win32": ("opus.dll", "libopus-0.dll"),
             "darwin": ("libopus.0.dylib", "libopus.dylib")}.get(platform or sys.platform,
                                                                ("libopus.so.0",))
    for d in (_app_dirs() if dirs is None else dirs):
        for name in names:
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return path
    return None


_bundled_opus = _find_bundled_opus()
if sys.platform == "win32":
    # opuslib finds the DLL by searching PATH, which does not include the program's own
    # folder, so add the folders from _app_dirs() (the first one ends up first on PATH).
    for _d in reversed(_app_dirs()):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(_d)
            except OSError:
                pass
elif _bundled_opus:
    # macOS app: opuslib asks ctypes.util.find_library("opus"), which knows nothing about the
    # app's own folders, so answer that one question with the library that came with the app.
    import ctypes.util
    _real_find_library = ctypes.util.find_library

    def _find_library(name, _real=_real_find_library, _path=_bundled_opus):
        return _path if name == "opus" else _real(name)

    ctypes.util.find_library = _find_library

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

ENGINE_VERSION = "2026-10-24a listen-only (rx_only) option; waits for missing audio devices; strict-clock pacing, Opus options"

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


def _mix(a, b):
    """Add two equal-length int16 buffers sample by sample, clipping at full scale."""
    x, y = array.array("h"), array.array("h")
    x.frombytes(a)
    y.frombytes(b)
    return array.array("h", [-32768 if v < -32768 else 32767 if v > 32767 else v
                             for v in (p + q for p, q in zip(x, y))]).tobytes()


def _dup_stereo(mono):
    """Duplicate a mono int16 buffer into interleaved L=R stereo."""
    a = array.array("h")
    a.frombytes(mono)
    st = array.array("h", bytes(len(mono) * 2))
    st[0::2] = a
    st[1::2] = a
    return st.tobytes()


class _CaptureStderr:
    """Collects what C libraries (PortAudio) print on the stderr file descriptor while active, so
    a warning can be reported together with the device that caused it."""

    def __enter__(self):
        self.lines, self.ok = [], False
        try:
            sys.stderr.flush()
            self.tmp = tempfile.TemporaryFile(mode="w+b")
            self.saved = os.dup(2)
            os.dup2(self.tmp.fileno(), 2)
            self.ok = True
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        if self.ok:
            try:
                os.dup2(self.saved, 2)
                os.close(self.saved)
                self.tmp.seek(0)
                text = self.tmp.read().decode("utf-8", "replace")
                self.tmp.close()
                self.lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            except Exception:
                pass
        return False


def _dev_name(dev):
    try:
        info = sd.query_devices(dev)
        if isinstance(info, (list, tuple)):          # a whole list came back: pick the device
            info = info[dev]
        return info["name"]
    except Exception:
        return str(dev)


def _logged_open(log, what, dev, fn, *args, **kw):
    """Run fn (opening or starting an audio stream) and report anything PortAudio prints while it
    runs, naming the device. Errors still raise as before."""
    cap = _CaptureStderr()
    cap.__enter__()
    try:
        result = fn(*args, **kw)
    except Exception:
        cap.__exit__(None, None, None)
        for line in cap.lines:
            log(f"PortAudio reported, {what} ({_dev_name(dev)}): {line}")
        raise
    cap.__exit__(None, None, None)
    for line in cap.lines:
        log(f"PortAudio reported, {what} ({_dev_name(dev)}): {line}  (the stream still opened, "
            "so this is only a warning)")
    return result


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
        self.tap = None    # panel addition: a function that also receives every played frame
        self.nr = None     # panel addition: noise reduction for the direct playback path
        self.mix_mic = False                   # panel addition: the microphone drives the sending,
        self.cable_buf = bytearray()           # and what arrives on the capture device (a cable that
        self.cable_lock = threading.Lock()     # FreeDV writes to) is mixed in with it
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
        data = bytes(indata)
        if self.mix_mic:                         # keep it for send_mic(); do not send it alone
            with self.cable_lock:
                self.cable_buf += data
                if len(self.cable_buf) > 12 * len(data):
                    del self.cable_buf[:len(self.cable_buf) - 3 * len(data)]
            return
        if self.in_gain != 1.0:
            data = _apply_gain(data, self.in_gain)
        d = self.dump_c
        if d is not None:
            d.put(data)
        p = _peak(data)
        if p > self.in_peak:
            self.in_peak = p
        self.txq.put(data)

    def send_mic(self, mic):
        """Panel addition: queue one microphone frame for sending, mixed with the frame that
        arrived on the capture device (silence unless a program writes to that cable). The
        microphone's own clock drives the sending, so it never depends on the cable stream."""
        data = mic
        with self.cable_lock:
            cb = self.cable_buf
            if len(cb) >= len(mic):
                cable = bytes(cb[:len(mic)])
                del cb[:len(mic)]
                data = _mix(mic, cable)
        if self.in_gain != 1.0:
            data = _apply_gain(data, self.in_gain)
        p = _peak(data)
        if p > self.in_peak:
            self.in_peak = p
        self.txq.put(data)

    def out_cb(self, outdata, frames, t, status):
        f = self.jb.pop()
        if f is None:
            outdata[:] = self.silence_out
        else:
            if self.out_gain != 1.0:
                f = _apply_gain(f, self.out_gain)
            if self.nr is not None:
                f = self.nr.process(f)
            outdata[:] = _dup_stereo(f) if self.out_ch == 2 else f
            p = _peak(f)
            if p > self.out_peak:
                self.out_peak = p
            tap = self.tap
            if tap is not None:
                tap(f)
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
                self.in_stream = _logged_open(
                    self.log, "opening the capture stream", self.in_dev, sd.RawInputStream,
                    device=self.in_dev, callback=self.in_cb, **kw)
            okw = dict(kw)
            while True:
                okw["channels"] = self.out_ch
                try:
                    self.out_stream = _logged_open(
                        self.log, "opening the playback stream", self.out_dev,
                        sd.RawOutputStream, device=self.out_dev, callback=self.out_cb, **okw)
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
                _logged_open(self.log, "starting the capture stream", self.in_dev,
                             self.in_stream.start)
            _logged_open(self.log, "starting the playback stream", self.out_dev,
                         self.out_stream.start)
        except Exception:
            self.stop()
            raise

        targets = [self.recv_loop, self.send_loop]
        if not self.is_server:
            targets.append(self.hello_loop)
        for fn in targets:
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self.threads.append(t)
        desc = f"Opus {self.args.bitrate // 1000} kbps" if self.codec == "opus" else "PCM"
        self.log(f"Bridge version {ENGINE_VERSION}")
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

    def monitor(self):
        """Print link statistics until Ctrl-C (the node must already be started)."""
        print("Ctrl-C to quit.")
        try:
            while True:
                time.sleep(5)
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
# ======================================================================================
# end of the audio engine
# ======================================================================================

CFG_FILE = Path.home() / ".ft991a-panel.json"
OLD_CFG_FILE = Path.home() / ".ft991a_panel.json"      # the name before 1.1.2: read if the new one is not there yet

# (label shown, Hamlib mode name). DATA-USB is what FreeDV uses (Hamlib: PKTUSB).
MODES = [("DATA-USB", "PKTUSB"), ("USB", "USB"), ("LSB", "LSB"), ("DATA-LSB", "PKTLSB"),
         ("CW-U", "CW"), ("CW-L", "CWR"), ("AM", "AM"), ("AM-N", "AMN"), ("FM", "FM"),
         ("FM-N", "FMN"), ("DATA-FM", "FM-D"), ("RTTY-LSB", "RTTY"), ("RTTY-USB", "RTTYR"),
         ("C4FM", "C4FM")]
# The amateur bands for the Band pull-down on the Radio tab: name, lowest and highest frequency in Hz,
# and where to go (frequency, mode) the first time you pick the band. After that the panel remembers
# the last frequency and mode you used on each band, as the radio's own band keys do.
BANDS = [("160m", 1_800_000, 2_000_000, 1_900_000, "LSB"),
         ("80m", 3_500_000, 4_000_000, 3_800_000, "LSB"),
         ("60m", 5_250_000, 5_450_000, 5_330_500, "USB"),
         ("40m", 7_000_000, 7_300_000, 7_200_000, "LSB"),
         ("30m", 10_100_000, 10_150_000, 10_130_000, "USB"),
         ("20m", 14_000_000, 14_350_000, 14_200_000, "USB"),
         ("17m", 18_068_000, 18_168_000, 18_130_000, "USB"),
         ("15m", 21_000_000, 21_450_000, 21_300_000, "USB"),
         ("12m", 24_890_000, 24_990_000, 24_950_000, "USB"),
         ("10m", 28_000_000, 29_700_000, 28_400_000, "USB"),
         ("6m", 50_000_000, 54_000_000, 50_125_000, "USB"),
         ("2m", 144_000_000, 148_000_000, 146_520_000, "FM"),
         ("70cm", 420_000_000, 450_000_000, 446_000_000, "FM")]
BAND_BY_NAME = {b[0]: b for b in BANDS}


def band_of(hz):
    """The name of the band that a frequency (Hz) is in, or None."""
    if hz:
        for name, lo, hi, _f, _m in BANDS:
            if lo <= hz <= hi:
                return name
    return None


LABEL_OF = {ham: label for label, ham in MODES}
HAM_OF = {label: ham for label, ham in MODES}

# Yaesu CAT mode codes (MD and IF commands) and the Hamlib names used throughout the panel
CAT_MODE = {"1": "LSB", "2": "USB", "3": "CW", "4": "FM", "5": "AM", "6": "RTTY",
            "7": "CWR", "8": "PKTLSB", "9": "RTTYR", "A": "FM-D", "B": "FMN",
            "C": "PKTUSB", "D": "AMN", "E": "C4FM"}
CAT_CODE_OF = {name: code for code, name in CAT_MODE.items()}
BACKENDS = {"Hamlib rigctld": "hamlib", "Raw CAT (ser2net)": "cat"}

POWERS_HF = [5, 25, 50, 75, 100]
POWERS_VHF = [5, 25, 50]          # 144 / 430 MHz: 50 W maximum

# The radio's own ALC meter reaches mid-scale when CAT reports about a quarter of full scale, so the
# reading is doubled to match what the radio shows. Green to 25 %, orange to 50 %, red above that.
ALC_SCALE = 2.0
ALC_WARN, ALC_BAD = 0.25, 0.50    # on the doubled scale (0-1)
BLUE, GREEN, AMBER, RED, GREY = "#2a7fd4", "#1a8f3c", "#d98a00", "#c62828", "#9a9a9a"
YELLOW = "#f2c200"                # the ATU light when the tuner is in line
S9_DEFAULT_RAW = 128              # assumed raw S-meter reading for S9 until you press Cal S9
NONE_LABEL = "(none - listen only)"     # no microphone: receive audio only
DIRECT_RX = "(direct: network to speakers)"      # remote radio, no virtual cable on receive
DIRECT_TX = "(direct: microphone to network)"    # remote radio, no virtual cable on transmit
BW_CHOICES = {"narrow": "nb", "wide": "wb", "super-wide": "swb", "full": "fb"}   # Opus bandwidth
PTT_TIMEOUT = 120.0               # seconds: push-to-talk lets go by itself after this long
PTT_IDLE_TEXT = "PTT  (click or press Space to talk)"
PTT_ON_TEXT = "TRANSMITTING - click or press Space to stop"
SEL_BLUE = "#cfe8ff"              # background of the selected VFO
VFO_FONT = ("Helvetica", 18, "bold")   # the frequency digits
POWER_WAIT = 1.4                  # seconds between the dummy data and PS0;/PS1; (radio wants 1 to 2)
TUNE_TIMEOUT = 25.0               # seconds before a stuck tuning cycle is stopped
STALE_AFTER = 3.0                 # seconds without any reply before values are greyed out
REPLY_TIMEOUT = 4.0               # seconds to wait for rigctld to answer a command


def load_cfg():
    for path in (CFG_FILE, OLD_CFG_FILE):
        try:
            return json.loads(path.read_text())
        except Exception:
            continue
    return {}


def interp(points, x, origin):
    """Piecewise-linear correction. points = [(reading, true value), ...]; origin = true
    value at reading 0. Returns None if there are no points."""
    pts = {round(float(r), 3): float(v) for r, v in points if float(r) > 0}
    if not pts:
        return None
    seq = [(0.0, float(origin))] + sorted(pts.items())
    if x <= 0:
        return seq[0][1]
    for (x0, y0), (x1, y1) in zip(seq, seq[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = seq[-2], seq[-1]       # beyond the last point: continue the slope
    return y1 + (y1 - y0) * (x - x1) / (x1 - x0)


FREQ_MIN, FREQ_MAX = 30_000, 470_000_000     # FA / FB range in the CAT manual


def parse_frequency(text):
    """Turn what you typed into Hz. Accepts MHz (14.074), kHz (14074 or 7074k), Hz (14074000),
    grouped digits (14.074.000) and units (145.5 MHz, 7074 kHz, 14074000 Hz)."""
    t = text.strip().lower().replace(" ", "").replace("_", "").replace(",", ".")
    m = re.match(r"^([0-9.]+)([a-z]*)$", t)
    if not m or not m.group(1).strip("."):
        raise ValueError(f"Cannot read '{text.strip()}' as a frequency")
    num, unit = m.groups()
    try:
        if num.count(".") >= 2:                    # grouped digits: 14.074.000 Hz
            if unit not in ("", "hz", "h"):
                raise ValueError(f"Cannot read '{text.strip()}' as a frequency")
            hz = int(num.replace(".", ""))
        elif unit in ("hz", "h"):
            hz = round(float(num))
        elif unit in ("khz", "k"):
            hz = round(float(num) * 1_000)
        elif unit in ("mhz", "m"):
            hz = round(float(num) * 1_000_000)
        elif unit:
            raise ValueError(f"Unknown unit '{unit}'")
        elif "." in num:                           # 14.074 means MHz
            hz = round(float(num) * 1_000_000)
        else:
            n = int(num)
            hz = n * 1_000_000 if n < 1000 else n * 1_000 if n <= 470_000 else n
    except ValueError as exc:
        if "unit" in str(exc):
            raise
        raise ValueError(f"Cannot read '{text.strip()}' as a frequency") from exc
    if not FREQ_MIN <= hz <= FREQ_MAX:
        raise ValueError("The radio tunes from 30 kHz to 470 MHz")
    return hz


# Receive bandwidth, from the SH table in the CAT manual: SH0nn; where nn is an index whose
# meaning depends on the mode and on the radio's NARROW state (NA command). Index 00 is the
# default width. Narrow and wide sets overlap at one width, so one slider can cover both.
BW_NARROW = {
    "SSB": {1: 200, 2: 400, 3: 600, 4: 850, 5: 1100, 6: 1350, 7: 1500, 8: 1650, 9: 1800},
    "CW": {1: 50, 2: 100, 3: 150, 4: 200, 5: 250, 6: 300, 7: 350, 8: 400, 9: 450, 10: 500},
    "RTTY": {1: 50, 2: 100, 3: 150, 4: 200, 5: 250, 6: 300, 7: 350, 8: 400, 9: 450, 10: 500},
}
_WIDE_CW = {10: 500, 11: 800, 12: 1200, 13: 1400, 14: 1700, 15: 2000, 16: 2400, 17: 3000}
BW_WIDE = {
    "SSB": {9: 1800, 10: 1950, 11: 2100, 12: 2200, 13: 2300, 14: 2400, 15: 2500, 16: 2600,
            17: 2700, 18: 2800, 19: 2900, 20: 3000, 21: 3200},
    "CW": _WIDE_CW,
    "RTTY": _WIDE_CW,
}
BW_DEFAULT = {"SSB": (1500, 2400), "CW": (500, 2400), "RTTY": (300, 500)}  # (narrow, wide)


def bw_family(mode, data_mode):
    """Which SH column applies. DATA modes follow menu 062: 0 = PSK (RTTY/PSK widths),
    1 = OTHER (assumed to use the SSB widths). AM, FM and C4FM have no adjustable width."""
    if mode in ("LSB", "USB"):
        return "SSB"
    if mode in ("CW", "CWR"):
        return "CW"
    if mode in ("RTTY", "RTTYR"):
        return "RTTY"
    if mode in ("PKTLSB", "PKTUSB"):
        return "RTTY" if data_mode == "0" else "SSB"
    return None


def bw_steps(family):
    return sorted(set(BW_NARROW[family].values()) | set(BW_WIDE[family].values()))


def bw_hz(family, narrow, idx):
    if idx == 0:
        return BW_DEFAULT[family][0 if narrow else 1]
    return (BW_NARROW if narrow else BW_WIDE)[family].get(idx)


def bw_plan(family, hz, cur_narrow):
    """(narrow flag, index) that gives this width, keeping the current NARROW state if it can."""
    options = [(True, i) for i, w in BW_NARROW[family].items() if w == hz]
    options += [(False, i) for i, w in BW_WIDE[family].items() if w == hz]
    for narrow, idx in options:
        if narrow == cur_narrow:
            return narrow, idx
    return options[0]


VIRTUAL_CABLE_WORDS = ("blackhole", "soundflower", "loopback", "vb-audio", "vb-cable", "voicemeeter",
                       "virtual")


def is_virtual_cable(name):
    """Virtual audio cables (BlackHole, Soundflower ...) belong on the radio ends of the routing,
    never as your speakers or microphone."""
    low = name.lower()
    return any(word in low for word in VIRTUAL_CABLE_WORDS)


def pct_to_db(pct):
    """Slider percent to decibels: 100% is unity gain, 0% is silence."""
    return -120.0 if pct <= 0 else 20 * math.log10(pct / 100)


def level_pct(peak):
    """Map a 16-bit peak value to 0..1 over a -60..0 dBFS scale."""
    if peak <= 0:
        return 0.0
    db = 20 * math.log10(min(peak, 32768) / 32768)
    return max(0.0, min(1.0, (db + 60) / 60))


class Radio:
    """Talks to rigctld in a background thread: one command at a time, wait for its reply."""

    units = "hamlib"
    can_power = False
    can_vfo = False                 # Hamlib's plain protocol cannot read VFO B without switching to it
    can_split = True
    can_bw = False                  # bandwidth control needs the Raw CAT connection
    can_ptt = True
    # Hamlib's AGC levels: 0 off, 1 superfast, 2 fast, 3 slow, 4 user, 5 medium, 6 auto
    AGC_SET = {"Off": 0, "Fast": 2, "Mid": 5, "Slow": 3, "Auto": 6}
    AGC_NAME = {0: "Off", 1: "Fast", 2: "Fast", 3: "Slow", 4: "Mid", 5: "Mid", 6: "Auto"}
    RX_ONLY = ("l STRENGTH",)       # polled only while receiving

    PLAN = [  # (command, reply lines, poll interval seconds, only while transmitting)
        ("t", 1, 0.4, False), ("f", 1, 1.0, False), ("m", 2, 1.0, False),
        ("l RFPOWER", 1, 2.0, False), ("u TUNER", 1, 2.0, False),
        ("l RFPOWER_METER_WATTS", 1, 0.25, True), ("l SWR", 1, 0.25, True),
        ("l ALC", 1, 0.25, True), ("l STRENGTH", 1, 0.5, False),
        ("s", 2, 1.0, False),       # split state: "1" or "0", then the TX VFO
        ("l AGC", 1, 2.0, False),
    ]

    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.userq = queue.SimpleQueue()
        self.thread = None
        self.connected = False
        self.due = {}
        self.buf = ""
        self.failed = set()
        self.state = self._blank()

    @staticmethod
    def _blank():
        return dict(freq=None, mode=None, ptt=None, power_w=None, tuner=None, tuning=False,
                    po_w=None, swr=None, alc=None, po_raw=None, swr_raw=None, alc_raw=None,
                    freq_b=None, vfo_sel=None, split=None, s_raw=None, s_db=None,
                    bw_idx=None, narrow=None, data_mode=None, agc=None, last_rx=0.0)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def read_menus(self, numbers):             # Hamlib cannot reach the radio's menu
        pass

    def write_menu(self, number, payload):
        pass

    def menu_snapshot(self):
        return {}, {}

    def read_functions(self, commands):        # nor to the radio's functions (the Function tab)
        pass

    def write_function(self, set_cmd, read_cmd):
        pass

    def fn_snapshot(self):
        return {}, {}

    def zero_in(self):                          # nor to CW zero-in (the CW window)
        pass

    def send(self, cmd, lines=1, timeout=REPLY_TIMEOUT):
        """Queue a command typed by the user (set commands reply with one RPRT line)."""
        self.userq.put((cmd, lines, timeout))

    # actions used by the panel (CatRadio has the same ones)
    def set_mode(self, ham):
        self.send(f"M {ham} 0")                 # passband 0 = Hamlib's default
        self.send("m", 2)

    def set_power(self, watts):
        self.send(f"L RFPOWER {watts / 100:g}")
        self.send("l RFPOWER")

    def set_tuner(self, on):
        self.send("U TUNER 1" if on else "U TUNER 0")
        self.send("u TUNER")

    def tune_start(self):
        self.send("G TUNE", 1, 30.0)            # may take a while to return

    def tune_stop(self):
        self.send("T 0")                        # PTT off

    def set_split(self, on):
        self.send("S 1 VFOB" if on else "S 0 VFOA")   # split on = transmit on VFO B
        self.send("s", 2)

    def set_ptt(self, on):
        self.send("T 1" if on else "T 0")
        self.send("t")

    def set_agc(self, code):
        self.send(f"L AGC {code}")
        self.send("l AGC")

    def set_freq(self, which, hz):
        if which == "A":                        # rigctld's plain protocol only reaches the current VFO
            self.send(f"F {hz}")
            self.send("f")

    def connect(self, host, port):
        self.disconnect()
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._run, args=(host, port), daemon=True)
        self.thread.start()

    def disconnect(self):
        self.stop_evt.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.thread = None
        self.connected = False

    # ---- worker thread ------------------------------------------------------------
    def _probe_raw(self, host, port):
        """After a timeout: does this port answer raw Yaesu CAT? (a ser2net-style port)"""
        try:
            with socket.create_connection((host, port), timeout=2) as s:
                s.settimeout(1.5)
                s.sendall(b"FA;")
                data = s.recv(100).decode("ascii", "ignore")
        except OSError:
            return
        if data.startswith("FA") and data.rstrip().endswith(";"):
            self.log("This port answers raw Yaesu CAT (it replied " + data.strip() + "), so it "
                     "is a ser2net-style port, not rigctld. Set Connection to Raw CAT (ser2net).")

    def _run(self, host, port):
        warned = False
        hinted = False
        while not self.stop_evt.is_set():
            try:
                sock = socket.create_connection((host, port), timeout=3)
            except OSError as exc:
                if not warned:
                    self.log(f"Cannot connect to {host}:{port}: {exc}. Retrying...")
                    warned = True
                self.stop_evt.wait(2.0)
                continue
            warned = False
            self.connected = True
            self.buf = ""
            self.due = {}
            self.log(f"Connected to {host}:{port}")
            try:
                while not self.stop_evt.is_set():
                    item = self._next_command()
                    if item is None:
                        self.stop_evt.wait(0.02)
                        continue
                    cmd, nlines, timeout = item
                    lines = self._transact(sock, cmd, nlines, timeout)
                    self._apply(cmd, lines)
            except (OSError, TimeoutError) as exc:
                self.log(f"Connection problem ({exc}); reconnecting")
                if isinstance(exc, TimeoutError) and not hinted:
                    hinted = True
                    self.log("No answer from this port. Either rigctld is waiting for a radio "
                             "that is switched off or not answering, or this is not a rigctld "
                             "port.")
                    self._probe_raw(host, port)
            finally:
                self.connected = False
                try:
                    sock.close()
                except OSError:
                    pass
            self.stop_evt.wait(1.0)

    def _transact(self, sock, cmd, nlines, timeout):
        sock.sendall((cmd + "\n").encode("ascii"))
        lines = []
        deadline = time.monotonic() + timeout
        while len(lines) < nlines:
            while "\n" not in self.buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no reply to '{cmd}'")
                sock.settimeout(remaining)
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    raise TimeoutError(f"no reply to '{cmd}'")
                if not data:
                    raise ConnectionError("closed by the server")
                self.buf += data.decode("ascii", "replace")
            line, self.buf = self.buf.split("\n", 1)
            lines.append(line.strip())
            if lines[0].startswith("RPRT"):
                break
        return lines

    def _next_command(self):
        try:
            return self.userq.get_nowait()
        except queue.Empty:
            pass
        now = time.monotonic()
        with self.lock:
            tx_on = bool(self.state["ptt"])
        best = None
        for cmd, nlines, every, tx_only in self.PLAN:
            if tx_only and not tx_on:
                continue
            if cmd in self.RX_ONLY and tx_on:
                continue
            due = self.due.get(cmd, 0.0)
            if due <= now and (best is None or due < best[0]):
                best = (due, cmd, nlines, every)
        if best is None:
            return None
        self.due[best[1]] = now + best[3]
        return best[1], best[2], REPLY_TIMEOUT

    def _apply(self, cmd, lines):
        if not lines:
            return
        if lines[0].startswith("RPRT"):
            try:
                code = int(lines[0].split()[1])
            except (IndexError, ValueError):
                code = -1
            if code != 0 and cmd not in self.failed:
                self.failed.add(cmd)       # report each failing command once
                self.log(f"rigctld reported an error for '{cmd}': {lines[0]}")
            return
        try:
            with self.lock:
                st = self.state
                if cmd == "t":
                    st["ptt"] = lines[0] not in ("0", "")
                elif cmd == "f":
                    st["freq"] = int(float(lines[0]))
                elif cmd == "m":
                    st["mode"] = lines[0]
                elif cmd == "l RFPOWER":
                    st["power_w"] = round(float(lines[0]) * 100)
                elif cmd == "u TUNER":
                    st["tuner"] = lines[0] == "1"
                elif cmd == "l RFPOWER_METER_WATTS":
                    st["po_w"] = float(lines[0])
                elif cmd == "l SWR":
                    st["swr"] = float(lines[0])
                elif cmd == "l ALC":
                    st["alc"] = float(lines[0])
                elif cmd == "l STRENGTH":
                    st["s_db"] = float(lines[0])
                elif cmd == "s":
                    st["split"] = lines[0].strip() == "1"
                elif cmd == "l AGC":
                    st["agc"] = int(float(lines[0]))
                else:
                    return                  # a user command that returned data we don't track
                st["last_rx"] = time.time()
        except ValueError:
            if cmd not in self.failed:
                self.failed.add(cmd)
                self.log(f"Unexpected reply to '{cmd}': {lines}")


class CatRadio:
    """Talks raw Yaesu CAT through a raw TCP serial server such as ser2net.

    With ser2net every connected client sees every reply, so replies are parsed by their
    content, not matched to requests: answers to OTHER clients' queries are used too, and
    anything unrecognised is ignored. Meters arrive as raw 0-255 values."""

    units = "raw"
    can_power = True
    can_vfo = True
    can_split = True
    can_bw = True
    can_ptt = True
    # GT set: 0 off, 1 fast, 2 mid, 3 slow, 4 auto. GT answer: 4, 5, 6 are auto fast, mid, slow.
    AGC_SET = {"Off": 0, "Fast": 1, "Mid": 2, "Slow": 3, "Auto": 4}
    AGC_NAME = {0: "Off", 1: "Fast", 2: "Mid", 3: "Slow", 4: "Auto", 5: "Auto", 6: "Auto"}
    RX_ONLY = ("SM0;",)             # polled only while receiving
    MIN_GAP = 0.03    # seconds between commands we send

    PLAN = [  # (command, poll interval seconds, only while transmitting)
        ("TX;", 0.5, False), ("IF;", 1.0, False), ("PC;", 2.0, False), ("AC;", 2.0, False),
        ("RM5;", 0.25, True), ("RM6;", 0.25, True), ("RM4;", 0.25, True),
        ("FB;", 1.0, False), ("FT;", 1.0, False), ("SM0;", 0.5, False),
        ("SH0;", 1.0, False), ("NA0;", 1.0, False), ("EX062;", 10.0, False),
        ("GT0;", 2.0, False),
    ]

    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.userq = queue.SimpleQueue()
        self.thread = None
        self.connected = False
        self.due = {}
        self.power_job = None       # (when due, command): the PS command that follows dummy data
        self.quiet_until = 0.0      # nothing else is sent on the line until then
        self.state = self._blank()
        self.menu_vals, self.menu_t = {}, {}    # menu number -> raw value, and when it arrived
        self.fn_vals, self.fn_t = {}, {}        # function (command + selector) -> value, and when

    @staticmethod
    def _blank():
        return dict(freq=None, mode=None, ptt=None, power_w=None, tuner=None, tuning=False,
                    po_w=None, swr=None, alc=None, po_raw=None, swr_raw=None, alc_raw=None,
                    freq_b=None, vfo_sel=None, split=None, s_raw=None, s_db=None,
                    bw_idx=None, narrow=None, data_mode=None, agc=None, last_rx=0.0)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def send(self, cmd, lines=1, timeout=0):
        """Queue a raw CAT command typed by the user, for example 'PC050;'."""
        self.userq.put(cmd)

    # the radio's menu (the Menu tab)
    def read_menus(self, numbers):
        for n in numbers:
            self.send(f"EX{n};")

    def write_menu(self, number, payload):
        self.send(f"EX{number}{payload};")
        self.send(f"EX{number};")                 # read it back, to see what the radio kept

    def menu_snapshot(self):
        with self.lock:
            return dict(self.menu_vals), dict(self.menu_t)

    # the radio's functions (the Function tab)
    def read_functions(self, commands):
        for cmd in commands:
            self.send(cmd)

    def write_function(self, set_cmd, read_cmd):
        self.send(set_cmd)
        self.send(read_cmd)                       # read it back, to see what the radio kept

    def fn_snapshot(self):
        with self.lock:
            return dict(self.fn_vals), dict(self.fn_t)

    def zero_in(self):
        self.send("ZI;")                          # CW auto zero-in: the radio tunes itself to the signal it hears

    # actions used by the panel (Radio has the same ones)
    def set_mode(self, ham):
        code = CAT_CODE_OF.get(ham)
        if code:
            self.send(f"MD0{code};")
            self.send("IF;")

    def set_power(self, watts):
        self.send(f"PC{watts:03d};")
        self.send("PC;")

    def set_tuner(self, on):
        self.send("AC001;" if on else "AC000;")
        self.send("AC;")

    def tune_start(self):
        self.send("AC002;")                     # Tuning Start / Stop (a toggle)
        self.send("AC;")

    def tune_stop(self):
        self.send("AC002;")

    def swap_vfo(self):
        """SV; swaps the contents of VFO A and VFO B (frequency and mode). The radio always
        operates on VFO A through CAT, so this is how B's frequency becomes the operating one."""
        self.send("SV;")
        self.send("FA;")
        self.send("FB;")
        self.send("IF;")

    def set_split(self, on):
        """On the FT-991A split is simply 'VFO B transmits' (FT3;); off is 'VFO A transmits'."""
        self.send("FT3;" if on else "FT2;")
        self.send("FT;")

    def set_freq(self, which, hz):
        self.send(f"{'FA' if which == 'A' else 'FB'}{hz:09d};")
        self.send("FA;" if which == "A" else "FB;")
        self.send("IF;")

    def set_bandwidth(self, narrow, idx, change_narrow):
        """Set the NARROW state first (the index means different widths in each), then the width."""
        if change_narrow:
            self.send("NA01;" if narrow else "NA00;")
        self.send(f"SH0{idx:02d};")
        self.send("NA0;")
        self.send("SH0;")

    def set_ptt(self, on):
        """TX1; = CAT TX on, TX0; = off (the CAT manual's TX command)."""
        self.send("TX1;" if on else "TX0;")
        self.send("TX;")

    def set_agc(self, code):
        self.send(f"GT0{code};")
        self.send("GT0;")

    def power(self, on):
        """Switch the radio on or off. The radio needs dummy data first and then PS1;/PS0;
        between one and two seconds later, with nothing else on the line in between."""
        self.userq.put(("POWER", on))

    def connect(self, host, port):
        self.disconnect()
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._run, args=(host, port), daemon=True)
        self.thread.start()

    def disconnect(self):
        self.stop_evt.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.thread = None
        self.connected = False

    def _run(self, host, port):
        warned = False
        while not self.stop_evt.is_set():
            try:
                sock = socket.create_connection((host, port), timeout=3)
            except OSError as exc:
                if not warned:
                    self.log(f"Cannot connect to {host}:{port}: {exc}. Retrying...")
                    warned = True
                self.stop_evt.wait(2.0)
                continue
            warned = False
            sock.settimeout(0.05)
            self.connected = True
            self.log(f"Connected to {host}:{port}")
            self.due = {}
            self.power_job, self.quiet_until = None, 0.0
            self.userq.put("ID;")
            buf, last_send = "", 0.0
            try:
                while not self.stop_evt.is_set():
                    try:
                        data = sock.recv(4096)
                        if not data:
                            raise ConnectionError("connection closed by the server")
                        buf += data.decode("ascii", "ignore")
                        while ";" in buf:
                            msg, buf = buf.split(";", 1)
                            msg = msg.strip()
                            if msg:
                                self._handle(msg)
                        if len(buf) > 300:
                            buf = ""
                    except socket.timeout:
                        pass
                    now = time.monotonic()
                    if now - last_send >= self.MIN_GAP:
                        cmd = self._next_command()
                        if cmd:
                            sock.sendall(cmd.encode("ascii"))
                            last_send = now
            except OSError as exc:
                self.log(f"Connection problem ({exc}); reconnecting")
            finally:
                self.connected = False
                try:
                    sock.close()
                except OSError:
                    pass
            self.stop_evt.wait(1.0)

    def _next_command(self):
        now = time.monotonic()
        if self.power_job and now >= self.power_job[0]:    # the command that follows the dummy
            cmd = self.power_job[1]
            self.power_job = None
            self.quiet_until = now + 0.3
            self.due = {}                                   # poll everything again afterwards
            return cmd
        if now < self.quiet_until:
            return None                     # keep the line silent between dummy data and command
        try:
            item = self.userq.get_nowait()
        except queue.Empty:
            item = None
        if isinstance(item, tuple) and item[0] == "POWER":
            due = now + POWER_WAIT
            self.power_job = (due, "PS1;" if item[1] else "PS0;")
            self.quiet_until = due
            return ";"                      # the dummy data
        if item is not None:
            return item
        with self.lock:
            tx_on = bool(self.state["ptt"])
        best = None
        for cmd, every, tx_only in self.PLAN:
            if tx_only and not tx_on:
                continue
            if cmd in self.RX_ONLY and tx_on:
                continue
            due = self.due.get(cmd, 0.0)
            if due <= now and (best is None or due < best[0]):
                best = (due, cmd, every)
        if best is None:
            return None
        self.due[best[1]] = now + best[2]
        return best[1]

    def _handle(self, msg):
        c = msg[:2]
        with self.lock:
            st = self.state
            try:
                if msg == "?":
                    return
                if c == "IF" and len(msg) >= 27 and msg[5:14].isdigit():
                    st["freq"] = int(msg[5:14])
                    st["mode"] = CAT_MODE.get(msg[21], msg[21])
                elif c == "FA" and len(msg) == 11 and msg[2:].isdigit():
                    st["freq"] = int(msg[2:])
                elif c == "FB" and len(msg) == 11 and msg[2:].isdigit():
                    st["freq_b"] = int(msg[2:])
                elif c == "FT" and len(msg) == 3 and msg[2] in "01":
                    st["vfo_sel"] = "A" if msg[2] == "0" else "B"      # which VFO transmits
                    st["split"] = msg[2] == "1"                        # B transmits = split
                elif c == "SM" and len(msg) == 6 and msg[3:].isdigit():
                    st["s_raw"] = int(msg[3:])
                elif c == "SH" and len(msg) == 5 and msg[3:].isdigit():
                    st["bw_idx"] = int(msg[3:])
                elif c == "NA" and len(msg) == 4 and msg[3] in "01":
                    st["narrow"] = msg[3] == "1"
                elif c == "EX" and len(msg) >= 6 and msg[2:5].isdigit():
                    self.menu_vals[msg[2:5]] = msg[5:]          # any menu item (Menu tab)
                    self.menu_t[msg[2:5]] = time.monotonic()
                    if msg[2:5] == "062":
                        st["data_mode"] = msg[5]               # menu 062 DATA MODE: 0 PSK, 1 OTHER
                elif c == "GT" and len(msg) == 4 and msg[3].isdigit():
                    st["agc"] = int(msg[3])
                elif c in FN_CMDS:                                   # a function (Function tab)
                    key = next((p for p in FN_BY_CMD[c] if msg.startswith(p)), None)
                    if (key is None or len(msg) != len(key) + FN_WIDTH[key]
                            or not msg[len(key):].isdigit()):
                        return
                    self.fn_vals[key] = msg[len(key):]
                    self.fn_t[key] = time.monotonic()
                elif c == "MD" and len(msg) == 4:
                    st["mode"] = CAT_MODE.get(msg[3], msg[3])
                elif c == "TX" and len(msg) == 3:
                    st["ptt"] = msg[2] in ("1", "2")
                elif c == "PC" and len(msg) == 5 and msg[2:].isdigit():
                    st["power_w"] = int(msg[2:])
                elif c == "AC" and len(msg) == 5:
                    st["tuner"] = msg[4] in ("1", "2")
                    st["tuning"] = msg[4] == "2"
                elif c == "RM" and len(msg) == 6 and msg[3:].isdigit():
                    key = {"5": "po_raw", "6": "swr_raw", "4": "alc_raw"}.get(msg[2])
                    if key:
                        st[key] = int(msg[3:])
                elif c == "ID" and len(msg) == 6:
                    if msg[2:] != "0670":
                        self.log(f"Warning: radio ID is {msg[2:]}, not 0670 (FT-991A)")
                else:
                    return
                st["last_rx"] = time.time()
            except ValueError:
                pass


# ---- the radio's menu, for the Menu tab ------------------------------------------------------
# Every item of the FT-991A menu (CAT command EX, items 001 to 153), from Yaesu's CAT manual. Each
# item has a kind: opt (a list of choices), num (a number), sgn (a signed number), tz (the time
# zone), hz (a frequency) or ro (read only). "digits" is the width of the value in the command.
# When the radio answers, its own reply decides the width, so a slip in this table cannot send a
# value of the wrong length.

def _N(lo, hi, step=1, digits=3, unit="", zero=None, scale=None):
    return dict(kind="num", lo=lo, hi=hi, step=step, digits=digits, unit=unit, zero=zero,
                scale=scale)


def _S(lo, hi, step, digits, unit=""):
    return dict(kind="sgn", lo=lo, hi=hi, step=step, digits=digits, unit=unit)


def _O(*labels, first=0):
    return dict(kind="opt", opts={str(first + i): lb for i, lb in enumerate(labels)}, digits=1)


def _M(opts, digits=1):
    return dict(kind="opt", opts=opts, digits=digits)


def _CUT(base, step, last):
    """OFF, then base Hz, base+step Hz ... for the filter cut-off items."""
    opts = {"00": "OFF"}
    for n in range(1, last + 1):
        opts[f"{n:02d}"] = f"{base + (n - 1) * step} Hz"
    return dict(kind="opt", opts=opts, digits=2)


def _TZ():
    opts = {}
    for minutes in range(-12 * 60, 14 * 60 + 1, 30):
        h, m = divmod(abs(minutes), 60)
        opts[f"{'-' if minutes < 0 else '+'}{h:02d}{m:02d}"] = f"UTC{'-' if minutes < 0 else '+'}{h}:{m:02d}"
    return dict(kind="opt", opts=opts, digits=5)


_DELAY = _N(20, 4000, 20, 4, "msec")
_LEVEL = _N(0, 100, 1, 3)
_SLOPE = _O("6 dB/oct", "18 dB/oct")
_DISEN = _O("DISABLE", "ENABLE")
_PTT = _O("DAKY", "RTS", "DTR")
_MICSEL = _O("MIC", "REAR")
_COLORS = _O("BLUE", "GRAY", "GREEN", "ORANGE", "PURPLE", "RED", "SKY BLUE")
_LCUT = _CUT(100, 50, 19)
_HCUT = _CUT(700, 50, 67)
_BAUD = _O("4800 bps", "9600 bps", "19200 bps", "38400 bps")
_TOT = _O("10 msec", "100 msec", "1000 msec", "3000 msec")
_EQLEV = _S(-20, 10, 1, 3, "dB")
_EQBW = _N(1, 10, 1, 2)
_CW_MEM = _O("TEXT", "MESSAGE")

_MENU_SPECS = [
    (1, "AGC FAST DELAY", _DELAY), (2, "AGC MID DELAY", _DELAY), (3, "AGC SLOW DELAY", _DELAY),
    (4, "HOME FUNCTION", _O("SCOPE", "FUNCTION")),
    (5, "MY CALL INDICATION", _N(0, 5, 1, 1, "sec")),
    (6, "DISPLAY COLOR", _COLORS),
    (7, "DIMMER LED", _O("1", "2")),
    (8, "DIMMER TFT", _N(0, 15, 1, 2)),
    (9, "BAR MTR PEAK HOLD", _O("OFF", "0.5 sec", "1.0 sec", "2.0 sec")),
    (10, "DVS RX OUT LEVEL", _LEVEL), (11, "DVS TX OUT LEVEL", _LEVEL),
    (12, "KEYER TYPE", _O("OFF", "BUG", "ELEKEY-A", "ELEKEY-B", "ELEKEY-Y", "ACS")),
    (13, "KEYER DOT/DASH", _O("NORMAL", "REVERSE")),
    (14, "CW WEIGHT", _N(25, 45, 1, 2, "", scale=0.1)),
    (15, "BEACON INTERVAL", _N(0, 690, 1, 3, "sec", zero="OFF")),
    (16, "NUMBER STYLE", _O("1290", "AUNO", "AUNT", "A2NO", "A2NT", "12NO", "12NT")),
    (17, "CONTEST NUMBER", _N(0, 9999, 1, 4)),
    (18, "CW MEMORY 1", _CW_MEM), (19, "CW MEMORY 2", _CW_MEM), (20, "CW MEMORY 3", _CW_MEM),
    (21, "CW MEMORY 4", _CW_MEM), (22, "CW MEMORY 5", _CW_MEM),
    (23, "NB WIDTH", _O("1 ms", "3 ms", "10 ms")),
    (24, "NB REJECTION", _O("10 dB", "30 dB", "50 dB")),
    (25, "NB LEVEL", _N(0, 10, 1, 2)),
    (26, "BEEP LEVEL", _LEVEL),
    (27, "TIME ZONE", _TZ()),
    (28, "GPS/232C SELECT", _M({"0": "GPS1", "1": "GPS2", "3": "RS232C"})),
    (29, "232C RATE", _BAUD), (30, "232C TOT", _TOT),
    (31, "CAT RATE", _BAUD), (32, "CAT TOT", _TOT), (33, "CAT RTS", _DISEN),
    (34, "MEM GROUP", _DISEN),
    (35, "QUICK SPLIT FREQ", _S(-20, 20, 1, 3, "kHz")),
    (36, "TX TOT", _N(0, 30, 1, 2, "min", zero="OFF")),
    (37, "MIC SCAN", _DISEN),
    (38, "MIC SCAN RESUME", _O("PAUSE", "TIME")),
    (39, "REF FREQ ADJ", _S(-25, 25, 1, 3)),
    (40, "CLAR MODE SELECT", _O("RX", "TX", "TRX")),
    (41, "AM LCUT FREQ", _LCUT), (42, "AM LCUT SLOPE", _SLOPE),
    (43, "AM HCUT FREQ", _HCUT), (44, "AM HCUT SLOPE", _SLOPE),
    (45, "AM MIC SELECT", _MICSEL), (46, "AM OUT LEVEL", _LEVEL), (47, "AM PTT SELECT", _PTT),
    (48, "AM PORT SELECT", _O("DATA", "USB")), (49, "AM DATA GAIN", _LEVEL),
    (50, "CW LCUT FREQ", _LCUT), (51, "CW LCUT SLOPE", _SLOPE),
    (52, "CW HCUT FREQ", _HCUT), (53, "CW HCUT SLOPE", _SLOPE),
    (54, "CW OUT LEVEL", _LEVEL),
    (55, "CW AUTO MODE", _O("OFF", "50 MHz", "ON")),
    (56, "CW BK-IN TYPE", _O("SEMI BREAK-IN", "FULL BREAK-IN")),
    (57, "CW BK-IN DELAY", _N(30, 3000, 10, 4, "msec")),
    (58, "CW WAVE SHAPE", _O("1 msec", "2 msec", "4 msec", "6 msec")),
    (59, "CW FREQ DISPLAY", _O("DIRECT FREQ", "PITCH OFFSET")),
    (60, "PC KEYING", _O("OFF", "DAKY", "RTS", "DTR")),
    (61, "QSK DELAY TIME", _O("15 msec", "20 msec", "25 msec", "30 msec")),
    (62, "DATA MODE", _O("PSK", "OTHER")),
    (63, "PSK TONE", _O("1000 Hz", "1500 Hz", "2000 Hz")),
    (64, "OTHER DISP (SSB)", _S(-3000, 3000, 10, 5, "Hz")),
    (65, "OTHER SHIFT (SSB)", _S(-3000, 3000, 10, 5, "Hz")),
    (66, "DATA LCUT FREQ", _LCUT), (67, "DATA LCUT SLOPE", _SLOPE),
    (68, "DATA HCUT FREQ", _HCUT), (69, "DATA HCUT SLOPE", _SLOPE),
    (70, "DATA IN SELECT", _MICSEL), (71, "DATA PTT SELECT", _PTT),
    (72, "DATA PORT SELECT", _O("DATA", "USB", first=1)),
    (73, "DATA OUT LEVEL", _LEVEL),
    (74, "FM MIC SELECT", _MICSEL), (75, "FM OUT LEVEL", _LEVEL),
    (76, "FM PKT PTT SELECT", _PTT),
    (77, "FM PKT PORT SELECT", _O("DATA", "USB", first=1)),
    (78, "FM PKT TX GAIN", _LEVEL),
    (79, "FM PKT MODE", _O("1200", "9600")),
    (80, "RPT SHIFT 28MHz", _N(0, 1000, 10, 4, "kHz")),
    (81, "RPT SHIFT 50MHz", _N(0, 4000, 10, 4, "kHz")),
    (82, "RPT SHIFT 144MHz", _N(0, 4000, 10, 4, "kHz")),
    (83, "RPT SHIFT 430MHz", _N(0, 10000, 10, 5, "kHz")),
    (84, "ARS 144MHz", _O("OFF", "ON")), (85, "ARS 430MHz", _O("OFF", "ON")),
    (86, "DCS POLARITY", _O("Tn-Rn", "Tn-Riv", "Tiv-Rn", "Tiv-Riv")),
    (87, "RADIO ID", dict(kind="ro")),
    (88, "GM DISPLAY", _O("DISTANCE", "STRENGTH")),
    (89, "DISTANCE", _O("km", "mile")),
    (90, "AMS TX MODE", _O("AUTO", "MANUAL", "DN", "VW", "ANALOG")),
    (91, "STANDBY BEEP", _O("OFF", "ON")),
    (92, "RTTY LCUT FREQ", _LCUT), (93, "RTTY LCUT SLOPE", _SLOPE),
    (94, "RTTY HCUT FREQ", _HCUT), (95, "RTTY HCUT SLOPE", _SLOPE),
    (96, "RTTY SHIFT PORT", _O("SHIFT", "DTR", "RTS")),
    (97, "RTTY POLARITY-RX", _O("NORMAL", "REVERSE")),
    (98, "RTTY POLARITY-TX", _O("NORMAL", "REVERSE")),
    (99, "RTTY OUT LEVEL", _LEVEL),
    (100, "RTTY SHIFT FREQ", _O("170 Hz", "200 Hz", "425 Hz", "850 Hz")),   # the manual lists 1,1,2,3: 170 Hz can only be 0
    (101, "RTTY MARK FREQ", _O("1275 Hz", "2125 Hz", first=1)),
    (102, "SSB LCUT FREQ", _LCUT), (103, "SSB LCUT SLOPE", _SLOPE),
    (104, "SSB HCUT FREQ", _HCUT), (105, "SSB HCUT SLOPE", _SLOPE),
    (106, "SSB MIC SELECT", _MICSEL), (107, "SSB OUT LEVEL", _LEVEL),
    (108, "SSB PTT SELECT", _PTT),
    (109, "SSB PORT SELECT", _O("DATA", "USB")),
    (110, "SSB TX BPF", _O("50-3000", "100-2900", "200-2800", "300-2700", "400-2600")),
    (111, "APF WIDTH", _O("NARROW", "MEDIUM", "WIDE")),
    (112, "CONTOUR LEVEL", _S(-40, 20, 1, 3, "dB")),
    (113, "CONTOUR WIDTH", _N(1, 11, 1, 2)),
    (114, "IF NOTCH WIDTH", _O("NARROW", "WIDE")),
    (115, "SCP DISPLAY MODE", _O("SPECTRUM", "WATER FALL")),
    (116, "SCP SPAN FREQ", _M({"03": "50 kHz", "04": "100 kHz", "05": "200 kHz",
                               "06": "500 kHz", "07": "1000 kHz"}, 2)),
    (117, "SPECTRUM COLOR", _COLORS),
    (118, "WATER FALL COLOR", _O("BLUE", "GRAY", "GREEN", "ORANGE", "PURPLE", "RED", "SKY BLUE",
                                 "MULTI")),
    (119, "PRMTRC EQ1 FREQ", _CUT(100, 100, 7)), (120, "PRMTRC EQ1 LEVEL", _EQLEV),
    (121, "PRMTRC EQ1 BWTH", _EQBW),
    (122, "PRMTRC EQ2 FREQ", _CUT(700, 100, 9)), (123, "PRMTRC EQ2 LEVEL", _EQLEV),
    (124, "PRMTRC EQ2 BWTH", _EQBW),
    (125, "PRMTRC EQ3 FREQ", _CUT(1500, 100, 18)), (126, "PRMTRC EQ3 LEVEL", _EQLEV),
    (127, "PRMTRC EQ3 BWTH", _EQBW),
    (128, "P-PRMTRC EQ1 FREQ", _CUT(100, 100, 7)), (129, "P-PRMTRC EQ1 LEVEL", _EQLEV),
    (130, "P-PRMTRC EQ1 BWTH", _EQBW),
    (131, "P-PRMTRC EQ2 FREQ", _CUT(700, 100, 9)), (132, "P-PRMTRC EQ2 LEVEL", _EQLEV),
    (133, "P-PRMTRC EQ2 BWTH", _EQBW),
    (134, "P-PRMTRC EQ3 FREQ", _CUT(1500, 100, 18)), (135, "P-PRMTRC EQ3 LEVEL", _EQLEV),
    (136, "P-PRMTRC EQ3 BWTH", _EQBW),
    (137, "HF TX MAX POWER", _N(5, 100, 1, 3, "W")),
    (138, "50M TX MAX POWER", _N(5, 100, 1, 3, "W")),
    (139, "144M TX MAX POWER", _N(5, 50, 1, 3, "W")),
    (140, "430M TX MAX POWER", _N(5, 50, 1, 3, "W")),
    (141, "TUNER SELECT", _O("OFF", "INTERNAL", "EXTERNAL", "ATAS", "LAMP")),
    (142, "VOX SELECT", _O("MIC", "DATA")),
    (143, "VOX GAIN", _LEVEL), (144, "VOX DELAY", _N(30, 3000, 10, 4, "msec")),
    (145, "ANTI VOX GAIN", _LEVEL),
    (146, "DATA VOX GAIN", _LEVEL), (147, "DATA VOX DELAY", _N(30, 3000, 10, 4, "msec")),
    (148, "ANTI DVOX GAIN", _LEVEL),
    (149, "EMERGENCY FREQ TX", _DISEN),
    (150, "PRT/WIRES FREQ", _O("MANUAL", "PRESET")),
    # the manual gives 00030000 to 47000000 with no unit (the radio reaches 470 MHz, so a step is
    # most likely 10 Hz); it is shown as the plain number the radio uses
    (151, "PRESET FREQUENCY", _N(30000, 47000000, 1, 8)),
    (152, "SEARCH SETUP", _O("HISTORY", "ACTIVITY")),
    (153, "WIRES DG-ID", _N(0, 99, 1, 2, "", zero="AUTO")),
]
MENU = {f"{n:03d}": dict(spec, num=f"{n:03d}", name=name) for n, name, spec in _MENU_SPECS}

# The sections of the Menu tab: (title, first item, last item)
MENU_GROUPS = [
    ("AGC, display, general", 1, 9), ("Voice memory (DVS)", 10, 11),
    ("CW keyer, memories", 12, 22), ("Noise blanker", 23, 25),
    ("Beep, clock, CAT", 26, 33), ("Memory, split, timers", 34, 40),
    ("AM", 41, 49), ("CW", 50, 61), ("Data modes", 62, 73),
    ("FM, repeaters", 74, 86), ("Digital, ID", 87, 91),
    ("RTTY", 92, 101), ("SSB", 102, 110),
    ("Filters, contour", 111, 118), ("Parametric equalizer", 119, 136),
    ("TX power, tuner", 137, 141), ("VOX", 142, 148),
    ("Emergency, WIRES-X", 149, 153),
]


def menu_group_title(index):
    title, first, last = MENU_GROUPS[index]
    return f"{title}  {first:03d}-{last:03d}"

# Items that ask first, and why
MENU_WARN = {
    "031": "CAT RATE sets the speed of the radio's CAT port. The radio switches at once, and "
           "the panel (and ser2net) keep using the old speed, so the connection will stop working "
           "until they are set to match.",
    "032": "CAT TOT sets how long the radio waits for the end of a CAT command. A very short time "
           "can make it ignore commands from a remote connection.",
    "033": "With CAT RTS enabled the radio only listens to CAT while the RTS line is set. A "
           "connection through ser2net usually does not control that line, so the panel may "
           "lose control of the radio.",
    "039": "REF FREQ ADJ moves the radio's frequency reference, which shifts every frequency it "
           "transmits and receives. Change it only if you can measure the result.",
    "149": "EMERGENCY FREQ TX ENABLE lets the radio transmit on the emergency frequencies.",
}


def menu_decode(item, raw):
    """The value in a radio reply as a number (num, sgn, hz, time zone in minutes) or the text."""
    kind = item["kind"]
    try:
        if kind in ("num", "hz"):
            return int(raw)
        if kind == "sgn":
            return (-1 if raw[0] == "-" else 1) * int(raw[1:])
    except (ValueError, IndexError):
        return None
    return raw


def menu_encode(item, value, width=None):
    """The text to send for a value; width is the width of the radio's own last reply."""
    kind = item["kind"]
    if kind in ("num", "hz"):
        return f"{int(round(value)):0{width or item['digits']}d}"
    if kind == "sgn":
        return ("-" if value < 0 else "+") + f"{abs(int(round(value))):0{(width or item['digits']) - 1}d}"
    return str(value)


def menu_text(item, value):
    """A value as shown next to a slider."""
    if value is None:
        return "?"
    if item["kind"] == "num":
        if item.get("zero") is not None and value == 0:
            return item["zero"]
        shown = f"{value * item['scale']:.1f}" if item.get("scale") else str(int(value))
        return f"{shown} {item['unit']}".strip()
    if item["kind"] == "sgn":
        return f"{int(value):+d} {item['unit']}".strip()
    return str(value)


# ---- the radio's functions, for the Function tab ---------------------------------------------------
# From Yaesu's CAT manual. A function has a command (two letters), a selector (the digits that come
# before the value) and the width of its value. To read one, send COMMAND+SELECTOR+";"; the answer is
# COMMAND+SELECTOR+VALUE+";" and is recognised by its content, as every other reply is. "kind" is:
#   toggle  two values, (off, on)         choice  a list of values and their names
#   slider  a number from lo to hi        step    the size of one step
# "rx" marks what changes the sound of the receiver (which matters to a noise calibration).
# Not here, on purpose: AGC, narrow, bandwidth, the tuner and the power (they are on the Radio tab),
# the meters (ALC, power, SWR), and the radio's own volume (the panel never changes it).

FN_PAGES = ("Receive", "Noise", "Transmit", "CW", "FM etc")

CTCSS_HZ = (67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5,
            107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7,
            159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8,
            196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1)


def _fn(ident, page, label, cmd, sel, width, kind, rx=False, warn=None, **kw):
    item = dict(id=ident, page=page, label=label, cmd=cmd, sel=sel, width=width, kind=kind,
                rx=rx, warn=warn, prefix=cmd + sel, read=f"{cmd}{sel};")
    item.update(kw)
    return item


def _hz(v):
    return f"{v} Hz"


def _ms(v):
    return f"{v} ms"


FUNCTIONS = [
    # --- Receive
    _fn("ipo", 0, "Preamp", "PA", "0", 1, "choice", rx=True,
        opts={"0": "IPO (preamp off)", "1": "AMP 1", "2": "AMP 2"}),
    _fn("att", 0, "Attenuator", "RA", "0", 1, "toggle", rx=True, opts=("0", "1")),
    _fn("rfg", 0, "RF gain", "RG", "0", 3, "slider", rx=True, lo=0, hi=255, step=1, fmt=str),
    _fn("sql", 0, "Squelch", "SQ", "0", 3, "slider", rx=True, lo=0, hi=100, step=1, fmt=str),
    _fn("cnt", 0, "Contour", "CO", "00", 4, "toggle", rx=True, opts=("0000", "0001")),
    _fn("cnf", 0, "Contour frequency", "CO", "01", 4, "slider", rx=True, lo=10, hi=3200, step=10,
        fmt=_hz),
    _fn("apf", 0, "APF (CW)", "CO", "02", 4, "toggle", rx=True, opts=("0000", "0001")),
    # --- Noise
    _fn("nb", 1, "Noise blanker", "NB", "0", 1, "toggle", rx=True, opts=("0", "1")),
    _fn("nbl", 1, "Noise blanker level", "NL", "0", 3, "slider", rx=True, lo=0, hi=10, step=1,
        fmt=str),
    _fn("dnr", 1, "DNR noise reduction", "NR", "0", 1, "toggle", rx=True,
        opts=("0", "1")),
    _fn("dnl", 1, "DNR level", "RL", "0", 2, "slider", rx=True, lo=1, hi=15, step=1, fmt=str),
    _fn("anf", 1, "Auto notch", "BC", "0", 1, "toggle", rx=True, opts=("0", "1")),
    _fn("mnf", 1, "Manual notch", "BP", "00", 3, "toggle", rx=True, opts=("000", "001")),
    _fn("mnq", 1, "Notch frequency", "BP", "01", 3, "slider", rx=True, lo=1, hi=320, step=1,
        fmt=lambda v: f"{v * 10} Hz"),
    # --- Transmit
    _fn("prc", 2, "Speech processor", "PR", "0", 1, "toggle", opts=("1", "2")),
    _fn("prl", 2, "Processor level", "PL", "", 3, "slider", lo=0, hi=100, step=1, fmt=str),
    _fn("meq", 2, "Mic equalizer", "PR", "1", 1, "toggle", opts=("1", "2")),
    _fn("mic", 2, "Mic gain", "MG", "", 3, "slider", lo=0, hi=100, step=1, fmt=str),
    _fn("mon", 2, "Monitor", "ML", "0", 3, "toggle", opts=("000", "001")),
    _fn("mol", 2, "Monitor level", "ML", "1", 3, "slider", lo=0, hi=100, step=1, fmt=str),
    _fn("vox", 2, "VOX", "VX", "", 1, "toggle", opts=("0", "1"),
        warn="VOX makes the radio transmit whenever sound reaches its microphone or data input. "
             "That includes sound the panel sends from this computer's microphone, and FreeDV's "
             "audio. Turn it on only if you want that."),
    _fn("vxg", 2, "VOX gain", "VG", "", 3, "slider", lo=0, hi=100, step=1, fmt=str),
    _fn("vxd", 2, "VOX delay", "VD", "", 4, "slider", lo=30, hi=3000, step=10, fmt=_ms),
    # --- CW
    _fn("kyr", 3, "Keyer", "KR", "", 1, "toggle", opts=("0", "1")),
    _fn("bki", 3, "Break-in", "BI", "", 1, "toggle", opts=("0", "1")),
    _fn("wpm", 3, "Keyer speed", "KS", "", 3, "slider", lo=4, hi=60, step=1,
        fmt=lambda v: f"{v} WPM"),
    _fn("ptc", 3, "Sidetone pitch", "KP", "", 2, "slider", lo=0, hi=75, step=1,
        fmt=lambda v: f"{300 + 10 * v} Hz"),
    _fn("bkd", 3, "Break-in delay", "SD", "", 4, "slider", lo=30, hi=3000, step=10, fmt=_ms),
    _fn("csp", 3, "CW spot (tuning tone)", "CS", "", 1, "toggle", opts=("0", "1")),
    # --- FM and more
    _fn("shf", 4, "Repeater shift", "OS", "0", 1, "choice",
        opts={"0": "Simplex", "1": "Plus (+)", "2": "Minus (-)"}),
    _fn("ctm", 4, "Tone mode", "CT", "0", 1, "choice",
        opts={"0": "Off", "1": "Tone enc/dec", "2": "Tone encode",
              "3": "DCS enc/dec", "4": "DCS encode"}),
    _fn("ctn", 4, "CTCSS tone", "CN", "00", 3, "choice",
        opts={f"{i:03d}": f"{hz:.1f} Hz" for i, hz in enumerate(CTCSS_HZ)}),
    _fn("lck", 4, "Dial lock (VFO A)", "LK", "", 1, "toggle", opts=("0", "1")),
    _fn("fst", 4, "Fast step (VFO A)", "FS", "", 1, "toggle", opts=("0", "1")),
]
FN = {f["id"]: f for f in FUNCTIONS}
FN_WIDTH = {f["prefix"]: f["width"] for f in FUNCTIONS}
FN_CMDS = {f["cmd"] for f in FUNCTIONS}
FN_BY_CMD = {c: [f["prefix"] for f in FUNCTIONS if f["cmd"] == c] for c in FN_CMDS}


def fn_text(item, raw):
    """What a value from the radio is called on screen."""
    if item["kind"] == "toggle":
        off, on = item["opts"]
        return "ON" if raw == on else "OFF" if raw == off else f"({raw})"
    if item["kind"] == "choice":
        return item["opts"].get(raw, f"({raw})")
    try:
        return item["fmt"](int(raw))
    except ValueError:
        return raw


class SettingsTab(ttk.Frame):
    """The radio's menu, items 001 to 153: read each item from the radio and change it, as the
    radio's own menu does. It needs the raw CAT connection (Hamlib has no way to reach the menu).

    Two-choice items that are OFF/ON or DISABLE/ENABLE are a check box, other lists are a pull-down,
    numbers are a slider (or an entry for the very wide ranges), and the frequency is an entry in
    MHz. A change is written at once and read back, so what is shown is always what the radio
    says. Nothing is written while the radio transmits, and a few items ask first."""

    TOGGLES = (("OFF", "ON"), ("DISABLE", "ENABLE"))

    def __init__(self, parent, panel):
        super().__init__(parent, padding=(6, 4))
        ttk.Style().configure("Compact.TButton", padding=(5, 1), width=0)
        self.panel = panel
        self.columnconfigure(0, weight=1)
        self.rows = {}            # menu number -> how it is drawn
        self.group_frames = {}
        self.shown = {}           # menu number -> the raw value that is on screen
        self.busy = set()         # items the user is changing right now
        self.pending = {}         # menu number -> (value written, time) until the radio answers
        self.group_index = None
        self.last_refresh = 0.0
        self.requested = set()    # sections already read from the radio since it connected
        self.was_talking = False

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Section:").grid(row=0, column=0)
        self.group_var = tk.StringVar()
        self.group_cb = ttk.Combobox(top, textvariable=self.group_var, state="readonly", width=27,
                                     values=[menu_group_title(i) for i in range(len(MENU_GROUPS))])
        self.group_cb.grid(row=0, column=1, padx=4)
        self.group_cb.bind("<<ComboboxSelected>>", lambda e: self.show_group(self.group_cb.current()))
        buttons = ttk.Frame(top)                       # on their own line, to keep the tab narrow
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        ttk.Button(buttons, text="Read section", style="Compact.TButton",
                   command=self.read_section).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(buttons, text="Read all 153", style="Compact.TButton",
                   command=self.read_all).grid(row=0, column=1, padx=2)
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, foreground="#555555", wraplength=430,
                  justify="left").grid(row=1, column=0, sticky="w", pady=(3, 3))
        box = ttk.Frame(self)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(box, height=300, highlightthickness=0, yscrollincrement=20)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=sb.set)
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.inner)
        self.group_cb.current(0)
        self.show_group(0, read=False)

    # ---- scrolling: the wheel scrolls the list and never changes a setting -------------
    def _bind_wheel(self, widget):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, self._wheel)

    def _wheel(self, event):
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")
        return "break"             # a combo box under the pointer must not take the wheel

    # ---- sections and rows ---------------------------------------------------------------
    def numbers(self, index=None):
        if index is None:
            return sorted(MENU)
        _, first, last = MENU_GROUPS[index]
        return [f"{n:03d}" for n in range(first, last + 1)]

    def show_group(self, index, read=True):
        if self.group_index is not None:
            self.group_frames[self.group_index].grid_remove()
        self.group_index = index
        frame = self.group_frames.get(index) or self.build_group(index)
        frame.grid(row=0, column=0, sticky="ew")
        self.canvas.yview_moveto(0)
        self.group_var.set(menu_group_title(index))
        for num in self.numbers(index):
            self.shown.pop(num, None)               # draw the latest values again
        if read:
            self.read_section()

    def build_group(self, index):
        frame = ttk.Frame(self.inner)
        frame.columnconfigure(0, minsize=190)
        self._bind_wheel(frame)
        for r, num in enumerate(self.numbers(index)):
            self.add_row(frame, r, MENU[num])
        self.group_frames[index] = frame
        return frame

    def add_row(self, frame, r, item):
        num, kind = item["num"], item["kind"]
        label = ttk.Label(frame, text=f"{num}  {item['name']}", anchor="w")
        label.grid(row=r, column=0, sticky="w", pady=1)
        row = dict(item=item)
        extra = []
        if kind == "opt":
            labels = list(item["opts"].values())
            if tuple(labels) in self.TOGGLES:
                var = tk.IntVar(value=0)
                chk = ttk.Checkbutton(frame, variable=var, command=lambda n=num: self.on_toggle(n))
                chk.grid(row=r, column=1, sticky="w")
                lbl = ttk.Label(frame, text="", width=10)
                lbl.grid(row=r, column=2, sticky="w")
                row.update(ui="toggle", var=var, lbl=lbl)
                extra = [chk, lbl]
            else:
                var = tk.StringVar()
                cb = ttk.Combobox(frame, textvariable=var, values=labels, state="readonly", width=17)
                cb.grid(row=r, column=1, columnspan=2, sticky="w")
                cb.bind("<<ComboboxSelected>>", lambda e, n=num: self.on_choose(n))
                row.update(ui="choice", var=var)
                extra = [cb]
        elif kind in ("num", "sgn") and (item["hi"] - item["lo"]) / item["step"] <= 1000:
            var = tk.DoubleVar(value=item["lo"])
            sc = ttk.Scale(frame, from_=item["lo"], to=item["hi"], variable=var, length=150,
                           command=lambda v, n=num: self.on_slide(n))
            sc.grid(row=r, column=1, sticky="w")
            lbl = ttk.Label(frame, text="", width=11)
            lbl.grid(row=r, column=2, sticky="w")
            sc.bind("<ButtonPress-1>", lambda e, n=num: self.busy.add(n))
            sc.bind("<ButtonRelease-1>", lambda e, n=num: self.on_slide_done(n))
            sc.bind("<KeyRelease>", lambda e, n=num: self.on_slide_done(n))
            row.update(ui="slider", var=var, lbl=lbl)
            extra = [sc, lbl]
        elif kind in ("num", "hz"):
            var = tk.StringVar()
            ent = ttk.Entry(frame, textvariable=var, width=12)
            ent.grid(row=r, column=1, sticky="w")
            unit = "MHz" if kind == "hz" else (item["unit"] or f"{item['lo']} to {item['hi']}")
            lbl = ttk.Label(frame, text=unit)
            lbl.grid(row=r, column=2, sticky="w")
            ent.bind("<Return>", lambda e, n=num: self.on_entry(n))
            ent.bind("<FocusIn>", lambda e, n=num: self.busy.add(n))
            ent.bind("<FocusOut>", lambda e, n=num: self.entry_left(n))
            row.update(ui="entry", var=var)
            extra = [ent, lbl]
        else:                                           # read only
            var = tk.StringVar()
            lbl = ttk.Label(frame, textvariable=var)
            lbl.grid(row=r, column=1, columnspan=2, sticky="w")
            row.update(ui="ro", var=var)
            extra = [lbl]
        for w in [label] + extra:
            self._bind_wheel(w)
        self.rows[num] = row

    # ---- what is on screen ------------------------------------------------------------------
    @staticmethod
    def entry_text(item, raw):
        try:
            return f"{int(raw) / 1_000_000:.6f}" if item["kind"] == "hz" else str(int(raw))
        except ValueError:
            return raw

    @staticmethod
    def describe(item, raw):
        if item["kind"] == "opt":
            return item["opts"].get(raw, raw)
        if item["kind"] == "hz":
            return SettingsTab.entry_text(item, raw) + " MHz"
        if item["kind"] in ("num", "sgn"):
            return menu_text(item, menu_decode(item, raw))
        return raw

    def set_widget(self, num, raw):
        row, item = self.rows[num], self.rows[num]["item"]
        ui = row["ui"]
        if ui == "toggle":
            row["var"].set(1 if raw == list(item["opts"])[1] else 0)
            row["lbl"].configure(text=item["opts"].get(raw, f"({raw})"))
        elif ui == "choice":
            row["var"].set(item["opts"].get(raw, f"(unknown value {raw})"))
        elif ui == "slider":
            value = menu_decode(item, raw)
            if value is not None:
                row["var"].set(max(item["lo"], min(item["hi"], value)))
            row["lbl"].configure(text=menu_text(item, value))
        elif ui == "entry":
            row["var"].set(self.entry_text(item, raw))
        else:
            row["var"].set(raw)

    def refresh(self):
        """Called often by the panel while this tab is showing: bring the screen in line with
        what the radio has said, and check that writes were accepted."""
        now = time.monotonic()
        if now - self.last_refresh < 0.3:
            return
        self.last_refresh = now
        radio = self.panel.radio
        talking = self.can_talk()
        if talking != self.was_talking:                 # (re)connected: read what is shown again
            self.was_talking = talking
            self.requested.clear()
        if talking and self.group_index not in self.requested:
            self.read_section()
        vals, times = radio.menu_snapshot()
        for num, (payload, when) in list(self.pending.items()):
            item = MENU[num]
            if times.get(num, 0.0) > when:
                got = vals.get(num)
                if got != payload and not (got is not None and
                                           menu_decode(item, got) == menu_decode(item, payload)):
                    self.panel.append_log(f"Menu {num} {item['name']}: the radio did not accept "
                                          f"{self.describe(item, payload)}; it says "
                                          f"{self.describe(item, got) if got else '?'}")
                    self.shown.pop(num, None)           # put the control back to what the radio kept
                del self.pending[num]
            elif now - when > 5.0:
                self.panel.append_log(f"Menu {num} {item['name']}: no answer from the radio")
                self.shown.pop(num, None)
                del self.pending[num]
        for num, row in self.rows.items():
            raw = vals.get(num)
            if raw is None or num in self.busy or self.shown.get(num) == raw:
                continue
            self.shown[num] = raw
            self.set_widget(num, raw)
        if radio.units != "raw":
            self.status.set("The Menu tab needs the Raw CAT connection. You are using Hamlib, "
                            "which cannot reach the radio's menu.")
        elif not radio.connected:
            self.status.set("Not connected to the radio.")
        else:
            here = self.numbers(self.group_index)
            self.status.set(f"{sum(1 for n in here if n in vals)} of {len(here)} items in this "
                            f"section read; {len(vals)} of 153 in all. A change is written to "
                            "the radio as soon as you make it.")

    # ---- reading ------------------------------------------------------------------------------
    def can_talk(self):
        radio = self.panel.radio
        return radio.units == "raw" and radio.connected

    def read_section(self):
        if self.can_talk():
            self.requested.add(self.group_index)
            self.panel.radio.read_menus(self.numbers(self.group_index))

    def read_all(self):
        if self.can_talk():
            self.panel.radio.read_menus(self.numbers())
            self.panel.append_log("Reading all 153 menu items from the radio (a few seconds)")

    # ---- writing ------------------------------------------------------------------------------
    def width_for(self, num):
        vals, _ = self.panel.radio.menu_snapshot()
        raw = vals.get(num)
        return len(raw) if raw else None            # the radio's own reply decides the width

    def restore(self, num):
        self.shown.pop(num, None)                   # draw what the radio says again
        self.busy.discard(num)

    def commit(self, num, payload):
        p, item = self.panel, MENU[num]
        if not self.can_talk():
            messagebox.showinfo("Menu", "The Menu tab needs the Raw CAT connection to the "
                                            "radio.")
            return self.restore(num)
        if p.radio.snapshot().get("ptt"):
            messagebox.showinfo("Menu", "The radio is transmitting. Change menu items while it "
                                            "is receiving.")
            return self.restore(num)
        if self.shown.get(num) == payload:
            return
        warn = MENU_WARN.get(num)
        if warn and not messagebox.askokcancel(f"{num} {item['name']}", warn + "\n\nChange it anyway?"):
            return self.restore(num)
        p.radio.write_menu(num, payload)
        self.pending[num] = (payload, time.monotonic())
        p.append_log(f"Menu {num} {item['name']} set to {self.describe(item, payload)}")

    def on_toggle(self, num):
        keys = list(MENU[num]["opts"])
        self.commit(num, keys[1] if self.rows[num]["var"].get() else keys[0])

    def on_choose(self, num):
        item = MENU[num]
        label = self.rows[num]["var"].get()
        key = next((k for k, v in item["opts"].items() if v == label), None)
        if key is None:
            return self.restore(num)
        self.commit(num, key)

    @staticmethod
    def quantize(item, value):
        value = item["lo"] + round((float(value) - item["lo"]) / item["step"]) * item["step"]
        return max(item["lo"], min(item["hi"], value))

    def on_slide(self, num):
        item, row = MENU[num], self.rows[num]
        row["lbl"].configure(text=menu_text(item, self.quantize(item, row["var"].get())))

    def on_slide_done(self, num):
        self.busy.discard(num)
        item = MENU[num]
        value = self.quantize(item, self.rows[num]["var"].get())
        self.commit(num, menu_encode(item, value, self.width_for(num)))

    def entry_left(self, num):
        self.busy.discard(num)
        self.restore(num)                            # an edit that was not confirmed with Return

    def on_entry(self, num):
        item, row = MENU[num], self.rows[num]
        text = row["var"].get().strip()
        try:
            value = round(float(text) * 1_000_000) if item["kind"] == "hz" else int(text)
        except ValueError:
            messagebox.showinfo("Menu", f"'{text}' is not a number.")
            return self.restore(num)
        if not item["lo"] <= value <= item["hi"]:
            low, high = ((f"{item['lo'] / 1e6:g}", f"{item['hi'] / 1e6:g} MHz") if item["kind"] == "hz"
                         else (item["lo"], item["hi"]))
            messagebox.showinfo("Menu", f"{item['name']} must be from {low} to {high}.")
            return self.restore(num)
        self.busy.discard(num)
        self.commit(num, menu_encode(item, value, self.width_for(num)))


CWD_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,?/=:;'\"@()*!&<>")
CWD_CHUNK = 120          # cwdaemon takes one network packet per request: keep each short
CWD_PORT = 6789
CWD_MACROS = ("CQ CQ CQ DE [MYCALL] [MYCALL] K", "[CALL] DE [MYCALL] K", "TU 73 DE [MYCALL] <",
              "QRZ? DE [MYCALL] K", "AGN PSE ?")


def cwd_clean(text):
    """What cwdaemon can send: capitals, digits, space and . , ? / = : ; ' " @ and its prosign
    characters (* AR, = BT, < SK, ( KN, ! SN, & AS, > BK). A + or - would change its speed in the
    middle of a message, and ~ adds a half space, so they are left out."""
    kept = "".join(c for c in text.upper() if c in CWD_OK)
    return " ".join(kept.split())


def cwd_chunks(text, size=CWD_CHUNK):
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > size:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


class CwDaemonLink:
    """The panel's link to cwdaemon, a small free program that runs on the radio's computer and keys
    the radio through the DTR line of its second USB serial port. The Morse timing is done there, in
    the radio's computer, so the network cannot spoil it. The link is UDP: a text message is sent as
    it is, and a few escape codes (ESC 2 <wpm> sets the speed, ESC 4 aborts) control it. To know when
    a message has finished, the panel puts "ESC h <token>" in front of the last part: cwdaemon then
    sends the token back when it has finished sending."""
    ESC = "\x1b"

    def __init__(self):
        self.sock = None
        self.counter = 0
        self.waiting = {}                 # token -> (time sent, seconds it should take)

    def _sock(self):
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
        return self.sock

    def send(self, target, text):
        self._sock().sendto(text.encode("ascii", "ignore"), target)

    @staticmethod
    def seconds_for(text, wpm):
        return len(text) * 9 * 1.2 / max(5, wpm)          # a generous 9 dots a character

    def message(self, target, text, wpm):
        """Send text at wpm words a minute. Returns the token that will come back when it ends."""
        self.counter += 1
        token = f"k{self.counter}"
        self.send(target, f"{self.ESC}2{int(wpm)}")
        parts = cwd_chunks(text)
        for part in parts[:-1]:
            self.send(target, part)
        self.send(target, f"{self.ESC}h{token}")
        self.send(target, parts[-1])
        self.waiting[token] = (time.monotonic(), self.seconds_for(text, wpm))
        return token

    def abort(self, target):
        self.send(target, f"{self.ESC}4")
        self.waiting.clear()

    def poll(self):
        """Tokens that have come back, with the seconds each took."""
        done = []
        while True:
            try:
                data, _addr = self._sock().recvfrom(200)
            except (BlockingIOError, OSError):
                break
            reply = data.decode("ascii", "ignore").strip()
            if reply.startswith("h") and reply[1:] in self.waiting:
                sent, _expected = self.waiting.pop(reply[1:])
                done.append((reply[1:], time.monotonic() - sent))
        return done

    def overdue(self, now, grace=4.0):
        late = [t for t, (sent, expected) in self.waiting.items() if now - sent > expected + grace]
        for t in late:
            del self.waiting[t]
        return late


class CwKeyboard:
    """The inside of the CW window: type text, or press a macro, and cwdaemon (running on the
    radio's computer) keys the radio with it. Abort stops it at once."""

    def __init__(self, page, tab):
        self.tab, self.panel = tab, tab.panel
        self.top = page.winfo_toplevel()                 # the CW window: its dialogs belong to it
        c = self.panel.cfg
        self.link = CwDaemonLink()
        self.text = tk.StringVar()
        self.wpm = tk.IntVar(value=max(5, min(60, int(c.get("cw_wpm", 20)))))
        self.mycall = tk.StringVar(value=str(c.get("cw_mycall", "")))
        self.theircall = tk.StringVar()
        self.host = tk.StringVar(value=str(c.get("cwd_host", "")))
        self.port = tk.StringVar(value=str(c.get("cwd_port", CWD_PORT)))
        self.status = tk.StringVar(value="Ready.")
        saved = c.get("cw_macros")
        macros = list(saved) if isinstance(saved, (list, tuple)) and len(saved) == 5 else list(CWD_MACROS)
        self.macros = [tk.StringVar(value=str(m)) for m in macros]
        box = ttk.Frame(page)
        box.grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(box, textvariable=self.text, width=34)
        self.entry.grid(row=0, column=0, columnspan=3, sticky="w")
        self.entry.bind("<Return>", lambda e: self.on_send())
        self.send_btn = ttk.Button(box, text="Send", style="Compact.TButton", command=self.on_send)
        self.send_btn.grid(row=0, column=3, padx=(4, 1))
        self.abort_btn = ttk.Button(box, text="Abort", style="Compact.TButton", command=self.on_abort)
        self.abort_btn.grid(row=0, column=4, padx=1)
        ttk.Label(box, text="Speed").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.speed = ttk.Scale(box, from_=5, to=60, variable=self.wpm, length=120,
                               command=lambda v: self.on_speed())
        self.speed.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.speed_lbl = ttk.Label(box, text="", width=8)
        self.speed_lbl.grid(row=1, column=3, columnspan=2, sticky="w", pady=(6, 0))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.speed.bind(seq, lambda e: "break")          # the wheel must never change a setting
        cf = ttk.Frame(box)
        cf.grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Label(cf, text="My call").grid(row=0, column=0)
        ttk.Entry(cf, textvariable=self.mycall, width=9).grid(row=0, column=1, padx=(3, 10))
        ttk.Label(cf, text="Their call").grid(row=0, column=2)
        ttk.Entry(cf, textvariable=self.theircall, width=9).grid(row=0, column=3, padx=3)
        self.zero_btn = ttk.Button(cf, text="Zero in", style="Compact.TButton", command=self.on_zero_in)
        self.zero_btn.grid(row=0, column=4, padx=(10, 0))
        self.macro_btns = []
        for i, var in enumerate(self.macros):
            b = ttk.Button(box, text=f"F{i + 1}", width=3, style="Compact.TButton",
                           command=lambda k=i: self.on_macro(k))
            b.grid(row=3 + i, column=0, sticky="w", pady=1)
            self.macro_btns.append(b)
            ttk.Entry(box, textvariable=var, width=37).grid(row=3 + i, column=1, columnspan=4,
                                                            sticky="w", pady=1)
        hf = ttk.Frame(box)
        hf.grid(row=8, column=0, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Label(hf, text="cwdaemon on").grid(row=0, column=0)
        ttk.Entry(hf, textvariable=self.host, width=17).grid(row=0, column=1, padx=3)
        ttk.Label(hf, text="port").grid(row=0, column=2)
        ttk.Entry(hf, textvariable=self.port, width=6).grid(row=0, column=3, padx=3)
        ttk.Label(box, textvariable=self.status, foreground="#333333", wraplength=380,
                  justify="left").grid(row=9, column=0, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Label(box, foreground="#666666", wraplength=380, justify="left", text=(
            "Needs cwdaemon on the radio's computer (cwdaemon -n -d <the radio's second serial port> "
            "-x n) and the radio's menu 060 PC KEYING set to DTR. In macros, [MYCALL] and [CALL] are "
            "replaced. Prosigns: * AR, = BT, < SK, ( KN, ! SN, & AS, > BK. + and - are left out. A blank "
            "address means the radio's computer.")
        ).grid(row=10, column=0, columnspan=5, sticky="w", pady=(4, 0))
        self.on_speed()

    def settings(self):
        """What the panel keeps between runs."""
        return dict(cw_wpm=self.wpm.get(), cw_mycall=self.mycall.get().strip(),
                    cw_macros=[m.get() for m in self.macros], cwd_host=self.host.get().strip(),
                    cwd_port=self.port.get().strip())

    def on_speed(self):
        self.speed_lbl.configure(text=f"{int(round(self.wpm.get()))} wpm")

    def target(self):
        p = self.panel
        ah = getattr(p, "audio_host", None)           # (only there when the sound library is)
        host = self.host.get().strip() or (ah.get().strip() if ah else "") or p.host.get().strip()
        try:
            port = int(self.port.get().strip() or CWD_PORT)
        except ValueError:
            port = 0
        return (host, port) if host and 1024 <= port <= 65535 else None

    def reason_not_to_send(self):
        p = self.panel
        radio, s = p.radio, p.radio.snapshot()
        if not radio.connected:
            return "the panel is not connected to the radio"
        if p.nr_cal.active:
            return "a noise calibration is running, and the radio is on another frequency"
        if (s.get("ptt") or p.ptt_down or s.get("tuning") or p.tune_deadline) and not self.link.waiting:
            return "the radio is transmitting or tuning"
        if s.get("mode") not in ("CW", "CWR"):
            return "the radio is not in a CW mode (CW-U or CW-L)"
        if self.target() is None:
            return "there is no usable cwdaemon address (a host, and a port from 1024 to 65535)"
        return None

    def refuse(self, why, what="Not sent"):
        self.status.set(f"{what}: {why}.")
        self.panel.append_log(f"CW keyboard: {what.lower()}: {why}")
        messagebox.showinfo("CW keyboard", f"{what}: {why}.", parent=self.top)

    def on_zero_in(self):
        """The radio's CW auto zero-in: it tunes itself to the signal it hears. It moves the radio, so
        it has the same refusals as sending."""
        p = self.panel
        radio, s = p.radio, p.radio.snapshot()
        why = None
        if radio.units != "raw" or not radio.connected:
            why = "zero in needs the Raw CAT connection to the radio"
        elif p.nr_cal.active:
            why = "a noise calibration is running, and the radio is on another frequency"
        elif s.get("ptt") or p.ptt_down or s.get("tuning") or p.tune_deadline:
            why = "the radio is transmitting or tuning"
        elif s.get("mode") not in ("CW", "CWR"):
            why = "the radio is not in a CW mode (CW-U or CW-L)"
        if why:
            return self.refuse(why, "Zero in not done")
        radio.zero_in()
        p.append_log("CW: zero in (the radio tunes itself to the signal it hears)")

    def substitute(self, text):
        mine, theirs = cwd_clean(self.mycall.get()), cwd_clean(self.theircall.get())
        if "[MYCALL]" in text.upper():
            if not mine:
                return None, "type your call sign in My call first"
            text = re.sub(r"\[MYCALL\]", mine, text, flags=re.I)
        if "[CALL]" in text.upper():
            if not theirs:
                return None, "type their call sign in Their call first"
            text = re.sub(r"\[CALL\]", theirs, text, flags=re.I)
        return text, None

    def send_text(self, raw):
        why = self.reason_not_to_send()
        if why:
            return self.refuse(why)
        text, problem = self.substitute(raw)
        if problem:
            return self.refuse(problem)
        text = cwd_clean(text)
        if not text:
            return self.refuse("there is nothing to send that cwdaemon can key")
        wpm = int(round(self.wpm.get()))
        target = self.target()
        self.link.message(target, text, wpm)
        self.status.set(f"Sending at {wpm} wpm: {text}")
        self.panel.append_log(f"CW keyboard: sending at {wpm} wpm via {target[0]}:{target[1]}: {text}")

    def on_send(self):
        text = self.text.get()
        if text.strip():
            self.send_text(text)

    def on_macro(self, i):
        self.send_text(self.macros[i].get())

    def on_abort(self):
        target = self.target()
        if target is None:
            return
        self.link.abort(target)                       # always allowed: it can only make the keying stop
        self.status.set("Aborted.")
        self.panel.append_log("CW keyboard: abort sent")

    def refresh(self, now):
        """Called often while the page is showing."""
        for _token, took in self.link.poll():
            self.status.set(f"Finished, in {took:.1f} s.")
        for _token in self.link.overdue(now):
            self.status.set("No confirmation from cwdaemon. Is it running on the radio's computer, and is "
                            "the address right? (Nothing may have been sent.)")
            self.panel.append_log("CW keyboard: cwdaemon did not confirm that the message finished")


class CwWindow:
    """A separate window for sending CW: the text box, the speed, the call signs and the five macros.
    A button beside the log, on every tab of the main window, shows and hides it. It remembers where it
    was and whether it was open. Closing it with its own close button only hides it."""

    def __init__(self, panel):
        self.panel = panel
        c = panel.cfg
        self.top = tk.Toplevel(panel.root)
        self.top.withdraw()
        self.top.title(f"{APP_NAME}: CW")
        self.top.resizable(False, False)
        try:
            set_window_icon(self.top)
        except Exception:
            pass
        self.top.protocol("WM_DELETE_WINDOW", self.hide)
        frame = ttk.Frame(self.top, padding=8)
        frame.grid(row=0, column=0)
        self.kb = CwKeyboard(frame, self)
        geometry = str(c.get("cw_win_geometry", ""))
        self.geometry = geometry if re.fullmatch(r"[+-]\d+[+-]\d+", geometry) else ""
        if c.get("cw_win_open", False):
            panel.root.after(400, self.show)

    def visible(self):
        return self.top.state() == "normal"

    def _place(self):
        """Put it back where it was if that is still on the screen, else beside the main window."""
        root = self.panel.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        m = re.fullmatch(r"([+-]\d+)([+-]\d+)", self.geometry)
        if m and -100 < int(m.group(1)) < sw - 120 and -20 < int(m.group(2)) < sh - 120:
            self.top.geometry(self.geometry)
        else:
            self.top.geometry(f"+{max(0, root.winfo_rootx() + root.winfo_width() + 12)}+{max(0, root.winfo_rooty())}")

    def show(self):
        self._place()
        self.top.deiconify()
        self.top.lift()
        self.panel.cw_btn.configure(text="Hide CW")

    def hide(self):
        if self.visible():
            self.geometry = f"+{self.top.winfo_x()}+{self.top.winfo_y()}"
        self.top.withdraw()
        self.panel.cw_btn.configure(text="CW window")

    def toggle(self):
        self.hide() if self.visible() else self.show()

    def refresh(self):
        """Called by the panel's periodic update, whatever tab is showing."""
        if self.visible():
            self.kb.refresh(time.monotonic())

    def shutdown(self):
        """The panel is closing: a message that is being keyed must stop, as PTT lets go."""
        if self.kb.link.waiting:
            self.kb.on_abort()

    def settings(self):
        out = self.kb.settings()
        out.update(cw_win_open=self.visible(),
                   cw_win_geometry=(f"+{self.top.winfo_x()}+{self.top.winfo_y()}" if self.visible() else self.geometry))
        return out


class FunctionTab(ttk.Frame):
    """The radio's front-panel functions: preamp, attenuator, RF gain, squelch, contour, the noise
    blanker and the radio's own noise reduction, notches, the speech processor, mic gain, monitor,
    VOX, the CW keyer, repeater shift and CTCSS tone, dial lock and fast step. They are in five
    small pages (see FUNCTIONS for each one). It needs the raw CAT connection.

    The tab reads the functions on the page that is showing every couple of seconds, so a change
    made on the radio itself appears here. A change you make is written at once and read back, and
    the control then shows what the radio kept. A function that the radio does not answer in its
    present mode (the manual notch in FM, say) is greyed out. The mouse wheel never changes one."""

    READ_EVERY = 2.0        # seconds between reads of a function that is on screen
    STALE_AFTER = 5.0       # no answer for this long (two reads missed) means the radio does not answer it now

    def __init__(self, parent, panel):
        super().__init__(parent, padding=(6, 4))
        self.panel = panel
        self.columnconfigure(0, weight=1)
        self.rows = {}            # function id -> its widgets
        self.shown = {}           # function id -> the raw value that is on screen
        self.busy = set()         # functions the user is changing right now (a slider being dragged)
        self.pending = {}         # function id -> (value written, time) until the radio answers
        self.asked = {}           # prefix -> when the last read was sent
        self.since = {}           # prefix -> when we began waiting for a first answer
        self.last_refresh = 0.0
        self.was_talking = False
        self.status = tk.StringVar()
        self.pnb = ttk.Notebook(self)
        self.pnb.grid(row=0, column=0, sticky="ew")
        self.pages, used = [], [0] * len(FN_PAGES)
        for title in FN_PAGES:
            page = ttk.Frame(self.pnb, padding=(4, 4))
            page.columnconfigure(0, minsize=150)
            self.pnb.add(page, text=title)
            self.pages.append(page)
        for item in FUNCTIONS:
            self.add_row(self.pages[item["page"]], used[item["page"]], item)
            used[item["page"]] += 1
        ttk.Label(self.pages[2], foreground="#666666", wraplength=380, justify="left", text=(
            "The VOX delay is the MIC or the DATA one, as menu 142 VOX SELECT says."
        )).grid(row=used[2], column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.pnb.bind("<<NotebookTabChanged>>", lambda e: self.on_page())
        ttk.Label(self, textvariable=self.status, foreground="#555555", wraplength=410,
                  justify="left").grid(row=1, column=0, sticky="w", pady=(4, 0))

    # ---- drawing -------------------------------------------------------------------------------
    def add_row(self, page, r, item):
        ident, kind = item["id"], item["kind"]
        ttk.Label(page, text=item["label"], anchor="w").grid(row=r, column=0, sticky="w", pady=2)
        row = dict(item=item, enabled=False)
        if kind == "toggle":                 # the check box and its ON/OFF text share one cell
            cell = ttk.Frame(page)
            cell.grid(row=r, column=1, columnspan=2, sticky="w")
            var = tk.IntVar(value=0)
            widget = ttk.Checkbutton(cell, variable=var, command=lambda i=ident: self.on_toggle(i))
            widget.grid(row=0, column=0)
            lbl = ttk.Label(cell, text="", width=14)
            lbl.grid(row=0, column=1, sticky="w", padx=(2, 0))
        else:
            lbl = ttk.Label(page, text="", width=14)
            lbl.grid(row=r, column=2, sticky="w")
            if kind == "choice":
                var = tk.StringVar()
                widget = ttk.Combobox(page, textvariable=var, values=list(item["opts"].values()),
                                      state="disabled", width=15)
                widget.grid(row=r, column=1, sticky="w")
                widget.bind("<<ComboboxSelected>>", lambda e, i=ident: self.on_choose(i))
            else:
                var = tk.DoubleVar(value=item["lo"])
                widget = ttk.Scale(page, from_=item["lo"], to=item["hi"], variable=var, length=125,
                                   command=lambda v, i=ident: self.on_slide(i))
                widget.grid(row=r, column=1, sticky="w")
            widget.bind("<ButtonPress-1>", lambda e, i=ident: self.busy.add(i))
            widget.bind("<ButtonRelease-1>", lambda e, i=ident: self.on_slide_done(i))
            widget.bind("<KeyRelease>", lambda e, i=ident: self.on_slide_done(i))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, lambda e: "break")        # the wheel must never change a setting
        row.update(ui=kind, var=var, widget=widget, lbl=lbl)
        self.rows[ident] = row
        self.enable(row, False)

    @staticmethod
    def enable(row, on):
        w = row["widget"]
        if row["ui"] == "choice":
            w.configure(state="readonly" if on else "disabled")
        else:
            w.state(["!disabled"] if on else ["disabled"])
        row["enabled"] = on

    def set_widget(self, item, raw):
        row = self.rows[item["id"]]
        kind = item["kind"]
        if kind == "toggle":
            row["var"].set(1 if raw == item["opts"][1] else 0)
            row["lbl"].configure(text=fn_text(item, raw))
        elif kind == "choice":
            row["var"].set(fn_text(item, raw))
            row["lbl"].configure(text="")
        else:
            try:
                row["var"].set(max(item["lo"], min(item["hi"], int(raw))))
            except ValueError:
                pass
            row["lbl"].configure(text=fn_text(item, raw))

    def unavailable(self, item, text):
        row = self.rows[item["id"]]
        self.shown.pop(item["id"], None)
        self.enable(row, False)
        row["lbl"].configure(text=text)

    def set_status(self, text):
        if self.status.get() != text:
            self.status.set(text)
            self.panel.fit_window()                   # the text may need more or fewer lines

    # ---- the page that is showing ------------------------------------------------------------------
    def page_items(self):
        try:
            index = self.pnb.index("current")
        except tk.TclError:
            index = 0
        return [f for f in FUNCTIONS if f["page"] == index]

    def on_page(self):
        for f in self.page_items():
            self.asked.pop(f["prefix"], None)         # read this page now
            self.since.pop(f["prefix"], None)
        self.last_refresh = 0.0

    def can_talk(self):
        radio = self.panel.radio
        return radio.units == "raw" and radio.connected

    def refresh(self):
        """Called often by the panel while this tab is showing."""
        now = time.monotonic()
        if now - self.last_refresh < 0.25:
            return
        self.last_refresh = now
        radio = self.panel.radio
        talking = self.can_talk()
        if talking != self.was_talking:               # (re)connected, or lost: start again
            self.was_talking = talking
            self.asked.clear()
            self.since.clear()
            self.shown.clear()
            self.pending.clear()
        if radio.units != "raw":
            self.set_status("The Function tab needs the Raw CAT connection. You are using Hamlib, "
                            "which cannot reach these functions.")
        elif not radio.connected:
            self.set_status("Not connected to the radio.")
        else:
            self.set_status("A change is written to the radio at once and read back. A function "
                            "is greyed out when the radio does not answer it in its present mode.")
        items = self.page_items()
        if not talking:
            for f in FUNCTIONS:                       # every page, not only the one that is showing
                self.unavailable(f, "")
            return
        due = [f for f in items if now - self.asked.get(f["prefix"], -1e9) >= self.READ_EVERY]
        due = [f for f in due if f["id"] not in self.busy][:3]    # a few at a time: the line is shared
        if due:
            radio.read_functions([f["read"] for f in due])
            for f in due:
                self.asked[f["prefix"]] = now
                self.since.setdefault(f["prefix"], now)
        vals, times = radio.fn_snapshot()
        for ident, (payload, when) in list(self.pending.items()):
            item = FN[ident]
            if times.get(item["prefix"], 0.0) > when:
                got = vals.get(item["prefix"])
                if got != payload:
                    self.panel.append_log(f"Function {item['label']}: the radio did not accept "
                                          f"{fn_text(item, payload)}; it says "
                                          f"{fn_text(item, got) if got else '?'}")
                    self.shown.pop(ident, None)       # put the control back to what the radio kept
                del self.pending[ident]
            elif now - when > 4.0:
                self.panel.append_log(f"Function {item['label']}: no answer from the radio")
                self.shown.pop(ident, None)
                del self.pending[ident]
        for f in items:
            ident, prefix = f["id"], f["prefix"]
            raw = vals.get(prefix)
            fresh = raw is not None and now - times.get(prefix, 0.0) < self.STALE_AFTER
            if not fresh:
                waiting = raw is None and now - self.since.get(prefix, -1e9) < 2.0
                self.unavailable(f, "reading..." if waiting else "not in this mode")
                continue
            self.since.pop(prefix, None)
            if ident in self.busy or ident in self.pending:
                continue
            row = self.rows[ident]
            if self.shown.get(ident) == raw and row["enabled"]:
                continue
            self.shown[ident] = raw
            self.set_widget(f, raw)
            self.enable(row, True)

    # ---- writing -------------------------------------------------------------------------------------
    def restore(self, ident):
        self.shown.pop(ident, None)                   # draw what the radio says again
        self.busy.discard(ident)

    def commit(self, item, payload):
        p, ident = self.panel, item["id"]
        if not self.can_talk():
            messagebox.showinfo("Function", "The Function tab needs the Raw CAT connection to the "
                                            "radio.")
            return self.restore(ident)
        if item["rx"] and p.nr_cal.active:
            messagebox.showinfo("Function", "Not while the noise calibration is running: it would "
                                            "measure the wrong noise. Press Cancel on the Radio tab "
                                            "first.")
            return self.restore(ident)
        if self.shown.get(ident) == payload:
            return
        turning_on = item["kind"] != "toggle" or payload == item["opts"][1]
        if item["warn"] and turning_on and not messagebox.askokcancel(
                item["label"], item["warn"] + "\n\nChange it anyway?"):
            return self.restore(ident)
        p.radio.write_function(f"{item['prefix']}{payload};", item["read"])
        self.pending[ident] = (payload, time.monotonic())
        p.append_log(f"Function {item['label']} set to {fn_text(item, payload)}")
        if item["rx"] and p.nr is not None and p.nr.calibrated:
            p.append_log("The receiver changed, so the noise calibration may no longer match: "
                         "press Auto-calibrate again for the best result.")

    def on_toggle(self, ident):
        item = FN[ident]
        off, on = item["opts"]
        self.commit(item, on if self.rows[ident]["var"].get() else off)

    def on_choose(self, ident):
        item = FN[ident]
        label = self.rows[ident]["var"].get()
        key = next((k for k, v in item["opts"].items() if v == label), None)
        if key is None:
            return self.restore(ident)
        self.commit(item, key)

    @staticmethod
    def quantize(item, value):
        value = item["lo"] + round((float(value) - item["lo"]) / item["step"]) * item["step"]
        return int(max(item["lo"], min(item["hi"], value)))

    def on_slide(self, ident):
        item, row = FN[ident], self.rows[ident]
        row["lbl"].configure(text=item["fmt"](self.quantize(item, row["var"].get())))

    def on_slide_done(self, ident):
        self.busy.discard(ident)
        item = FN[ident]
        value = self.quantize(item, self.rows[ident]["var"].get())
        self.commit(item, f"{value:0{item['width']}d}")


class MeterBar(tk.Canvas):
    def __init__(self, master, width=430, height=7):
        super().__init__(master, width=width, height=height, highlightthickness=1,
                         highlightbackground="#888", background="#ececec")
        self.frac, self.peak, self.color = 0.0, None, BLUE
        self.bind("<Configure>", lambda e: self.redraw())

    def set(self, frac, color, peak=None):
        self.frac, self.color, self.peak = max(0.0, min(1.0, frac)), color, peak
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4:
            return
        self.create_rectangle(0, 0, w * self.frac, h, fill=self.color, width=0)
        for i in range(1, 10):
            x = w * i / 10
            self.create_line(x, h - 3, x, h, fill="#777")
        if self.peak is not None and self.peak > 0:
            x = min(w - 2, w * min(1.0, self.peak))
            self.create_line(x, 0, x, h, fill="#222", width=2)


# ---- noise reduction: spectral subtraction, for the audio you listen to ------------------------
try:
    import numpy as np
    HAVE_NUMPY = True
except Exception:                      # the panel works without numpy; only noise reduction is off
    np = None
    HAVE_NUMPY = False

NR_LEVELS = list(range(1, 21))


class SpectralNR:
    """Spectral-subtraction noise reduction (Boll 1979, with Berouti's over-subtraction and
    spectral floor) for mono 16-bit audio, in real time.

    The audio is cut into 20 ms windows that overlap by half (a sine window, so the pieces add up
    to exactly the original when nothing is subtracted). For each window the noise power that the
    method has learned is subtracted from the signal's power, bin by bin:

        clean power = max(power - alpha * noise power, beta * power)

    alpha is the over-subtraction (larger where the signal is close to the noise, because that is
    where the noise estimate is least trustworthy) and beta is the spectral floor that stops bins
    being cut to nothing, which is what makes "musical noise". The power is smoothed over 3 bins
    and over time before it is compared, and the gain is smoothed too, for the same reason.

    Where the noise comes from:
      * after calibrate(), from a noise profile measured on a quiet frequency; it then only
        follows slow changes (for example the receiver's AGC), and only from frames that look like
        noise;
      * before that, from a tracker that follows the minimum quickly and creeps upward slowly.

    level (1 to 20) sets how much is taken out: alpha, the floor, and the smoothing. The
    "conditions" found at calibration (how steady the noise is) make it more cautious when the
    noise is not steady, because subtracting a noise estimate that keeps changing is what produces
    artefacts.

    The window pipeline always runs, with the same delay (about 20 ms) whether noise reduction is
    on or off, so switching it never makes a click. process() takes and returns bytes of the same
    length."""

    MIN_BIAS = 2.1             # taking a minimum under-estimates the mean; this corrects it

    def __init__(self, rate=48000, level=8):
        self.rate = int(rate)
        self.hop = max(16, int(round(self.rate * 0.010)))            # 10 ms
        self.n = 2 * self.hop                                         # 20 ms window, 50 % overlap
        self.bins = self.n // 2 + 1
        self.win = np.sin(np.pi * (np.arange(self.n) + 0.5) / self.n)
        self.enabled = False       # the on/off switch
        self.muted = False         # while calibrating, the speakers hear nothing
        self.capture = None        # while calibrating: a list that gets every window's power
        self.calibrated = False
        self.noise = None
        self._ps = self._sub = None          # the minimum-statistics tracker
        self._hist = []
        self._count = 0
        self.cond = 1.0            # 0.5 (unsteady noise) to 1.0 (steady noise)
        self.alpha_k = np.ones(self.bins)
        self.info = {}
        self.g_prev = np.ones(self.bins)
        self.p_prev = None
        self._in = np.zeros(0)
        self._out = np.zeros(self.n)           # primed with silence: a fixed delay, never short
        self._ola = np.zeros(self.hop)
        self.set_level(level)

    # ---- the level ---------------------------------------------------------------------------
    def set_level(self, level):
        self.level = int(max(1, min(20, level)))
        t = (self.level - 1) / 19.0
        caution = 0.6 + 0.4 * self.cond                       # 1.0 for steady noise, less otherwise
        self.alpha0 = 1.5 + 3.5 * t * caution                 # over-subtraction at low SNR
        self.depth_db = (9.0 + 21.0 * t) * caution            # the deepest cut, in dB (about)
        self.beta = 2.0 * 10 ** (-self.depth_db / 10.0)       # spectral floor, a fraction of the noise power
        self.p_smooth = min(0.9, 0.70 + 0.10 * t + 0.10 * (1.0 - self.cond))   # smoothing of power over time
        self.g_att = 0.30 + 0.20 * (1.0 - self.cond)          # gain may rise quickly ...
        self.g_rel = 0.60                                     # ... and falls more slowly

    # ---- the audio ---------------------------------------------------------------------------
    def process(self, data):
        x = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        self._in = np.concatenate((self._in, x)) if len(self._in) else x
        made = []
        while len(self._in) >= self.n:
            made.append(self._frame(self._in[:self.n]))
            self._in = self._in[self.hop:]
        if made:
            self._out = np.concatenate([self._out] + made)
        k = len(x)
        y, self._out = self._out[:k], self._out[k:]
        if len(y) < k:                                        # cannot happen; never return short
            y = np.concatenate((y, np.zeros(k - len(y))))
        return np.clip(np.rint(y), -32768, 32767).astype(np.int16).tobytes()

    def _frame(self, frame):
        X = np.fft.rfft(frame * self.win)
        P = X.real * X.real + X.imag * X.imag
        if self.capture is not None:
            self.capture.append(P)
        Pn = self._track(P)
        if self.muted:
            gain = 0.0
        elif self.enabled:
            gain = self._gain(P, Pn)
        else:
            gain = 1.0
            self.g_prev[:] = 1.0                              # so that switching on fades in
            self.p_prev = None
        y = np.fft.irfft(X * gain, self.n) * self.win
        out = self._ola + y[:self.hop]
        self._ola = y[self.hop:]
        return out

    @staticmethod
    def _smooth(v):
        """1-2-1 smoothing across neighbouring bins."""
        s = np.empty_like(v)
        s[1:-1] = 0.25 * v[:-2] + 0.5 * v[1:-1] + 0.25 * v[2:]
        s[0] = 0.75 * v[0] + 0.25 * v[1]
        s[-1] = 0.75 * v[-1] + 0.25 * v[-2]
        return s

    def _track(self, P):
        """The noise power per bin."""
        if self.calibrated:
            # follow slow changes (the receiver's AGC, say), only from bins that look like noise
            # (from the smoothed power, as the gain is judged against: following the raw power would
            # slowly make the profile spiky, and a steady line would then be cut far too little)
            Pn, Psm = self.noise, self._smooth(P)
            quiet = Psm < 2.5 * Pn
            self.noise = np.where(quiet, 0.995 * Pn + 0.005 * Psm, Pn)
            return self.noise
        # No profile: minimum statistics (after Martin). Smooth the power, take its minimum over
        # about 1.5 s (ten pieces of 150 ms), and correct for the bias of taking a minimum.
        if self._ps is None:
            self._ps = P.copy()
            self._sub = P.copy()
        self._ps = 0.85 * self._ps + 0.15 * P
        self._sub = np.minimum(self._sub, self._ps)
        self._count += 1
        if self._count >= 15:
            self._hist.append(self._sub)
            if len(self._hist) > 10:
                self._hist.pop(0)
            self._sub = self._ps.copy()
            self._count = 0
        est = self._sub
        for h in self._hist:
            est = np.minimum(est, h)
        return est * self.MIN_BIAS

    def _gain(self, P, Pn):
        Ps = self._smooth(P)
        if self.p_prev is not None:
            Ps = self.p_smooth * self.p_prev + (1.0 - self.p_smooth) * Ps
        self.p_prev = Ps
        snr_db = 10.0 * np.log10(np.maximum(Ps, 1e-9) / np.maximum(Pn, 1e-9))
        a = 1.0 + (self.alpha0 - 1.0) * np.clip((20.0 - snr_db) / 25.0, 0.0, 1.0)
        a = a * self.alpha_k
        clean = np.maximum(Ps - a * Pn, self.beta * Pn)
        g = np.minimum(1.0, np.sqrt(clean / np.maximum(Ps, 1e-9)))
        g = np.where(g > self.g_prev, self.g_att * self.g_prev + (1.0 - self.g_att) * g,
                     self.g_rel * self.g_prev + (1.0 - self.g_rel) * g)
        g = self._smooth(g)
        self.g_prev = g
        return g

    # ---- calibration -----------------------------------------------------------------------
    def dbfs(self, mean_power):
        """Noise level, in dB relative to full scale, from a mean power per bin."""
        total = 2.0 * mean_power.sum() - mean_power[0] - mean_power[-1]
        sigma2 = total / self.n / (self.n / 2.0)
        return 10.0 * np.log10(max(sigma2, 1e-12) / 32768.0 ** 2)

    def build_profile(self, frames):
        """Learn the noise from windows of noise-only audio (a list of power spectra) and tune
        the subtraction to the level and to how steady the noise is. Returns what it found."""
        P = np.asarray(frames, dtype=np.float64)
        mean = P.mean(axis=0)
        total = P.sum(axis=1)
        # How much the total power varies from window to window, compared with what steady Gaussian
        # noise of this very spectrum would show (a narrow receiver passband has few independent
        # bins, so even steady noise varies a good deal). 1.0 means steady.
        expected = float(np.sqrt((mean ** 2).sum()) / max(mean.sum(), 1e-9))
        nonstat = float(total.std() / max(total.mean(), 1e-9) / max(expected, 1e-9))
        v_k = P.std(axis=0) / np.maximum(mean, 1e-9)                  # 1.0 for Gaussian noise
        v = float(np.median(v_k))
        q = max(len(total) // 4, 1)
        trend_db = float(10.0 * np.log10(max(total[-q:].mean(), 1e-9) / max(total[:q].mean(), 1e-9)))
        self.cond = float(np.clip(1.0 - 0.6 * max(0.0, v - 1.2) - 0.35 * max(0.0, nonstat - 1.4),
                                  0.5, 1.0))
        self.alpha_k = self._smooth(1.0 + 0.3 * np.clip(v_k - 1.1, 0.0, 1.5))
        self.noise = self._smooth(mean)
        self.calibrated = True
        self.g_prev[:] = 1.0
        self.p_prev = None
        self.set_level(self.level)
        self.info = dict(frames=len(frames), noise_dbfs=float(self.dbfs(mean)), nonstat=nonstat,
                         variability=v, trend_db=trend_db, cond=self.cond)
        return self.info

    def features(self, frames, lo_hz=300, hi_hz=2900):
        """How noise-like is a stretch of audio (a list of power spectra)? The scan for a quiet
        frequency uses this. Returns:
          peak_db   the highest narrow line above its surroundings (a carrier or a voice shows
                    here; steady noise stays under about 3 dB), and peak_hz, its pitch
          excess    that line height for every bin, to tell a line that stays at one pitch
                    on several frequencies (the receiver's own) from a signal
          nonstat   how much the total power moves, compared with steady noise of the same
                    spectrum (1.0 steady; speech and static crashes are several times that)
          dbfs      the level, in dB relative to full scale"""
        P = np.asarray(frames, dtype=np.float64)
        mean = P.mean(axis=0)
        lo = max(1, int(lo_hz * self.n / self.rate))
        hi = min(self.bins - 1, int(hi_hz * self.n / self.rate))
        seg = 10.0 * np.log10(np.maximum(self._smooth(mean)[lo:hi], 1e-9))
        k = 21                                                    # the local floor: the median over 21 bins
        pad = np.pad(seg, k // 2, mode="edge")
        floor = np.median(np.lib.stride_tricks.sliding_window_view(pad, k), axis=1)
        total = P[:, lo:hi].sum(axis=1)
        expected = np.sqrt((mean[lo:hi] ** 2).sum()) / max(mean[lo:hi].sum(), 1e-9)
        excess = seg - floor                     # how far each bin stands above its surroundings
        return dict(peak_db=float(excess.max()),
                    peak_hz=float((lo + int(excess.argmax())) * self.rate / self.n),
                    excess=excess, lo=lo,
                    nonstat=float(total.std() / max(total.mean(), 1e-9) / max(expected, 1e-9)),
                    dbfs=float(self.dbfs(mean)))

    def forget(self):
        self.calibrated = False
        self.noise = None
        self._ps = self._sub = None
        self._hist = []
        self._count = 0
        self.cond = 1.0
        self.alpha_k = np.ones(self.bins)
        self.info = {}
        self.set_level(self.level)


class NoiseCalibrator:
    """Auto-calibrate for the noise reduction.

    It retunes VFO A to a quiet frequency beside the one you are on, listens to the noise there,
    teaches the noise reduction what that noise sounds like (and tunes the subtraction to the
    level and to how steady the noise is), and retunes to where you were.

    The radio is away from your frequency while it runs, so:
      * the speakers are silent, and PTT and Tune are refused until the radio is back;
      * anything unexpected (the audio stops, the mode changes, something keys the radio) first
        puts the radio back on your frequency, and only then ends;
      * if somebody else changes the frequency, or the connection is lost, it ends without
        touching the radio, because putting it "back" would then be the wrong thing to do.

    Candidates are tried nearest first. A frequency is taken as noise only if the audio there has no
    narrow line (a carrier, a whistle, a voice) and its power is steady (no speech, no static
    crashes, no fading). With nothing found it uses the least bad one and says so."""

    SETTLE = 1.2           # seconds to wait after a retune before listening (network, buffers, AGC)
    LISTEN = 1.5           # seconds to listen at each candidate frequency
    FINAL = 4.0            # seconds of noise to measure for the profile
    TUNE_WAIT = 3.0        # seconds for the radio to reach a frequency
    OFFSETS_KHZ = {"ssb": (3.75, -3.75, 6.25, -6.25, 8.75, -8.75),
                   "am": (10, -10, 15, -15, 20, -20),
                   "fm": (25, -25, 50, -50, 75, -75)}
    MAX_PEAK_DB, MAX_NONSTAT, SILENT_DBFS = 6.0, 2.5, -85.0
    COMMON_MIN = 3         # a line at the same pitch on this many frequencies belongs to the receiver
    SSB_MIN_APART = 3500   # Hz: nearer than this, the signal you are on is inside the audio passband

    def khz(self, hz):
        """An offset from the starting frequency, in kHz, as +3.75 or -6.25 (whole numbers without a tail)."""
        return f"{(hz - self.orig) / 1000:+.2f}".rstrip("0").rstrip(".")

    def candidates(self, orig, fam):
        """The frequencies to listen on, nearest first. For SSB-type modes they are 1.25 kHz away
        from the busy whole and half kilohertz (stations sit on round frequencies), counted on the
        absolute grid and not from where you happen to be tuned, and never nearer than 3.5 kHz."""
        out = []
        for k in self.OFFSETS_KHZ[fam]:
            hz = orig + int(round(1000 * k))
            if fam == "ssb":
                hz = 500 * round((hz - 250) / 500) + 250          # x.25 or x.75 kHz
                while abs(hz - orig) < self.SSB_MIN_APART:
                    hz += 500 if hz >= orig else -500
            if 30000 <= hz <= 470000000 and hz != orig and hz not in out:
                out.append(hz)
        return out

    def __init__(self, panel):
        self.p = panel
        self.active = False
        self.state = None

    @staticmethod
    def family(mode):
        if mode in ("AM", "AMN"):
            return "am"
        if mode in ("FM", "FMN", "FM-D"):
            return "fm"
        if mode == "C4FM":
            return None
        return "ssb"                       # SSB, CW, RTTY and the data modes

    def at(self, snap, hz):
        return snap.get("freq") is not None and abs(snap["freq"] - hz) < 5

    # ---- start and end ----------------------------------------------------------------------
    def start(self):
        p = self.p
        if self.active:
            return self.abort("cancelled by you")
        if not HAVE_NUMPY:
            return messagebox.showinfo("Noise reduction", "Noise reduction needs numpy: pip install numpy")
        if p.nr is None or not p.audio_running():
            return messagebox.showinfo("Noise reduction", "Start the audio first: the calibration listens "
                                                          "to the radio's audio.")
        if not p.radio.connected:
            return messagebox.showinfo("Noise reduction", "Connect to the radio first: the calibration "
                                                          "retunes it.")
        s = p.radio.snapshot()
        if s.get("freq") is None or s.get("mode") is None:
            return messagebox.showinfo("Noise reduction", "The radio's frequency and mode are not known yet.")
        if s.get("ptt") or p.ptt_down or s.get("tuning") or p.tune_deadline:
            return messagebox.showinfo("Noise reduction", "Not while the radio is transmitting or tuning.")
        fam = self.family(s["mode"])
        if fam is None:
            return messagebox.showinfo("Noise reduction", "C4FM is a digital mode, so noise reduction does "
                                                          "not apply.")
        self.orig, self.mode = s["freq"], s["mode"]
        self.cands = self.candidates(self.orig, fam)
        if not self.cands:
            return messagebox.showinfo("Noise reduction", "No frequency to listen on beside this one.")
        self.i, self.results, self.forced, self.uncertain = 0, [], False, False
        self.common = None                      # (pitch in Hz, on how many frequencies) of a receiver line
        self.wrong_since = None
        self.active = True
        p.nr.muted, p.nr.capture = True, None
        p.nr_btn.configure(text="Cancel")
        p.append_log(f"Noise calibration: looking for a quiet frequency beside "
                     f"{self.orig / 1e6:.6f} MHz (the speakers are silent, PTT and Tune are off until "
                     "it is done)")
        self.tune(self.cands[0], "settle")

    def tune(self, hz, then):
        self.target, self.state, self.after, self.t0 = hz, "tuning", then, time.monotonic()
        self.p.radio.set_freq("A", int(hz))
        self.say(f"Calibrating: tuning to {hz / 1e6:.3f} MHz ...")

    def say(self, text):
        self.p.nr_status.set(text)

    def restore(self, message):
        """Put the radio back on the frequency you were on, then finish."""
        self.message, self.state, self.t0, self.retried = message, "restoring", time.monotonic(), False
        self.p.nr.capture = None if self.p.nr else None
        self.p.radio.set_freq("A", int(self.orig))
        self.say("Calibrating: returning to your frequency ...")

    def done(self, message):
        p = self.p
        if p.nr is not None:
            p.nr.muted, p.nr.capture = False, None
        self.active, self.state = False, None
        p.nr_btn.configure(text="Auto-calibrate")
        p.nr_status.set(message)
        p.append_log("Noise calibration: " + message)

    def abort(self, reason, restore=True):
        if not self.active or self.state == "restoring":
            return
        radio = self.p.radio
        if restore and radio.connected and not self.at(radio.snapshot(), self.orig):
            self.restore(f"stopped ({reason}); the radio is back on {self.orig / 1e6:.6f} MHz")
        else:
            self.done(f"stopped ({reason})")

    # ---- the steps, called every 100 ms by the panel's tick ----------------------------------
    def step(self):
        if not self.active:
            return
        p, now = self.p, time.monotonic()
        s = p.radio.snapshot()
        if not p.radio.connected:
            return self.done("the connection to the radio was lost; the radio was left as it is")
        if self.state == "restoring":
            return self.step_restoring(s, now)
        if p.nr is None or not p.audio_running():
            return self.abort("the audio stopped")
        if s.get("ptt") or p.ptt_down:
            return self.abort("the radio is transmitting")
        if s.get("mode") != self.mode:
            return self.abort("the mode was changed")
        if self.state != "tuning" and not self.at(s, self.target):
            self.wrong_since = self.wrong_since or now
            if now - self.wrong_since > 1.5:
                return self.done("the frequency was changed by something else; the radio was left as it is")
        else:
            self.wrong_since = None
        nr = p.nr
        if self.state == "tuning":
            if self.at(s, self.target):
                self.state, self.t0 = "settle", now
            elif now - self.t0 > self.TUNE_WAIT:
                self.abort("the radio did not tune")
        elif self.state == "settle":
            if now - self.t0 >= self.SETTLE:
                nr.capture, self.s_samples, self.cap_start = [], [], now
                self.state = "final" if self.forced else "listen"
        elif self.state in ("listen", "final"):
            if s.get("s_raw") is not None:
                self.s_samples.append(s["s_raw"])
            self.say(f"Calibrating: listening at {self.khz(self.target)} kHz ...")
            if self.state == "listen" and now - self.cap_start >= self.LISTEN:
                self.evaluate()
            elif self.state == "final" and now - self.cap_start >= self.FINAL:
                self.finish_profile()

    def evaluate(self):
        p, nr = self.p, self.p.nr
        frames = list(nr.capture)
        feat = (nr.features(frames) if len(frames) >= 60 else
                dict(peak_db=99.0, peak_hz=0.0, excess=np.full(8, 99.0), lo=0, nonstat=99.0, dbfs=-120.0))
        silent = feat["dbfs"] < self.SILENT_DBFS
        ok = (not silent and feat["peak_db"] < self.MAX_PEAK_DB and feat["nonstat"] < self.MAX_NONSTAT)
        s_avg = (sum(self.s_samples) / len(self.s_samples)) if self.s_samples else None
        self.results.append(dict(hz=self.target, ok=ok, silent=silent, s=s_avg, **feat))
        what = ("no sound (is the squelch closed?)" if silent else "noise only" if ok else
                "not clear: " + ("a carrier or a tone" if feat["peak_db"] >= self.MAX_PEAK_DB
                                else "unsteady, speech or static"))
        p.append_log(f"Noise calibration: {self.khz(self.target)} kHz: {what} "
                     f"(line {feat['peak_db']:.0f} dB at {feat['peak_hz']:.0f} Hz, "
                     f"steadiness {feat['nonstat']:.1f}, "
                     f"level {feat['dbfs']:.0f} dBFS"
                     + (f", S-meter {s_avg:.0f}" if s_avg is not None else "") + ")")
        if ok:                                     # keep listening here: the same audio becomes the profile
            self.state = "final"
            return
        nr.capture = []
        if self.try_common():
            return
        self.i += 1
        if self.i < len(self.cands):
            return self.tune(self.cands[self.i], "settle")
        usable = [r for r in self.results if not r["silent"]]
        if not usable:
            return self.abort("there was no sound on any of the frequencies tried; open the squelch")
        best = min(usable, key=lambda r: r["peak_db"] + 2.0 * max(0.0, r["nonstat"] - self.MAX_NONSTAT))
        self.uncertain, self.forced = True, True
        p.append_log(f"Noise calibration: no clear frequency; using {self.khz(best['hz'])} kHz")
        self.tune(best["hz"], "settle")

    def try_common(self):
        """Is the line that spoils the frequencies the receiver's own? A signal changes pitch when the
        radio is retuned, so a line at the SAME pitch on three or more different frequencies cannot be
        one: it comes from the receiver or the audio path (a spur, a tone, hum). It is then treated as
        part of the noise: left out when judging a frequency, and left in the profile, where the noise
        reduction takes it out like the rest. Returns True when it has chosen a frequency."""
        p, nr = self.p, self.p.nr
        usable = [r for r in self.results if not r["silent"]]
        if len(usable) < self.COMMON_MIN:
            return False
        stack = np.stack([r["excess"] for r in usable])
        count = (stack >= self.MAX_PEAK_DB).sum(axis=0)
        hit = count >= self.COMMON_MIN
        if not hit.any():
            return False
        mask = np.convolve(hit.astype(int), np.ones(7, dtype=int), "same") > 0      # a few bins either side
        for r in usable:
            rest = r["excess"][~mask]
            r["peak_rest"] = float(rest.max()) if rest.size else 0.0
        clear = [r for r in usable if r["peak_rest"] < self.MAX_PEAK_DB and r["nonstat"] < self.MAX_NONSTAT]
        if not clear:
            return False
        best = clear[0]                                           # the nearest: they were tried in order
        strongest = int(np.argmax(np.where(mask, stack.mean(axis=0), -1e9)))
        pitch = (usable[0]["lo"] + strongest) * nr.rate / nr.n
        self.common, self.forced = (pitch, int(count[strongest])), True
        p.append_log(f"Noise calibration: the same steady line, near {pitch:.0f} Hz, is on "
                     f"{int(count[strongest])} different frequencies, so it comes from the receiver and "
                     f"not from a signal. It is treated as noise. Using {self.khz(best['hz'])} kHz")
        self.tune(best["hz"], "settle")
        return True

    def finish_profile(self):
        p, nr = self.p, self.p.nr
        frames = list(nr.capture)
        nr.capture = None
        if len(frames) < 150:
            return self.abort("too little audio arrived to measure the noise")
        info = nr.build_profile(frames)
        nr.enabled = True
        p.nr_on.set(True)
        steady = ("steady" if info["cond"] >= 0.9 else "fairly steady" if info["cond"] >= 0.7 else "unsteady")
        text = (f"calibrated at {self.khz(self.target)} kHz: noise {info['noise_dbfs']:.0f} dBFS, "
                f"{steady}"
                + (", level still changing" if abs(info["trend_db"]) > 3 else "")
                + (f", with a steady line near {self.common[0]:.0f} Hz that comes from the receiver, "
                   "so it is reduced too" if self.common else "")
                + (", NO CLEAR FREQUENCY, so it may include a signal" if self.uncertain else "")
                + f". Noise reduction is on, level {nr.level}.")
        p.append_log(f"Noise calibration: profile from {info['frames']} windows; steadiness "
                     f"{info['nonstat']:.2f}, variability {info['variability']:.2f}, trend "
                     f"{info['trend_db']:+.1f} dB, cautiousness {info['cond']:.2f}")
        self.restore(text)

    def step_restoring(self, s, now):
        if self.at(s, self.orig):
            return self.done(self.message)
        if now - self.t0 > 4.0 and not self.retried:
            self.retried = True
            self.p.radio.set_freq("A", int(self.orig))
        elif now - self.t0 > 9.0:
            self.done(f"WARNING: could not confirm that the radio is back on {self.orig / 1e6:.6f} MHz; "
                      "check its frequency")


class AudioPath:
    """One local audio path: capture from one device and play on another, with a gain and peak
    meters. A receive path and a transmit path make up the local router that the Audio tab uses
    for a local radio, and for the virtual-cable end of a remote radio.

    Audio is passed as a stream of bytes, so it does not matter if the two devices call back
    with different block sizes. Device warnings and errors are logged, never swallowed."""
    PRIME, MAXQ = 3, 12                 # frames: start playing after 3 are queued, drop beyond 12

    def __init__(self, name, in_dev, out_dev, rate, frame_ms, stereo, log):
        # in_dev None: nothing is captured; audio is handed over with feed() instead
        self.name, self.in_dev, self.out_dev, self.log = name, in_dev, out_dev, log
        self.rate, self.bs = rate, rate * frame_ms // 1000
        self.out_ch = 2 if stereo else 1
        self.sink = None                # a function that takes each captured frame (no output device)
        self.nr = None                  # noise reduction, for the receive path to the speakers
        self.buf, self.lock, self.primed = bytearray(), threading.Lock(), False
        self.gain, self.peak, self.peak_in = 1.0, 0, 0     # peak_in: before the gain, peak: after
        self.underruns = self.dropped = self.in_blocks = self.out_blocks = 0
        self.in_stream = self.out_stream = None
        self.warned = set()

    def set_gain_db(self, db):
        self.gain = 10 ** (db / 20)

    def _warn(self, key, msg):
        if key not in self.warned:
            self.warned.add(key)
            self.log(f"{self.name} path: {msg}")

    def start(self):
        kw = dict(samplerate=self.rate, channels=1, dtype="int16", blocksize=self.bs)
        try:
            if self.in_dev is not None:
                self.in_stream = _logged_open(
                    self.log, f"opening the {self.name} path input", self.in_dev,
                    sd.RawInputStream, device=self.in_dev, callback=self._in_cb, **kw)
            okw = dict(kw)
            while self.out_dev is not None:
                okw["channels"] = self.out_ch
                try:
                    self.out_stream = _logged_open(
                        self.log, f"opening the {self.name} path output", self.out_dev,
                        sd.RawOutputStream, device=self.out_dev, callback=self._out_cb, **okw)
                    break
                except Exception:
                    if self.out_ch == 2:        # the device may be mono-only
                        self.log(f"{self.name} path: stereo is not supported by the output "
                                 "device; using mono")
                        self.out_ch = 1
                    else:
                        raise
            if self.in_stream is not None:
                _logged_open(self.log, f"starting the {self.name} path input", self.in_dev,
                             self.in_stream.start)
            if self.out_stream is not None:
                _logged_open(self.log, f"starting the {self.name} path output", self.out_dev,
                             self.out_stream.start)
        except Exception:
            self.stop()
            raise

    def stop(self):
        for st in (self.in_stream, self.out_stream):
            if st is not None:
                try:
                    st.stop()
                    st.close()
                except Exception:
                    pass
        self.in_stream = self.out_stream = None
        with self.lock:
            self.buf.clear()

    def _in_cb(self, indata, frames, t, status):
        try:
            if status:
                self._warn("in_status", f"the input device reported: {status}")
            self.in_blocks += 1
            self.feed(bytes(indata))
        except Exception as exc:
            self._warn("in_err", f"input error: {exc!r}")

    def feed(self, data):
        """Queue mono int16 audio for playback (the device callback or the engine calls this)."""
        try:
            p = _peak(data)
            if p > self.peak_in:
                self.peak_in = p
            if self.nr is not None:                # noise reduction, before the volume
                data = self.nr.process(data)
            if self.gain != 1.0:
                data = _apply_gain(data, self.gain)
                p = _peak(data)
            if p > self.peak:
                self.peak = p
            if self.sink is not None:            # capture-only path: hand the frame straight on
                self.sink(data)
                return
            with self.lock:
                self.buf += data
                limit = self.MAXQ * self.bs * 2
                if len(self.buf) > limit:        # the devices drift: drop the oldest audio
                    drop = len(self.buf) - self.PRIME * self.bs * 2
                    del self.buf[:drop]
                    self.dropped += drop // 2 // self.bs or 1
        except Exception as exc:
            self._warn("feed_err", f"audio error: {exc!r}")

    def _out_cb(self, outdata, frames, t, status):
        try:
            if status:
                self._warn("out_status", f"the output device reported: {status}")
            self.out_blocks += 1
            need = frames * 2                    # mono bytes wanted
            data = None
            with self.lock:
                if not self.primed and len(self.buf) >= self.PRIME * self.bs * 2:
                    self.primed = True
                if self.primed:
                    if len(self.buf) >= need:
                        data = bytes(self.buf[:need])
                        del self.buf[:need]
                    else:                        # ran dry: wait until a little is queued again
                        self.primed = False
                        self.underruns += 1
            if data is None:
                outdata[:] = bytes(frames * self.out_ch * 2)
            else:
                outdata[:] = _dup_stereo(data) if self.out_ch == 2 else data
        except Exception as exc:
            self._warn("out_err", f"output error: {exc!r}")
            try:
                outdata[:] = bytes(len(outdata))
            except Exception:
                pass


class FreqDisplay(tk.Frame):
    """Nine-digit frequency readout (100 MHz down to 1 Hz).

    Click a digit to select it (it turns amber), then use the mouse wheel to change that digit.
    With the readout focused, Up/Down change the selected digit and Left/Right move the selection
    along the digits. on_change(hz) is called with the new frequency and returns True to accept it."""
    NDIG = 9
    SEL_BG = "#ffd966"

    def __init__(self, master, bg, on_change, font=("Helvetica", 26, "bold")):
        super().__init__(master, bg=bg, takefocus=True, highlightthickness=0)
        self.bg, self.on_change = bg, on_change
        self.value, self.sel, self.enabled = None, None, True
        self.digits, self.seps = [], []
        col = 0
        for i in range(self.NDIG):
            lb = tk.Label(self, text="-", font=font, bg=bg, padx=0, pady=0, width=1)
            lb.grid(row=0, column=col)
            lb.bind("<Button-1>", lambda e, i=i: self.select(i))
            self.digits.append(lb)
            col += 1
            if i in (2, 5):
                sp = tk.Label(self, text=".", font=font, bg=bg, padx=0, pady=0)
                sp.grid(row=0, column=col)
                self.seps.append(sp)
                col += 1
        for w in [self] + self.digits + self.seps:
            w.bind("<MouseWheel>", self._wheel)             # Windows and macOS
            w.bind("<Button-4>", self._wheel)               # Linux
            w.bind("<Button-5>", self._wheel)
        self.bind("<Up>", lambda e: self._step(+1))
        self.bind("<Down>", lambda e: self._step(-1))
        self.bind("<Left>", lambda e: self._move(-1))
        self.bind("<Right>", lambda e: self._move(+1))

    def set_value(self, hz):
        self.value = hz
        text = "-" * self.NDIG if hz is None else f"{hz:09d}"
        for i, lb in enumerate(self.digits):
            ch = text[i]
            if hz is not None and i < 2 and set(text[:i + 1]) == {"0"}:
                ch = " "                                    # no leading zeros before the MHz digit
            lb.configure(text=ch)

    def set_bg(self, bg):
        self.bg = bg
        self.configure(bg=bg)
        for w in self.seps:
            w.configure(bg=bg)
        self._paint()

    def select(self, pos):
        self.sel = pos
        self._paint()
        self.focus_set()

    def _paint(self):
        for i, lb in enumerate(self.digits):
            lb.configure(bg=self.SEL_BG if i == self.sel else self.bg)

    def _move(self, d):
        self.select(5 if self.sel is None else max(0, min(self.NDIG - 1, self.sel + d)))

    def _step(self, sign):
        if self.value is None or self.sel is None or not self.enabled:
            return
        new = self.value + sign * 10 ** (self.NDIG - 1 - self.sel)
        new = max(FREQ_MIN, min(FREQ_MAX, new))
        if new != self.value and self.on_change(new):
            self.set_value(new)

    def _wheel(self, event):
        if self.sel is None:                                # nothing selected yet: use the digit
            hovered = event.widget in self.digits           # under the pointer, else 1 kHz
            self.select(self.digits.index(event.widget) if hovered else 5)
        up = event.num == 4 or getattr(event, "delta", 0) > 0
        self._step(+1 if up else -1)


class Panel:
    def __init__(self, root, args):
        self.root = root
        root.title(f"{APP_NAME} {__version__}")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.cfg = load_cfg()
        self.logq = queue.SimpleQueue()
        self.backend = args.backend or self.cfg.get("backend", "hamlib")
        if self.backend not in ("hamlib", "cat"):
            self.backend = "hamlib"
        # correction points are kept separately: Hamlib's watts and the raw CAT scale differ
        self.cal_sets = {
            "hamlib": {"po": [tuple(p) for p in self.cfg.get("cal_po", [])],
                       "swr": [tuple(p) for p in self.cfg.get("cal_swr", [])]},
            "cat": {"po": [tuple(p) for p in self.cfg.get("cal_po_raw", [])],
                    "swr": [tuple(p) for p in self.cfg.get("cal_swr_raw", [])]}}
        self.ports = {"hamlib": str(self.cfg.get("port_hamlib", self.cfg.get("port", 4532))),
                      "cat": str(self.cfg.get("port_cat", ""))}
        if args.port:
            self.ports[self.backend] = str(args.port)
        self.radio = self.make_radio()
        self.cal = self.cal_sets[self.backend]
        self.host = tk.StringVar(value=args.host or self.cfg.get("host", ""))
        self.port = tk.StringVar(value=self.ports[self.backend])
        self.backend_var = tk.StringVar(
            value=next(k for k, v in BACKENDS.items() if v == self.backend))
        self.freq_hold = {}           # VFO -> (Hz you just set, show it until this time)
        self._pending_freq, self._freq_job = {}, {}
        self._bw_family, self._bw_steps, self._bw_updating = None, [], False
        self.bw_hold_until, self._bw_job, self._bw_target = 0.0, None, None
        self.agc_hold_until = 0.0
        self.ptt_down, self._ptt_job, self._ptt_auto_mic = False, None, False
        self.s9_calibrated = isinstance(self.cfg.get("s9_raw"), int)
        self.s9_raw = self.cfg["s9_raw"] if self.s9_calibrated else S9_DEFAULT_RAW
        self.show_cal = bool(self.cfg.get("show_cal", False))
        self.log_hidden = bool(self.cfg.get("log_hidden", False))
        self._tick_errors = set()                # errors in the periodic update already reported
        self.band_var = tk.StringVar(value="Band")
        self.band_hold = None                    # (band, until): wait for the radio to follow a pick
        self.band_mem = {}                       # band -> [frequency, mode] last used on it
        saved = self.cfg.get("band_memory")
        for name, v in (saved.items() if isinstance(saved, dict) else ()):
            try:
                if name in BAND_BY_NAME and band_of(int(v[0])) == name and str(v[1]) in LABEL_OF:
                    self.band_mem[name] = [int(v[0]), str(v[1])]
            except (TypeError, ValueError, IndexError, KeyError):
                pass                             # a damaged entry is just forgotten
        self._vfo_shown = None
        self.status = tk.StringVar(value="Not connected")
        self.mode_var = tk.StringVar()
        self.power_var = tk.StringVar()
        self.po_peak, self.po_peak_t = 0.0, 0.0
        self._atu_led_color = None
        self.band_hf = True
        self.pending_power = (None, 0.0)
        self.pending_mode = (None, 0.0)
        self.tune_deadline = 0.0
        self.build()
        self.apply_geometry()
        self.tick()
        if args.host:
            self.toggle_connect()

    # ---- layout -------------------------------------------------------------------
    def build(self):
        """A narrow layout (about 450 px wide): short rows, small digits, compact meters."""
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(0, weight=1)
        # Mic level and Volume are software gains on this computer, shown on both tabs
        self.a_rx_pct = int(self.cfg.get("audio_rx_pct", 100))
        self.a_tx_pct = int(self.cfg.get("audio_tx_pct", 100))
        self.rx_var, self.tx_var = tk.IntVar(value=self.a_rx_pct), tk.IntVar(value=self.a_tx_pct)
        self.a_rx_lbl = tk.StringVar(value=f"{self.a_rx_pct}%")
        self.a_tx_lbl = tk.StringVar(value=f"{self.a_tx_pct}%")
        outer = ttk.Frame(r, padding=6)
        outer.grid(sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)             # the log takes whatever room is left
        self.nb = ttk.Notebook(outer)               # tabs: Radio, Audio, Function, Menu
        self.nb.grid(row=0, column=0, sticky="ew")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.fit_window(shrink=True))
        main = ttk.Frame(self.nb, padding=6)
        main.columnconfigure(0, weight=1)
        self.nb.add(main, text="Radio")
        audio_tab = ttk.Frame(self.nb, padding=6)
        audio_tab.columnconfigure(1, weight=1)
        self.nb.add(audio_tab, text="Audio")
        self.build_audio(audio_tab)

        # ---- connection: two short rows
        f = ttk.Frame(main)
        f.grid(row=0, column=0, sticky="ew")
        ttk.Label(f, text="Host:").grid(row=0, column=0)
        self.host_e = ttk.Entry(f, textvariable=self.host, width=15)
        self.host_e.grid(row=0, column=1, padx=3)
        ttk.Label(f, text="Port:").grid(row=0, column=2)
        self.port_e = ttk.Entry(f, textvariable=self.port, width=6)
        self.port_e.grid(row=0, column=3, padx=3)
        self.conn_btn = ttk.Button(f, text="Connect", width=10, command=self.toggle_connect)
        self.conn_btn.grid(row=0, column=4, padx=3)
        self.backend_cb = ttk.Combobox(f, textvariable=self.backend_var, values=list(BACKENDS),
                                       width=18, state="readonly")
        self.backend_cb.grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))
        self.backend_cb.bind("<<ComboboxSelected>>", self.on_backend)
        self.status_lbl = ttk.Label(f, textvariable=self.status,
                                    font=("TkDefaultFont", 10, "bold"))
        self.status_lbl.grid(row=1, column=3, columnspan=2, sticky="w", padx=6, pady=(3, 0))

        # ---- VFO A and B, RX/TX state, and the typed frequency entry
        f = ttk.Frame(main)
        f.grid(row=1, column=0, sticky="ew", pady=(8, 2))
        f.columnconfigure(2, weight=1)
        self.vfo_frames = {}
        self.vfo_normal_bg = self.root.cget("bg")
        for col, which in enumerate(("A", "B")):
            fr = tk.Frame(f, bg=self.vfo_normal_bg, padx=6, pady=2,
                          cursor="hand2" if which == "B" else "")
            fr.grid(row=0, column=col, padx=(0, 6), sticky="w")
            cap = tk.Label(fr, text="VFO " + which, font=("Helvetica", 9),
                           bg=self.vfo_normal_bg, fg="#555555")
            cap.grid(row=0, column=0, sticky="w")
            val = FreqDisplay(fr, self.vfo_normal_bg,
                              lambda hz, v=which: self.on_freq_change(v, hz), font=VFO_FONT)
            val.grid(row=1, column=0, sticky="w")
            for w in (fr, cap):                    # the area around the digits: click B to swap
                w.bind("<Button-1>", lambda e, v=which: self.on_vfo_click(v))
            self.vfo_frames[which] = (fr, cap, val)
        self.state_lbl = tk.Label(f, text="--", width=6, font=("Helvetica", 13, "bold"),
                                  fg="white", bg=GREY)
        self.state_lbl.grid(row=0, column=2, padx=4, sticky="e")
        ef = ttk.Frame(f)
        ef.grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))
        self.band_cb = ttk.Combobox(ef, textvariable=self.band_var, width=5, state="readonly",
                                    values=[b[0] for b in BANDS])
        self.band_cb.grid(row=0, column=0, padx=(0, 6))
        self.band_cb.bind("<<ComboboxSelected>>", self.on_band)
        ttk.Label(ef, text="Go to:").grid(row=0, column=1)
        self.entry_var = tk.StringVar()
        self.freq_entry = ttk.Entry(ef, textvariable=self.entry_var, width=11)
        self.freq_entry.grid(row=0, column=2, padx=3)
        self.freq_entry.bind("<Return>", lambda e: self.on_freq_entry("A"))
        ttk.Button(ef, text="Set A", width=5, command=lambda: self.on_freq_entry("A")).grid(
            row=0, column=3, padx=1)
        ttk.Button(ef, text="Set B", width=5, command=lambda: self.on_freq_entry("B")).grid(
            row=0, column=4, padx=1)
        ttk.Label(ef, text="Type a frequency: 14.074  7074k  145.5m", foreground="#666666").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(1, 0))

        # ---- push to talk, just above the log and so on every tab: click once to transmit,
        #      click again to stop (the space bar does too)
        self.ptt_lbl = tk.Label(outer, text=PTT_IDLE_TEXT, relief="raised", bd=3,
                                padx=6, pady=7, font=("Helvetica", 13, "bold"), fg="#999999",
                                bg=self.vfo_normal_bg)
        self.ptt_lbl.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self.ptt_lbl.bind("<ButtonPress-1>", self.ptt_toggle)
        self.bind_space_ptt()
        r.bind("<FocusOut>", self.on_focus_out)

        # ---- controls: mode, C4FM, power on the first row; ATU, tune, split, radio power below
        f = ttk.Frame(main)
        f.grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Label(f, text="Mode:").grid(row=0, column=0)
        self.mode_cb = ttk.Combobox(f, textvariable=self.mode_var, width=8, state="readonly",
                                    values=[label for label, _ in MODES])
        self.mode_cb.grid(row=0, column=1, padx=3)
        self.mode_cb.bind("<<ComboboxSelected>>", self.on_mode)
        self.c4fm_btn = ttk.Button(f, text="C4FM: --", width=9, command=self.on_c4fm)
        self.c4fm_btn.grid(row=0, column=2, padx=(0, 8))
        ttk.Label(f, text="Power:").grid(row=0, column=3)
        self.power_cb = ttk.Combobox(f, textvariable=self.power_var, width=6, state="readonly",
                                     values=[f"{p} W" for p in POWERS_HF])
        self.power_cb.grid(row=0, column=4, padx=3)
        self.power_cb.bind("<<ComboboxSelected>>", self.on_power)
        g = ttk.Frame(f)
        g.grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))
        # ttk buttons are at least 11 characters wide unless told otherwise; width=0 means "as wide
        # as the label"
        ttk.Style().configure("Compact.TButton", padding=(5, 1), width=0)
        # a light for the antenna tuner: green when it is off, yellow when it is in line
        self.atu_led = tk.Canvas(g, width=16, height=16, highlightthickness=0)
        self.atu_led.grid(row=0, column=0, padx=(0, 3))
        self.atu_dot = self.atu_led.create_oval(2, 2, 14, 14, fill=GREY, outline="#555")
        self.tuner_btn = ttk.Button(g, text="ATU", style="Compact.TButton", command=self.on_tuner)
        self.tuner_btn.grid(row=0, column=1, padx=(0, 3))
        self.tune_btn = ttk.Button(g, text="Tune", style="Compact.TButton", command=self.on_tune)
        self.tune_btn.grid(row=0, column=2, padx=2)
        self.split_btn = ttk.Button(g, text="Split: --", style="Compact.TButton",
                                    command=self.on_split)
        self.split_btn.grid(row=0, column=3, padx=2)
        self.pon_btn = ttk.Button(g, text="Power on", style="Compact.TButton",
                                  command=lambda: self.on_power_switch(True))
        self.pon_btn.grid(row=0, column=4, padx=(8, 2))
        self.poff_btn = ttk.Button(g, text="Power off", style="Compact.TButton",
                                   command=lambda: self.on_power_switch(False))
        self.poff_btn.grid(row=0, column=5, padx=2)

        # ---- bandwidth
        bf = ttk.Frame(main)
        bf.grid(row=3, column=0, sticky="ew", pady=(0, 2))
        bf.columnconfigure(1, weight=1)
        ttk.Label(bf, text="Width:").grid(row=0, column=0, padx=(0, 6))
        self.bw_scale = ttk.Scale(bf, from_=0, to=20, orient="horizontal",
                                  command=self.on_bw_slide)
        self.bw_scale.grid(row=0, column=1, sticky="ew")
        self.bw_label = tk.StringVar(value="--")
        ttk.Label(bf, textvariable=self.bw_label, width=10).grid(row=0, column=2, padx=4)
        self.bw_scale.state(["disabled"])
        ttk.Label(bf, text="AGC:").grid(row=0, column=3, padx=(4, 0))
        self.agc_var = tk.StringVar()
        self.agc_cb = ttk.Combobox(bf, textvariable=self.agc_var, width=5, state="disabled",
                                   values=list(CatRadio.AGC_SET))
        self.agc_cb.grid(row=0, column=4, padx=(3, 0))
        self.agc_cb.bind("<<ComboboxSelected>>", self.on_agc)
        ttk.Label(bf, text="Mic level:").grid(row=1, column=0, padx=(0, 6), sticky="w")
        self.r_tx_scale = ttk.Scale(bf, from_=0, to=200, orient="horizontal",
                                    variable=self.tx_var, command=self.on_audio_tx_slide)
        self.r_tx_scale.grid(row=1, column=1, sticky="ew")
        ttk.Label(bf, textvariable=self.a_tx_lbl, width=6).grid(row=1, column=2, padx=6, sticky="w")
        ttk.Label(bf, text="Volume:").grid(row=2, column=0, padx=(0, 6), sticky="w")
        self.r_rx_scale = ttk.Scale(bf, from_=0, to=200, orient="horizontal",
                                    variable=self.rx_var, command=self.on_audio_rx_slide)
        self.r_rx_scale.grid(row=2, column=1, sticky="ew")
        ttk.Label(bf, textvariable=self.a_rx_lbl, width=6).grid(row=2, column=2, padx=6, sticky="w")
        if not AUDIO_OK:                    # no sound library: the level sliders have nothing to do
            self.r_tx_scale.state(["disabled"])
            self.r_rx_scale.state(["disabled"])
            self.a_tx_lbl.set("no audio")
            self.a_rx_lbl.set("no audio")

        # ---- noise reduction: below the ATU and the sliders, above the meters
        self.build_nr(main, row=4)

        # ---- meters
        m = ttk.LabelFrame(main, text="Meters", padding=(6, 3))
        m.grid(row=5, column=0, sticky="ew", pady=4)
        m.columnconfigure(1, weight=1)
        self.bars, self.texts = {}, {}
        for i, (key, label) in enumerate((("s", "S meter"), ("po", "Power"),
                                          ("swr", "SWR"), ("alc", "ALC"))):
            ttk.Label(m, text=label + ":").grid(row=i, column=0, sticky="e", pady=0)
            bar = MeterBar(m, width=170)
            bar.grid(row=i, column=1, sticky="ew", padx=6)
            txt = tk.StringVar(value="--")
            ttk.Label(m, textvariable=txt, width=17).grid(row=i, column=2, sticky="w")
            self.bars[key], self.texts[key] = bar, txt
        # the same two level meters as on the Audio tab: audio from the radio, and your microphone
        ttk.Label(m, text="Audio in:").grid(row=4, column=0, sticky="e", pady=0)
        self.r_rx_bar = MeterBar(m, width=170)
        self.r_rx_bar.grid(row=4, column=1, sticky="ew", padx=6)
        ttk.Label(m, text="Mic:").grid(row=5, column=0, sticky="e", pady=0)
        self.r_tx_bar = MeterBar(m, width=170)
        self.r_tx_bar.grid(row=5, column=1, sticky="ew", padx=6)
        # calibration: a small toggle; its buttons appear on the line below when wanted
        self.cal_toggle = ttk.Button(m, text="Calibration", width=12, command=self.toggle_cal)
        self.cal_toggle.grid(row=6, column=0, columnspan=2, sticky="w", pady=(3, 0))
        b = ttk.Frame(m)
        self.cal_frame = b
        b.grid(row=7, column=0, columnspan=3, sticky="w", pady=(3, 0))
        ttk.Button(b, text="Cal PO", width=7, command=lambda: self.calibrate("po")).grid(
            row=0, column=0, padx=1)
        ttk.Button(b, text="Cal SWR", width=7, command=lambda: self.calibrate("swr")).grid(
            row=0, column=1, padx=1)
        ttk.Button(b, text="Cal S9", width=7, command=self.calibrate_s9).grid(
            row=0, column=2, padx=1)
        ttk.Button(b, text="Clear", width=6, command=self.clear_cal).grid(
            row=0, column=3, padx=1)
        self.cal_txt = tk.StringVar()
        ttk.Label(b, textvariable=self.cal_txt).grid(row=1, column=0, columnspan=4,
                                                      sticky="w", pady=(2, 0))
        self.apply_cal_visibility()

        # ---- log, with a button to hide it
        lh = ttk.Frame(outer)
        lh.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        lh.columnconfigure(0, weight=1)
        ttk.Label(lh, text="Log").grid(row=0, column=0, sticky="w")
        self.cw_btn = ttk.Button(lh, text="CW window", style="Compact.TButton",
                                 command=self.toggle_cw_window)
        self.cw_btn.grid(row=0, column=1, sticky="e", padx=(0, 4))
        self.log_btn = ttk.Button(lh, text="Hide log", style="Compact.TButton",
                                  command=self.toggle_log)
        self.log_btn.grid(row=0, column=2, sticky="e")
        box = ttk.Frame(outer)
        self.log_box = box
        box.grid(row=3, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.log = tk.Text(box, height=5, width=32, state="disabled", wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        self.update_cal_text()
        self.function_tab = FunctionTab(self.nb, self)
        self.nb.add(self.function_tab, text="Function")
        self.settings_tab = SettingsTab(self.nb, self)
        self.nb.add(self.settings_tab, text="Menu")
        self.cw_window = CwWindow(self)
        self.apply_log_visibility()

    def build_nr(self, parent, row):
        """The noise reduction controls (spectral subtraction on the audio you listen to), on the
        Radio tab: a check box, the level, and the automatic calibration."""
        ttk.Style().configure("Compact.TButton", padding=(5, 1), width=0)
        nrf = ttk.Frame(parent)
        nrf.grid(row=row, column=0, sticky="ew", pady=(2, 0))
        self.nr_chk = ttk.Checkbutton(nrf, text="Noise reduction", variable=self.nr_on,
                                      command=self.on_nr_toggle)
        self.nr_chk.grid(row=0, column=0)
        ttk.Label(nrf, text="Level").grid(row=0, column=1, padx=(10, 3))
        self.nr_cb = ttk.Combobox(nrf, textvariable=self.nr_level, state="readonly", width=3,
                                  values=[str(n) for n in NR_LEVELS])
        self.nr_cb.grid(row=0, column=2)
        self.nr_cb.bind("<<ComboboxSelected>>", lambda e: self.on_nr_level())
        self.nr_btn = ttk.Button(nrf, text="Auto-calibrate", style="Compact.TButton",
                                 command=self.nr_cal.start)
        self.nr_btn.grid(row=0, column=3, padx=(12, 0))
        ttk.Label(nrf, textvariable=self.nr_status, foreground="#666666", wraplength=420,
                  justify="left").grid(row=1, column=0, columnspan=4, sticky="w", pady=(1, 0))
        if not (HAVE_NUMPY and AUDIO_OK):               # nothing to work on, or nothing to do it with
            for w in (self.nr_chk, self.nr_cb, self.nr_btn):
                w.configure(state="disabled")
        self.refresh_nr_status()

    def toggle_cw_window(self):
        self.cw_window.toggle()

    # ---- the log can be hidden -----------------------------------------------------------
    def toggle_log(self):
        self.log_hidden = not self.log_hidden
        self.apply_log_visibility()
        self.fit_window(shrink=True)

    def apply_log_visibility(self):
        """Show or hide the log. It keeps collecting messages while hidden, and the Copy report
        button still includes them."""
        outer = self.log_box.master
        if self.log_hidden:
            self.log_box.grid_remove()
            outer.rowconfigure(3, weight=0)           # no empty space where the log was
            self.log_btn.configure(text="Show log")
        else:
            self.log_box.grid()
            outer.rowconfigure(3, weight=1)
            self.log_btn.configure(text="Hide log")

    # ---- the Band pull-down --------------------------------------------------------------
    def on_band(self, _event=None):
        """Go to a band: the last frequency and mode used on it, or its usual starting point the
        first time. This tunes VFO A, as the Set A button does."""
        name = self.band_var.get()
        band = BAND_BY_NAME.get(name)
        if not band:
            return
        _n, lo, hi, hz, ham = band
        last = self.band_mem.get(name)
        if last and lo <= last[0] <= hi:
            hz, ham = last
        if not self.on_freq_change("A", hz):          # it says why when it refuses
            self.band_hold = None
            self.update_band(self.radio.snapshot())   # put the pull-down back
            return
        self.band_hold = (name, time.time() + 1.5)
        self.vfo_frames["A"][2].set_value(hz)
        if ham in LABEL_OF:
            self.mode_var.set(LABEL_OF[ham])
            # the frequency goes out after a short pause (so that a burst of wheel clicks is one
            # command), and the mode must follow it, not run ahead of it
            self.root.after(160, lambda: self._band_mode(ham))
        self.append_log(f"Band {name}: VFO A -> {self.fmt_freq(hz)}, {LABEL_OF.get(ham, ham)}")

    def _band_mode(self, ham):
        if self.radio.connected and not self.radio.snapshot().get("ptt"):
            self.pending_mode = (ham, time.time())
            self.radio.set_mode(ham)

    def update_band(self, s):
        """Keep the pull-down showing the band the radio is on, and remember where it is on it."""
        hz = s.get("freq")
        name = band_of(hz) or "Band"
        if name != "Band" and s.get("mode") and not s.get("ptt") and not self.band_hold:
            self.band_mem[name] = [int(hz), s["mode"]]      # not while the radio is catching up
        if self.band_hold:
            held, until = self.band_hold
            if time.time() < until and name != held:
                return                                 # the radio has not followed the pick yet
            self.band_hold = None
        if self.band_var.get() != name:
            self.band_var.set(name)

    # ---- Audio tab: four endpoints, as in FreeDV's audio settings ------------------------
    HINT_REMOTE = ("Remote: the bridge on the server carries the radio's audio. A radio end set to "
                   "(direct) connects the network straight to your speakers or mic. A virtual "
                   "cable (BlackHole on a Mac, VB-Cable on Windows) connects through the cable, "
                   "which FreeDV can share. The server serves one client at a time.")
    HINT_LOCAL = ("Local: choose the radio's own sound card (its USB codec) for radio to "
                  "computer and computer to radio. Nothing goes over the network.")

    def build_audio(self, tab):
        self.audio_node, self.rx_router, self.tx_router = None, None, None
        self.a_tx_cable, self.mic_on, self.audio_ready = False, False, AUDIO_OK
        self.a_disp = {"rx": 0.0, "tx": 0.0}
        self.a_diag = {"net": 0, "cable": 0, "spk": 0}      # receive peaks over the last second
        self.a_txdiag = {"mic": 0, "sent": 0, "alc": 0.0, "tx": 0}   # transmit peaks, last second
        self.a_tick, self.a_was_linked = 0, False
        self.a_detail_text = ""
        self.nr, self.nr_cal = None, NoiseCalibrator(self)      # noise reduction (when the audio runs)
        self.nr_on = tk.BooleanVar(value=bool(self.cfg.get("nr_on", False)))   # shown on the Radio tab
        self.nr_level = tk.StringVar(value=str(int(self.cfg.get("nr_level", 8))))
        self.nr_status = tk.StringVar(value="")
        if not AUDIO_OK:
            ttk.Label(tab, justify="left", wraplength=380, text=(
                "Audio streaming is not available:\n" + str(AUDIO_ERR) + "\n\nInstall the "
                "sounddevice package (pip install sounddevice). The rest of the panel works "
                "without it.")).grid(row=0, column=0, columnspan=3, sticky="w")
            return
        c = self.cfg
        adv = c.get("audio_adv", {})
        self.audio_mode = tk.StringVar(value=c.get("audio_mode", "remote"))
        self.audio_all_devices = tk.BooleanVar(value=bool(c.get("audio_all_devices", False)))
        self._reset_noted = set()
        self.audio_host = tk.StringVar(value=c.get("audio_host", ""))
        self.audio_port = tk.StringVar(value=str(c.get("audio_port", 5800)))
        self.audio_codec = tk.StringVar(value=c.get("audio_codec", "pcm"))
        self.audio_jitter = tk.StringVar(value=str(c.get("audio_jitter", 110)))
        self.audio_rate = tk.StringVar(value=str(adv.get("rate", 48000)))
        self.audio_frame = tk.StringVar(value=str(adv.get("frame", 10)))
        self.audio_pace = tk.BooleanVar(value=adv.get("pace", True))
        self.audio_stereo = tk.BooleanVar(value=adv.get("stereo", True))
        self.audio_bitrate = tk.StringVar(value=str(adv.get("bitrate", 64)))
        self.audio_bw = tk.StringVar(value=adv.get("bw", "narrow"))
        self.audio_signal = tk.StringVar(value=adv.get("signal", "music"))
        self.audio_fec = tk.BooleanVar(value=adv.get("fec", False))
        self.audio_loss = tk.StringVar(value=str(adv.get("loss", 5)))
        self.a_show_adv = bool(adv.get("show", False))
        self.a_radio_in, self.a_spk = tk.StringVar(), tk.StringVar()
        self.a_mic, self.a_radio_out = tk.StringVar(), tk.StringVar()
        dv = c.get("audio_devices", {})      # (name, api) lists, or "direct" / "none" / None
        self.a_want = {"radio_in": dv.get("radio_in"),
                       "speakers": dv.get("speakers", dv.get("out")),   # older settings used in/out
                       "mic": dv.get("mic", dv.get("in")),
                       "radio_out": dv.get("radio_out")}
        self.a_in_map, self.a_out_map, self.a_dup_map, self.a_key = {}, {}, {}, {}
        self.a_allowed = {"radio_in": set(), "radio_out": set()}
        self.audio_status = tk.StringVar(value="Stopped")
        self.a_hint = tk.StringVar()
        bold = ("TkDefaultFont", 10, "bold")

        mf = ttk.Frame(tab)
        mf.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(mf, text="Radio:").grid(row=0, column=0)
        self.a_mode_rbs = [
            ttk.Radiobutton(mf, text="Remote (server)", variable=self.audio_mode,
                            value="remote", command=self.on_audio_mode),
            ttk.Radiobutton(mf, text="Local (sound card)", variable=self.audio_mode,
                            value="local", command=self.on_audio_mode)]
        self.a_mode_rbs[0].grid(row=0, column=1, padx=(8, 4))
        self.a_mode_rbs[1].grid(row=0, column=2, padx=4)
        f = ttk.Frame(tab)
        f.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Label(f, text="Server:").grid(row=0, column=0)
        self.a_host_e = ttk.Entry(f, textvariable=self.audio_host, width=15)
        self.a_host_e.grid(row=0, column=1, padx=3)
        ttk.Label(f, text="UDP:").grid(row=0, column=2)
        self.a_port_e = ttk.Entry(f, textvariable=self.audio_port, width=6)
        self.a_port_e.grid(row=0, column=3, padx=3)
        self.a_btn = ttk.Button(f, text="Start audio", width=11, command=self.toggle_audio)
        self.a_btn.grid(row=0, column=4, padx=3)
        ttk.Label(tab, textvariable=self.a_hint, foreground="#666666", wraplength=380,
                  justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 2))

        ttk.Label(tab, text="Receive", font=bold).grid(row=3, column=0, columnspan=3,
                                                       sticky="w", pady=(4, 0))
        ttk.Label(tab, text="Radio to computer:").grid(row=4, column=0, sticky="w")
        self.a_radio_in_cb = ttk.Combobox(tab, textvariable=self.a_radio_in, width=21,
                                          state="readonly")
        self.a_radio_in_cb.grid(row=4, column=1, sticky="ew", padx=4, pady=1)
        self.a_radio_in_cb.bind("<<ComboboxSelected>>", lambda e: self.on_audio_dev_pick("radio_in"))
        ttk.Label(tab, text="Computer to speaker:").grid(row=5, column=0, sticky="w")
        self.a_spk_cb = ttk.Combobox(tab, textvariable=self.a_spk, width=21, state="readonly")
        self.a_spk_cb.grid(row=5, column=1, sticky="ew", padx=4, pady=1)
        self.a_spk_cb.bind("<<ComboboxSelected>>", lambda e: self.on_audio_dev_pick("speakers"))
        ttk.Button(tab, text="Test", width=7, command=self.test_speakers).grid(row=5, column=2)
        ttk.Label(tab, text="Volume:").grid(row=6, column=0, sticky="w")
        self.a_rx_scale = ttk.Scale(tab, from_=0, to=200, orient="horizontal",
                                    variable=self.rx_var, command=self.on_audio_rx_slide)
        self.a_rx_scale.grid(row=6, column=1, sticky="ew", padx=4)
        ttk.Label(tab, textvariable=self.a_rx_lbl, width=6).grid(row=6, column=2, sticky="w")
        ttk.Label(tab, text="Level:").grid(row=7, column=0, sticky="w")
        self.a_rx_bar = MeterBar(tab, width=200)
        self.a_rx_bar.grid(row=7, column=1, sticky="ew", padx=4, pady=1)
        ttk.Label(tab, text="Transmit", font=bold).grid(row=10, column=0, columnspan=3,
                                                        sticky="w", pady=(8, 0))
        ttk.Label(tab, text="Mic to computer:").grid(row=11, column=0, sticky="w")
        self.a_mic_cb = ttk.Combobox(tab, textvariable=self.a_mic, width=21, state="readonly")
        self.a_mic_cb.grid(row=11, column=1, sticky="ew", padx=4, pady=1)
        self.a_mic_cb.bind("<<ComboboxSelected>>", lambda e: self.on_audio_dev_pick("mic"))
        ttk.Button(tab, text="Rescan", width=7, command=self.refresh_audio_devices).grid(
            row=11, column=2)
        ttk.Label(tab, text="Computer to radio:").grid(row=12, column=0, sticky="w")
        self.a_radio_out_cb = ttk.Combobox(tab, textvariable=self.a_radio_out, width=21,
                                           state="readonly")
        self.a_radio_out_cb.grid(row=12, column=1, sticky="ew", padx=4, pady=1)
        self.a_radio_out_cb.bind("<<ComboboxSelected>>",
                                 lambda e: self.on_audio_dev_pick("radio_out"))
        self.a_all_ck = ttk.Checkbutton(tab, text="Show all", variable=self.audio_all_devices,
                                        command=self.apply_audio_devices)
        self.a_all_ck.grid(row=12, column=2)
        ttk.Label(tab, text="Mic level:").grid(row=13, column=0, sticky="w")
        self.a_tx_scale = ttk.Scale(tab, from_=0, to=200, orient="horizontal",
                                    variable=self.tx_var, command=self.on_audio_tx_slide)
        self.a_tx_scale.grid(row=13, column=1, sticky="ew", padx=4)
        ttk.Label(tab, textvariable=self.a_tx_lbl, width=6).grid(row=13, column=2, sticky="w")
        ttk.Label(tab, text="Level:").grid(row=14, column=0, sticky="w")
        self.a_tx_bar = MeterBar(tab, width=200)
        self.a_tx_bar.grid(row=14, column=1, sticky="ew", padx=4, pady=1)
        self.mic_btn = ttk.Button(tab, text="Mic: OFF", width=10, command=self.toggle_mic)
        self.mic_btn.grid(row=15, column=0, sticky="w", pady=(3, 0))
        ttk.Label(tab, text="The microphone is sent only while Mic is ON, or while PTT is on.",
                  foreground="#666666", wraplength=250, justify="left").grid(
            row=15, column=1, columnspan=2, sticky="w", padx=4, pady=(3, 0))

        ttk.Separator(tab).grid(row=16, column=0, columnspan=3, sticky="ew", pady=6)
        g = ttk.Frame(tab)
        g.grid(row=17, column=0, columnspan=3, sticky="w", pady=(0, 2))
        ttk.Label(g, text="Codec:").grid(row=0, column=0)
        codecs = ["pcm", "opus"] if HAVE_OPUS else ["pcm"]
        self.a_codec_cb = ttk.Combobox(g, textvariable=self.audio_codec, values=codecs, width=5,
                                       state="readonly")
        self.a_codec_cb.grid(row=0, column=1, padx=3)
        self.a_codec_cb.bind("<<ComboboxSelected>>", lambda e: self.update_adv_states())
        ttk.Label(g, text="Jitter:").grid(row=0, column=2, padx=(8, 0))
        self.a_jit_e = ttk.Entry(g, textvariable=self.audio_jitter, width=4)
        self.a_jit_e.grid(row=0, column=3, padx=3)
        ttk.Label(g, text="ms").grid(row=0, column=4)
        self.a_adv_btn = ttk.Button(g, text="Advanced", width=11, command=self.toggle_adv)
        self.a_adv_btn.grid(row=0, column=5, padx=(14, 0))

        # the bridge's own options, hidden until wanted
        h = ttk.Frame(tab)
        self.a_adv = h
        h.grid(row=18, column=0, columnspan=3, sticky="w", pady=(2, 2))
        ttk.Label(h, text="Rate:").grid(row=0, column=0, sticky="e")
        self.a_rate_cb = ttk.Combobox(h, textvariable=self.audio_rate, width=6, state="readonly",
                                      values=[str(r) for r in OPUS_RATES])
        self.a_rate_cb.grid(row=0, column=1, padx=3, pady=1)
        ttk.Label(h, text="Hz   Frame:").grid(row=0, column=2, sticky="e")
        self.a_frame_cb = ttk.Combobox(h, textvariable=self.audio_frame, width=3, state="readonly",
                                       values=[str(m) for m in OPUS_FRAME_MS])
        self.a_frame_cb.grid(row=0, column=3, padx=3)
        ttk.Label(h, text="ms").grid(row=0, column=4, sticky="w")
        self.a_pace_ck = ttk.Checkbutton(h, text="Smooth packet timing", variable=self.audio_pace)
        self.a_pace_ck.grid(row=1, column=0, columnspan=3, sticky="w")
        self.a_stereo_ck = ttk.Checkbutton(h, text="Stereo playback", variable=self.audio_stereo)
        self.a_stereo_ck.grid(row=1, column=3, columnspan=3, sticky="w", padx=(6, 0))
        ttk.Label(h, text="Opus:").grid(row=2, column=0, sticky="e")
        o = ttk.Frame(h)
        o.grid(row=2, column=1, columnspan=5, sticky="w")
        self.a_bitrate_e = ttk.Entry(o, textvariable=self.audio_bitrate, width=4)
        self.a_bitrate_e.grid(row=0, column=0, padx=3)
        ttk.Label(o, text="kbps").grid(row=0, column=1)
        self.a_bw_cb = ttk.Combobox(o, textvariable=self.audio_bw, width=9, state="readonly",
                                    values=list(BW_CHOICES))
        self.a_bw_cb.grid(row=0, column=2, padx=(8, 3))
        self.a_signal_cb = ttk.Combobox(o, textvariable=self.audio_signal, width=6,
                                        state="readonly", values=list(OPUS_SIGNALS))
        self.a_signal_cb.grid(row=0, column=3, padx=3)
        self.a_fec_ck = ttk.Checkbutton(h, text="Loss protection (FEC), expected loss",
                                        variable=self.audio_fec)
        self.a_fec_ck.grid(row=3, column=0, columnspan=4, sticky="w")
        self.a_loss_e = ttk.Entry(h, textvariable=self.audio_loss, width=3)
        self.a_loss_e.grid(row=3, column=4, padx=3)
        ttk.Label(h, text="%").grid(row=3, column=5, sticky="w")
        ttk.Label(h, text="Remote: the server must use the same codec, rate and frame size.",
                  foreground="#666666").grid(row=4, column=0, columnspan=6, sticky="w")

        ttk.Label(tab, textvariable=self.audio_status, wraplength=380, justify="left").grid(
            row=19, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(tab, text="Copy report", width=12, command=self.copy_report).grid(
            row=20, column=0, sticky="w", pady=(4, 0))
        ttk.Label(tab, text="Copies the settings, status and log, to paste into a message.",
                  foreground="#666666", wraplength=250, justify="left").grid(
            row=20, column=1, columnspan=2, sticky="w", padx=4, pady=(4, 0))
        self.a_hint.set(self.HINT_REMOTE if self.audio_mode.get() == "remote" else self.HINT_LOCAL)
        self.apply_adv_visibility()
        self.refresh_audio_devices(reinit=False)
        self.update_adv_states()

    def audio_running(self):
        return (self.audio_node is not None or self.rx_router is not None
                or self.tx_router is not None)

    def has_mic_path(self):
        """Is there a microphone path that the Mic button and PTT can open?"""
        node = self.audio_node
        return self.tx_router is not None or (
            node is not None and not node.args.rx_only and not self.a_tx_cable)

    def on_audio_mode(self):
        self.a_hint.set(self.HINT_REMOTE if self.audio_mode.get() == "remote" else self.HINT_LOCAL)
        self.apply_audio_devices()
        self.update_adv_states()
        self.save_cfg()

    def toggle_adv(self):
        self.a_show_adv = not self.a_show_adv
        self.apply_adv_visibility()
        self.fit_window(shrink=True)
        self.save_cfg()

    def apply_adv_visibility(self):
        if self.a_show_adv:
            self.a_adv.grid()
            self.a_adv_btn.configure(text="Advanced \u25be")
        else:
            self.a_adv.grid_remove()
            self.a_adv_btn.configure(text="Advanced \u25b8")

    def update_adv_states(self):
        """Lock every setting while audio runs. Network settings apply to a remote radio only,
        and the Opus options only to the Opus codec."""
        running = self.audio_running()
        remote = self.audio_mode.get() == "remote"
        net = "disabled" if (running or not remote) else "normal"
        for w in (self.a_host_e, self.a_port_e, self.a_jit_e, self.a_pace_ck):
            w.configure(state=net)
        self.a_codec_cb.configure(state="disabled" if (running or not remote) else "readonly")
        for w in (self.a_radio_in_cb, self.a_spk_cb, self.a_mic_cb, self.a_radio_out_cb,
                  self.a_rate_cb, self.a_frame_cb):
            w.configure(state="disabled" if running else "readonly")
        self.a_stereo_ck.configure(state="disabled" if running else "normal")
        self.a_all_ck.configure(state="disabled" if running else "normal")
        for rb in self.a_mode_rbs:
            rb.configure(state="disabled" if running else "normal")
        opus = remote and self.audio_codec.get() == "opus"
        on = "disabled" if (running or not opus) else "normal"
        for w in (self.a_bitrate_e, self.a_loss_e, self.a_fec_ck):
            w.configure(state=on)
        for w in (self.a_bw_cb, self.a_signal_cb):
            w.configure(state="disabled" if (running or not opus) else "readonly")

    def refresh_audio_devices(self, reinit=True):
        if not self.audio_ready:
            return
        if reinit and not self.audio_running():
            try:                                  # make PortAudio re-scan (private API: guarded)
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
        self.a_in_map, self.a_out_map, self.a_dup_map, self.a_key = {}, {}, {}, {}
        try:
            apis = sd.query_hostapis()
            for i, d in enumerate(sd.query_devices()):
                api = apis[d["hostapi"]]["name"]
                label = f"{i}: {d['name']} [{api}]"
                self.a_key[label] = (d["name"], api)
                if d["max_input_channels"] > 0:
                    self.a_in_map[label] = i
                if d["max_output_channels"] > 0:
                    self.a_out_map[label] = i
                if d["max_input_channels"] > 0 and d["max_output_channels"] > 0:
                    self.a_dup_map[label] = i     # a virtual cable can be both played to and read
        except Exception as exc:
            self.append_log(f"Could not list audio devices: {exc}")
        self.apply_audio_devices()

    def _find_audio_label(self, want, mp):
        """Dropdown label for a saved (name, api); exact match, else a unique name match."""
        if not want or isinstance(want, str):
            return ""
        want = tuple(want)
        for label in mp:
            if self.a_key.get(label) == want:
                return label
        same = [lb for lb in mp if self.a_key.get(lb, ("",))[0] == want[0]]
        return same[0] if len(same) == 1 else ""

    def _device_present(self, want):
        want = tuple(want)
        return any(v == want or v[0] == want[0] for v in self.a_key.values())

    def apply_audio_devices(self):
        """Fill the four dropdowns with the devices that suit each role: real speakers and
        microphones for your ends, and (remote radio) virtual cables for the radio ends. Tick
        'All devices' to list everything. A saved device that is present but does not suit its
        role is replaced by a default, and a saved device that is missing stays visible as
        '(not present)' so what you chose is never silently swapped for another."""
        remote = self.audio_mode.get() == "remote"
        show_all = self.audio_all_devices.get()
        virt = lambda lb: is_virtual_cable(self.a_key[lb][0])
        keep = lambda mp: {lb: i for lb, i in mp.items() if show_all or not virt(lb)}
        # Remote radio ends. The bridge PLAYS the radio's audio into one cable (needs an output
        # device) and READS the transmit audio from another (needs an input device). BlackHole is
        # both; Windows cables such as VB-Cable are separate one-way devices.
        if show_all:
            cab_out, cab_in = dict(self.a_out_map), dict(self.a_in_map)
        else:
            cab_out = {lb: i for lb, i in self.a_out_map.items() if virt(lb)}
            cab_in = {lb: i for lb, i in self.a_in_map.items() if virt(lb)}
            if remote and not cab_out:              # no virtual cable found: offer what can work
                cab_out = dict(self.a_dup_map) or dict(self.a_out_map)
            if remote and not cab_in:
                cab_in = dict(self.a_dup_map) or dict(self.a_in_map)
        try:
            defaults = {"in": sd.default.device[0], "out": sd.default.device[1]}
        except Exception:
            defaults = {}
        spec = (
            ("radio_in", self.a_radio_in_cb, self.a_radio_in,
             cab_out if remote else keep(self.a_in_map),
             (DIRECT_RX, "direct") if remote else None, "Radio to computer"),
            ("speakers", self.a_spk_cb, self.a_spk, keep(self.a_out_map), None,
             "Computer to speaker"),
            ("mic", self.a_mic_cb, self.a_mic, keep(self.a_in_map), (NONE_LABEL, "none"),
             "Mic to computer"),
            ("radio_out", self.a_radio_out_cb, self.a_radio_out,
             cab_in if remote else keep(self.a_out_map),
             (DIRECT_TX, "direct") if remote else None, "Computer to radio"),
        )
        self.a_allowed = {key: set(mp) for key, _cb, _var, mp, _sp, _what in spec}
        for key, cb, var, mp, special, what in spec:
            want = self.a_want.get(key)
            values = ([special[0]] if special else []) + list(mp)
            label = ""
            if special and want == special[1]:
                label = special[0]
            else:
                label = self._find_audio_label(want, mp)
                if not label and isinstance(want, (list, tuple)) and want:
                    if self._device_present(want):    # there, but not suited to this role
                        if (key, tuple(want)) not in self._reset_noted:
                            self._reset_noted.add((key, tuple(want)))
                            self.logq.put(f"{what}: {want[0]} does not suit this role, so a "
                                          "default was chosen instead.")
                        self.a_want[key] = want = None
                    else:                             # not plugged in at the moment
                        label = f"(not present) {want[0]} [{want[1]}]"
                        values.insert(0, label)
                if not label and (not want or want in ("direct", "none")):
                    if key in ("speakers", "mic"):    # suggest the system default, else the first
                        dflt = defaults.get("out" if key == "speakers" else "in")
                        label = next((lb for lb, i in mp.items() if i == dflt),
                                     next(iter(mp), ""))
                    elif special:                     # remote radio ends start as (direct)
                        label = special[0]
            cb["values"] = values
            var.set(label)

    def on_audio_dev_pick(self, key):
        var = {"radio_in": self.a_radio_in, "speakers": self.a_spk, "mic": self.a_mic,
               "radio_out": self.a_radio_out}[key]
        label = var.get()
        if label in (DIRECT_RX, DIRECT_TX):
            self.a_want[key] = "direct"
        elif label == NONE_LABEL:
            self.a_want[key] = "none"
        elif label in self.a_key:
            self.a_want[key] = self.a_key[label]
        else:
            return
        self.save_cfg()
        self.apply_audio_devices()

    def test_speakers(self):
        """Play a short 1 kHz tone on the chosen speakers, to tell a speaker problem from a
        routing problem. It works whether or not audio is running."""
        if not self.audio_ready:
            return
        label = self.a_spk.get()
        if label not in self.a_out_map:
            messagebox.showinfo("Audio", "Choose the speakers (computer to speaker) first.")
            return
        rate, state = 48000, {"n": 0}
        period = array.array("h", [int(8000 * math.sin(2 * math.pi * i / 48)) for i in range(48)])

        def cb(outdata, frames, t, status):
            n = state["n"]
            mono = array.array("h", (period[(n + i) % 48] for i in range(frames)))
            state["n"] = n + frames
            if state["n"] > rate * 1.2:                       # about 1.2 seconds
                outdata[:] = bytes(len(outdata))
                raise sd.CallbackStop
            out = array.array("h", bytes(frames * state["ch"] * 2))
            for c in range(state["ch"]):
                out[c::state["ch"]] = mono
            outdata[:] = out.tobytes()

        for ch in (2, 1):
            state["ch"], state["n"] = ch, 0
            try:
                stream = sd.RawOutputStream(device=self.a_out_map[label], samplerate=rate,
                                            channels=ch, dtype="int16", blocksize=480, callback=cb)
                stream.start()
                self.append_log(f"Playing a test tone on {self.a_key[label][0]} ...")
                self.root.after(1800, lambda: (stream.stop(), stream.close()))
                return
            except Exception as exc:
                last = exc
        messagebox.showerror("Audio", f"Could not play on that device:\n{last}")

    def toggle_audio(self):
        if self.audio_running():
            self.audio_stop()
        else:
            self.audio_start()

    def audio_start(self):
        if self.audio_running():
            return
        remote = self.audio_mode.get() == "remote"
        info = lambda msg: messagebox.showinfo("Audio", msg)
        try:
            port, jitter = int(self.audio_port.get()), int(self.audio_jitter.get())
            rate, frame = int(self.audio_rate.get()), int(self.audio_frame.get())
            bitrate, loss = int(self.audio_bitrate.get()) * 1000, int(self.audio_loss.get())
        except ValueError:
            info("The port, jitter, rate, frame size, bitrate and loss fields must be whole "
                 "numbers.")
            return
        if remote and not 1 <= port <= 65535:
            info("The UDP port must be between 1 and 65535.")
            return
        rin, spk, mic, rout = (self.a_radio_in.get(), self.a_spk.get(), self.a_mic.get(),
                               self.a_radio_out.get())
        if spk not in self.a_out_map:
            info("Choose the speakers (computer to speaker).")
            return
        if not self.audio_all_devices.get():
            for label, what in ((spk, "Computer to speaker"), (mic, "Mic to computer")):
                if label in self.a_key and is_virtual_cable(self.a_key[label][0]):
                    info(f"{what} is a virtual cable. Virtual cables belong on the radio ends "
                         "(Radio to computer and Computer to radio). Choose your real speakers "
                         "and microphone here, or tick Show all if you really mean it.")
                    return
        if mic == NONE_LABEL:
            mic_idx = None
        elif mic in self.a_in_map:
            mic_idx = self.a_in_map[mic]
        else:
            info("Choose the microphone (mic to computer), or '(none - listen only)'.")
            return
        if remote:
            rx_cable, tx_cable = rin != DIRECT_RX, rout != DIRECT_TX
            ends = ((rx_cable, "radio_in", rin, "Radio to computer", "played into"),
                    (tx_cable, "radio_out", rout, "Computer to radio", "recorded from"))
            for cable, key, label, what, verb in ends:
                if cable and label not in self.a_allowed[key]:
                    info(f"{what} must be (direct) or a virtual audio cable that can be {verb}, "
                         "such as BlackHole on a Mac or VB-Cable on Windows.")
                    return
            if rx_cable and tx_cable and rin == rout:
                info("Radio to computer and Computer to radio must be two different cables.")
                return
            idx_rin = self.a_out_map[rin] if rx_cable else None     # the bridge plays into it
            idx_rout = self.a_in_map[rout] if tx_cable else None    # the bridge records from it
            host = self.audio_host.get().strip() or self.host.get().strip()
            if not host:
                info("Enter the server's address (or the radio's host).")
                return
        else:
            rx_cable = tx_cable = True              # the local router does all the work
            if rin not in self.a_in_map:
                info("Choose the radio's sound card input (radio to computer).")
                return
            if mic_idx is not None and rout not in self.a_out_map:
                info("Choose the radio's sound card output (computer to radio).")
                return
            idx_rin = self.a_in_map[rin]
            idx_rout = self.a_out_map[rout] if mic_idx is not None else None
        stereo = self.audio_stereo.get()
        log = lambda m: self.logq.put("Audio: " + m)
        rx_router = tx_router = node = None
        try:
            if rx_cable:
                # remote: the engine hands its received audio over in memory (it also plays it into
                # the cable for FreeDV), so the cable is never opened a second time to be read back
                rx_router = AudioPath("receive", None if remote else idx_rin,
                                      self.a_out_map[spk], rate, frame, stereo, log)
                rx_router.set_gain_db(pct_to_db(self.a_rx_pct))
            if mic_idx is not None and tx_cable:
                # remote: the microphone goes straight to the bridge in memory (nothing is written
                # into the cable, so the cable is opened once, only to be read for FreeDV's audio)
                tx_router = AudioPath("transmit", mic_idx, None if remote else idx_rout,
                                      rate, frame, stereo, log)
                tx_router.set_gain_db(-120.0)       # the microphone starts muted
            if remote:
                node_in = idx_rout if tx_cable else mic_idx
                args = make_args(
                    host=host, port=port, rate=rate, frame_ms=frame, jitter_ms=jitter,
                    codec=self.audio_codec.get(), bitrate=bitrate,
                    opus_bw=BW_CHOICES.get(self.audio_bw.get(), "nb"),
                    opus_signal=self.audio_signal.get(), fec=self.audio_fec.get(),
                    loss=max(0, min(100, loss)), in_dev=node_in,
                    out_dev=idx_rin if rx_cable else self.a_out_map[spk],
                    out_channels=2 if stereo else 1, pace=self.audio_pace.get(),
                    in_gain_db=0.0 if tx_cable else -120.0,
                    out_gain_db=0.0 if rx_cable else pct_to_db(self.a_rx_pct),
                    rx_only=node_in is None)
                err = check_args(args)
                if err:
                    messagebox.showerror("Audio", err)
                    return
                node = Node(args, False, log=log)
                if rx_router is not None:
                    node.tap = rx_router.feed
                if tx_router is not None:
                    node.mix_mic = True
                    tx_router.sink = node.send_mic
            for part in (node, rx_router, tx_router):      # the bridge first, then the paths
                if part is not None:
                    part.start()
        except Exception as exc:
            for part in (rx_router, tx_router, node):
                if part is not None:
                    try:
                        part.stop()
                    except Exception:
                        pass
            messagebox.showerror("Audio", f"Could not start the audio:\n{exc}")
            return
        self.rx_router, self.tx_router, self.audio_node = rx_router, tx_router, node
        self.nr = None
        if HAVE_NUMPY:
            self.nr = SpectralNR(rate, int(self.nr_level.get()))
            self.nr.enabled = self.nr_on.get()
            target = rx_router if rx_router is not None else node     # the speakers' path only:
            target.nr = self.nr                                       # not the cable that FreeDV reads
        self.refresh_nr_status()
        self.a_tx_cable = tx_cable
        self.mic_on = False
        self.mic_btn.configure(text="Mic: OFF")
        for key in ("radio_in", "speakers", "mic", "radio_out"):      # remember what worked
            self.on_audio_dev_pick(key)
        self.a_btn.configure(text="Stop audio")
        self.a_was_linked = False
        self.audio_status.set("Connecting..." if remote else "Local audio running.")
        self.update_adv_states()
        self.fit_window()
        where = f"to {host}:{port}" if remote else "locally"
        self.append_log(f"Audio started {where} ("
                        + ("microphone muted" if self.has_mic_path() else "listen only") + ")")

    # ---- noise reduction ---------------------------------------------------------------------
    def on_nr_toggle(self):
        if self.nr is not None:
            self.nr.enabled = self.nr_on.get()
        self.refresh_nr_status()
        self.save_cfg()

    def on_nr_level(self):
        if self.nr is not None:
            self.nr.set_level(int(self.nr_level.get()))
        self.refresh_nr_status()
        self.save_cfg()

    def refresh_nr_status(self):
        if self.nr_cal.active:
            return                                   # the calibration is writing its own progress
        level = self.nr_level.get()
        if not HAVE_NUMPY:
            text = "Noise reduction needs numpy (pip install numpy), which is not installed."
        elif not AUDIO_OK:
            text = "Noise reduction works on the audio, which needs the sounddevice package."
        elif self.nr is None:
            text = (f"{'On' if self.nr_on.get() else 'Off'}, level {level}. Works while the audio runs.")
        elif self.nr.calibrated:
            info = self.nr.info
            text = (f"{'On' if self.nr.enabled else 'Off'}, level {level}. Calibrated: noise "
                    f"{info['noise_dbfs']:.0f} dBFS, "
                    f"{'steady' if info['cond'] >= 0.9 else 'fairly steady' if info['cond'] >= 0.7 else 'unsteady'}.")
        else:
            text = (f"{'On' if self.nr.enabled else 'Off'}, level {level}. Not calibrated (it follows the "
                    "noise by itself).")
        self.nr_status.set(text)

    def audio_stop(self):
        if self.nr_cal.active:
            self.nr_cal.abort("the audio was stopped")
        self.nr = None
        parts = (self.rx_router, self.tx_router, self.audio_node)
        self.rx_router = self.tx_router = self.audio_node = None
        for part in parts:
            if part is not None:
                part.stop()
        self.mic_on = False
        if self.audio_ready:
            self.mic_btn.configure(text="Mic: OFF")
            self.a_btn.configure(text="Start audio")
            self.update_adv_states()
            self.audio_status.set("Stopped")
            for bar in (self.a_rx_bar, self.r_rx_bar, self.a_tx_bar, self.r_tx_bar):
                bar.set(0, GREY)
            self.append_log("Audio stopped")

    def apply_rx_gain(self):
        db = pct_to_db(self.a_rx_pct)
        if self.rx_router is not None:
            self.rx_router.set_gain_db(db)
        elif self.audio_node is not None:
            self.audio_node.set_gain_db("out", db)

    def apply_mic_gain(self):
        db = pct_to_db(self.a_tx_pct) if self.mic_on else -120.0
        if self.tx_router is not None:
            self.tx_router.set_gain_db(db)
        elif self.audio_node is not None and self.has_mic_path():
            self.audio_node.set_gain_db("in", db)

    def toggle_mic(self):
        if not self.audio_running():
            self.append_log("Start the audio first")
            return
        if not self.has_mic_path():
            self.append_log("There is no microphone to switch (listen only, or the transmit audio "
                            "comes from the radio cable)")
            return
        self.mic_on = not self.mic_on
        self.mic_btn.configure(text="Mic: ON" if self.mic_on else "Mic: OFF")
        self.apply_mic_gain()
        self.append_log("Microphone audio is being sent" if self.mic_on
                        else "Microphone muted")

    def on_audio_rx_slide(self, value):
        self.a_rx_pct = max(0, min(200, int(round(float(value)))))
        if AUDIO_OK:
            self.a_rx_lbl.set(f"{self.a_rx_pct}%")
        self.apply_rx_gain()

    def on_audio_tx_slide(self, value):
        self.a_tx_pct = max(0, min(200, int(round(float(value)))))
        if AUDIO_OK:
            self.a_tx_lbl.set(f"{self.a_tx_pct}%")
        self.apply_mic_gain()

    def receive_diagnosis(self):
        """Peak levels (percent of full scale) at each stage of the receive path over the last
        second, and a plain hint about the first silent stage."""
        dg, node, rxr = self.a_diag, self.audio_node, self.rx_router
        pct = lambda v: f"{v * 100 // 32768}%"
        net, src, spk = dg["net"], dg["cable"], dg["spk"]
        self.a_diag = {"net": 0, "cable": 0, "spk": 0}
        no_spk = (" Audio reaches the speaker stage but nothing plays: check Volume and the "
                  "speaker device, and try the Test button.")
        if self.a_rx_pct == 0:
            no_spk = " Volume is at 0%."
        if node is not None:                          # remote radio
            parts = [f"from the network {pct(net)}", f"to the speakers {pct(spk)}"]
            hint = ""
            if node.stats()["linked"] and net == 0:
                hint = (" The server is sending silence: check the radio's squelch, mode and "
                        "receive audio, and the input device the bridge uses on the server.")
            elif net > 0 and spk == 0:
                hint = no_spk
        else:                                         # local radio
            parts = [f"from the radio's sound card {pct(src)}", f"to the speakers {pct(spk)}"]
            hint = ""
            if src == 0:
                hint = (" Nothing arrives from the radio's input device: check the device chosen "
                        "for Radio to computer and the radio's own audio settings. On a Mac, "
                        "also check that this program may use the microphone.")
            elif spk == 0:
                hint = no_spk
        return "Receive levels, last second: " + ", ".join(parts) + "." + hint

    def transmit_diagnosis(self):
        """Peak levels (percent of full scale) on the transmit side over the last second."""
        node, txr = self.audio_node, self.tx_router
        d, self.a_txdiag = self.a_txdiag, {"mic": 0, "sent": 0, "alc": 0.0, "tx": 0}
        pct = lambda v: f"{v * 100 // 32768}%"
        if txr is None:
            if node is not None and not node.args.rx_only:       # only a program on the cable (FreeDV)
                return f"Transmit: audio from the radio cable sent to the server {pct(d['sent'])}."
            return ""
        parts = [f"microphone {pct(d['mic'])} ({'Mic ON' if self.mic_on else 'Mic muted'})",
                 (f"sent to the server {pct(d['sent'])}" if node is not None
                  else f"to the radio {pct(d['sent'])}")]
        if d["tx"]:
            parts.append(f"radio ALC {d['alc'] * 100:.0f}%")
        hint = ""
        snap = self.radio.snapshot()
        if (self.mic_on and d["tx"] >= 8 and d["sent"] > 0 and d["alc"] < 0.06
                and snap.get("mode") in ("USB", "LSB", "PKTUSB", "PKTLSB")):
            hint = (" Your audio is leaving the panel, but the radio's ALC is not moving, so the "
                    "audio is not reaching the radio, or is far too quiet. On the radio's computer, "
                    "check that the sound card's volume is up and not muted, and that the bridge "
                    "uses the right output device; then the radio's own settings for this mode.")
        elif self.mic_on:
            if d["mic"] == 0:
                hint = (" The microphone is silent: check the microphone and its level in the "
                        "system sound settings, and that this program may use the microphone "
                        "(macOS: System Settings, Privacy and Security, Microphone).")
            elif d["sent"] == 0:
                hint = " The microphone is live but nothing is being sent."
        return "Transmit levels, last second: " + ", ".join(parts) + "." + hint

    def update_audio(self, now):
        if not self.audio_ready or not self.audio_running():
            return
        node, rxr, txr = self.audio_node, self.rx_router, self.tx_router
        rx_peak = tx_peak = 0
        dg = self.a_diag
        if rxr is not None:
            rx_peak, rxr.peak = rxr.peak, 0
            cable, rxr.peak_in = rxr.peak_in, 0
            dg["cable"], dg["spk"] = max(dg["cable"], cable), max(dg["spk"], rx_peak)
        if node is not None:
            p, node.out_peak = node.out_peak, 0
            dg["net"] = max(dg["net"], p)
            if rxr is None:                             # direct: the network audio plays right here
                rx_peak = p
                dg["spk"] = max(dg["spk"], p)
        td = self.a_txdiag
        snap = self.radio.snapshot()
        if snap.get("ptt"):                               # the radio's ALC shows audio reaching it
            td["tx"] += 1
            td["alc"] = max(td["alc"], self.alc_fraction(snap, self.radio.units == "raw"))
        if txr is not None:
            tx_peak, txr.peak = txr.peak, 0
            mic, txr.peak_in = txr.peak_in, 0
            td["mic"] = max(td["mic"], mic)
        if node is not None:
            sent, node.in_peak = node.in_peak, 0
            td["sent"] = max(td["sent"], sent)
            if txr is None:
                tx_peak = sent
        elif txr is not None:                           # local radio: what goes to the radio's input
            td["sent"] = max(td["sent"], tx_peak)
        self.a_disp["rx"] = max(level_pct(rx_peak), self.a_disp["rx"] - 0.06)
        self.a_disp["tx"] = max(level_pct(tx_peak), self.a_disp["tx"] - 0.06)
        rx_color = BLUE if self.a_disp["rx"] < 0.9 else AMBER
        tx_color = GREEN if (self.mic_on or txr is None) else GREY
        for bar in (self.a_rx_bar, self.r_rx_bar):
            bar.set(self.a_disp["rx"], rx_color)
        for bar in (self.a_tx_bar, self.r_tx_bar):
            bar.set(self.a_disp["tx"], tx_color)
        self.a_tick += 1
        if self.a_tick % 10:                            # the text below changes about once a second
            return
        head, detail = [], []
        if node is not None:
            st = node.stats()
            if st["linked"] != self.a_was_linked:
                self.a_was_linked = st["linked"]
                self.append_log("Audio link up" if st["linked"] else "Audio link lost")
            head.append("Connected to the server." if st["linked"] else "No data from the server.")
            detail.append(f"RX {st['rx']} packets; lost {st['lost']} ({st['loss_events']} events, "
                          f"longest {st['max_gap']}); late {st['late']}; bad {st['bad']}; underruns "
                          f"{st['underruns']}; dropped {st['dropped']}; buffer {st['buf']} frames.")
            detail.append(f"TX {st['tx_kbps']:.0f} kbps ({st['tx_pkts']} packets, errors "
                          f"{st['tx_err']}); send timing {st['tx_tight']} too close, {st['tx_slow']} "
                          f"too late, longest gap {st['tx_gap_max']:.0f} ms.")
        else:
            head.append("Local audio running.")
        for r in (rxr, txr):
            if r is not None:
                detail.append(f"{r.name.capitalize()} path: underruns {r.underruns}, dropped "
                              f"{r.dropped}.")
        levels = [self.receive_diagnosis()]
        tx = self.transmit_diagnosis()
        if tx:
            levels.append(tx)
        # the levels say where the audio stops, so they come first. The packet counters are shown
        # only while Advanced is open (a report always includes them).
        self.a_detail_text = "\n".join(detail)
        self.audio_status.set("\n".join(head + levels + (detail if self.a_show_adv else [])))
        self.fit_window()

    def size_notebook(self):
        """Make the notebook as tall as the tab that is showing. By default a ttk notebook is as
        tall as its tallest tab, which left blank space under the shorter ones."""
        try:
            tab = self.nb.nametowidget(self.nb.select())
        except (tk.TclError, KeyError):
            return
        self.root.update_idletasks()
        want = tab.winfo_reqheight()
        if int(self.nb.cget("height") or 0) != want:
            self.nb.configure(height=want)

    def fit_window(self, shrink=False):
        """Fit the window to the tab that is showing: grow it when the content needs more room
        (the status text changes length), and, after a tab change, shrink it so no space is
        wasted."""
        r = self.root
        self.size_notebook()
        r.update_idletasks()
        need = min(r.winfo_reqheight(), r.winfo_screenheight() - 80)
        if need > r.winfo_height() + 1 or (shrink and need < r.winfo_height() - 1):
            r.geometry(f"{r.winfo_width()}x{need}")

    def build_report(self):
        """Settings, status and the recent log as plain text, to paste into a message. IPv4
        addresses are hidden."""
        def safe(fn):                      # a report must never fail because one detail is unreadable
            try:
                return fn()
            except Exception as exc:
                return f"(unavailable: {type(exc).__name__}: {exc})"

        dev = {k: v.get() for k, v in (("radio to computer", self.a_radio_in),
                                       ("computer to speaker", self.a_spk),
                                       ("mic to computer", self.a_mic),
                                       ("computer to radio", self.a_radio_out))}
        snap = self.radio.snapshot()
        log_lines = self.log.get("1.0", "end").strip().splitlines()[-30:]
        lines = [
            f"{APP_NAME} report",
            f"panel version: {__version__}",
            f"engine: {ENGINE_VERSION}",
            f"python {sys.version.split()[0]} on {sys.platform}, "
            f"packaged={bool(getattr(sys, 'frozen', False))}",
            "sounddevice: " + (safe(lambda: f"ok, PortAudio {sd.get_portaudio_version()[1]}")
                               if AUDIO_OK else str(AUDIO_ERR)),
            f"opus: {'yes' if HAVE_OPUS else 'no'}, shipped copy: {_bundled_opus or 'none'}",
            "",
            f"radio audio: {self.audio_mode.get()}   running: {'yes' if self.audio_running() else 'no'}",
            f"server: {self.audio_host.get().strip() or '(radio host)'}:{self.audio_port.get()}",
            f"codec: {self.audio_codec.get()}, rate {self.audio_rate.get()}, frame "
            f"{self.audio_frame.get()} ms, jitter {self.audio_jitter.get()} ms, "
            f"smooth timing {self.audio_pace.get()}, stereo {self.audio_stereo.get()}",
            *[f"{k}: {v}" for k, v in dev.items()],
            f"Mic: {'ON' if self.mic_on else 'OFF'}   Mic level {self.a_tx_pct}%   Volume {self.a_rx_pct}%"
            f"   mic path: {self.has_mic_path()}",
            f"radio link: {'connected' if self.radio.connected else 'not connected'} "
            f"({self.radio.units}); freq {snap.get('freq')}; mode {snap.get('mode')}; "
            f"transmitting {snap.get('ptt')}; power {snap.get('po_w')}; alc {snap.get('alc')}",
            "",
            "status text:",
            self.audio_status.get(),
            self.a_detail_text,
            "",
            "log (last 30 lines):",
            *log_lines,
        ]
        text = "\n".join(lines)
        return re.sub(r"\b(?!127\.0\.0\.1\b)\d{1,3}(?:\.\d{1,3}){3}\b", "<ip address>", text)

    def copy_report(self):
        text = self.build_report()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        path = Path.home() / "ft991a-panel-report.txt"
        try:
            path.write_text(text, encoding="utf-8")
            where = f" and saved as {path}"
        except OSError:
            where = ""
        self.append_log(f"Report copied to the clipboard{where}")

    def apply_geometry(self):
        r = self.root
        r.update_idletasks()
        m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", self.cfg.get("geometry2", "") or "")
        if m:
            sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
            gw = min(max(int(m.group(1)), 400, r.winfo_reqwidth()), sw)    # never clip the controls
            gh = min(max(int(m.group(2)), 360, r.winfo_reqheight()), sh)
            x = min(max(int(m.group(3)), 100 - gw), sw - 100)
            y = min(max(int(m.group(4)), 0), sh - 100)
            r.geometry(f"{gw}x{gh}{x:+d}{y:+d}")
        else:
            r.geometry(f"{r.winfo_reqwidth()}x{r.winfo_reqheight()}")
        r.minsize(400, 360)
        self.fit_window(shrink=True)

    # ---- helpers ------------------------------------------------------------------
    def append_log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 200:
            self.log.delete("1.0", "40.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def make_radio(self):
        cls = Radio if self.backend == "hamlib" else CatRadio
        return cls(lambda m: self.logq.put(m))

    def on_backend(self, _event=None):
        new = BACKENDS.get(self.backend_var.get(), "hamlib")
        if new == self.backend:
            return
        if self.radio.thread is not None and not self.radio.stop_evt.is_set():
            # connected: the type cannot change until you disconnect
            self.backend_var.set(next(k for k, v in BACKENDS.items() if v == self.backend))
            self.append_log("Disconnect before changing the connection type")
            return
        self.ports[self.backend] = self.port.get()
        self.backend = new
        self.radio = self.make_radio()
        self.cal = self.cal_sets[new]
        self.port.set(self.ports[new])
        self.update_cal_text()
        self.append_log("Connection type: " + self.backend_var.get())

    def toggle_cal(self):
        self.show_cal = not self.show_cal
        self.apply_cal_visibility()
        self.fit_window(shrink=True)
        self.save_cfg()

    def apply_cal_visibility(self):
        if self.show_cal:
            self.cal_frame.grid()
            self.cal_toggle.configure(text="Calibration \u25be")
        else:
            self.cal_frame.grid_remove()
            self.cal_toggle.configure(text="Calibration \u25b8")

    @staticmethod
    def fmt_freq(hz):
        return "---.---.---" if hz is None else f"{hz:,}".replace(",", ".")

    def set_vfo_highlight(self, sel, split=False):
        """Light blue behind the operating VFO (always A: CAT cannot operate on B). With Raw CAT
        the captions say what each VFO does: A is RX and TX normally; with split on, A is RX
        only and B is TX."""
        raw = self.radio.units == "raw"
        for which, (fr, cap, val) in self.vfo_frames.items():
            on = which == sel
            bg = SEL_BLUE if on else self.vfo_normal_bg
            for w in (fr, cap):
                w.configure(bg=bg)
            val.set_bg(bg)
            text = "VFO " + which
            if raw:
                if split:
                    text += "  (RX)" if which == "A" else "  (TX)"
                elif on:
                    text += "  (RX/TX)"
            cap.configure(text=text)

    def on_vfo_click(self, which):
        """VFO A is always the operating VFO, so clicking it does nothing. Clicking VFO B swaps
        the two VFOs, so B's frequency moves into A. Click B again to swap back."""
        if which == "A":
            return
        if not self.radio.can_vfo:
            self.append_log("Swapping the VFOs needs the Raw CAT connection")
            return
        if not self.can_send():
            return
        s = self.radio.snapshot()
        if s["ptt"]:
            self.append_log("Not swapping the VFOs while transmitting")
            return
        if s["freq_b"] is None:
            self.append_log("VFO B's frequency is not known yet")
            return
        self.radio.swap_vfo()
        self.append_log(f"VFOs swapped: A was {self.fmt_freq(s['freq'])}, "
                        f"B was {self.fmt_freq(s['freq_b'])}")

    def on_split(self):
        """Toggle split. Split on: receive on VFO A, transmit on VFO B."""
        if not self.radio.can_split:
            return
        if not self.can_send():
            return
        s = self.radio.snapshot()
        if s["ptt"]:
            self.append_log("Not changing split while transmitting")
            return
        if s["split"] is None:
            self.append_log("The split state is not known yet; try again in a moment")
            return
        on = not s["split"]
        if on and self.radio.units == "raw" and s["freq_b"] is None:
            self.append_log("VFO B's frequency is not known yet")
            return
        self.radio.set_split(on)
        if on:
            where = f" ({self.fmt_freq(s['freq_b'])})" if s["freq_b"] is not None else ""
            self.append_log(f"Split ON: receive on VFO A, transmit on VFO B{where}")
        else:
            self.append_log("Split OFF: transmit on VFO A")

    # ---- frequency: digit scrolling and typed entry ---------------------------------
    def on_freq_change(self, which, hz):
        """Called by a digit tuner with a new frequency. Returns True if it was accepted."""
        if not self.radio.connected:
            self.append_log("Not connected")
            return False
        if which == "B" and not self.radio.can_vfo:
            self.append_log("Changing VFO B needs the Raw CAT connection")
            return False
        if self.radio.snapshot()["ptt"]:
            self.append_log("Not changing the frequency while transmitting")
            return False
        self.freq_hold[which] = (hz, time.time() + 1.5)
        self._pending_freq[which] = hz
        if self._freq_job.get(which) is None:          # a burst of wheel clicks sends one command
            self._freq_job[which] = self.root.after(70, lambda w=which: self._send_freq(w))
        return True

    def _send_freq(self, which):
        self._freq_job[which] = None
        hz = self._pending_freq.pop(which, None)
        if hz is not None:
            self.radio.set_freq(which, hz)

    def on_freq_entry(self, which):
        text = self.entry_var.get()
        if not text.strip():
            return
        try:
            hz = parse_frequency(text)
        except ValueError as exc:
            self.append_log(str(exc))
            return
        if self.on_freq_change(which, hz):
            self.vfo_frames[which][2].set_value(hz)
            self.append_log(f"VFO {which} -> {self.fmt_freq(hz)}")
            self.entry_var.set("")

    def show_freq(self, which, hz):
        hold = self.freq_hold.get(which)
        if hold and time.time() < hold[1] and hz != hold[0]:
            return                                      # keep what you set until the radio confirms
        fd = self.vfo_frames[which][2]
        if fd.value != hz:
            fd.set_value(hz)

    # ---- push to talk ---------------------------------------------------------------
    def ptt_toggle(self, event=None):
        """One click (or one press of the space bar) starts transmitting, the next stops it."""
        if self.ptt_down:
            self.ptt_release()
        else:
            self.ptt_press()

    def bind_space_ptt(self):
        """The space bar toggles PTT from anywhere in the window. A space typed into a text box
        is left alone, key auto-repeat is ignored, and the space bar no longer 'presses' a
        button that happens to have the keyboard focus."""
        self._space_held, self._space_job = False, None
        r = self.root
        for cls in ("TButton", "TCheckbutton", "TRadiobutton", "TCombobox", "TScale", "TNotebook"):
            r.bind_class(cls, "<KeyPress-space>", self.on_space_press)     # replaces the default
            r.bind_class(cls, "<KeyRelease-space>", self.on_space_release)
        r.bind_all("<KeyPress-space>", self.on_space_press)
        r.bind_all("<KeyRelease-space>", self.on_space_release)

    TYPING_WIDGETS = ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "Listbox")

    def _space_for_ptt(self, event):
        w = event.widget
        try:
            if w.winfo_toplevel() is not self.root:
                return False                     # a dialog or a drop-down list, not this window
            return w.winfo_class() not in self.TYPING_WIDGETS
        except tk.TclError:
            return False

    def on_space_press(self, event):
        if not self._space_for_ptt(event):
            return None                          # typing, or another window: do nothing here
        if self._space_job is not None:          # X11 auto-repeat: a release, then a press at once
            self.root.after_cancel(self._space_job)
            self._space_job = None
            return "break"
        if self._space_held:                     # auto-repeat on Windows and macOS
            return "break"
        self._space_held = True
        self.ptt_toggle()
        return "break"

    def on_space_release(self, event):
        if not self._space_for_ptt(event) or not self._space_held:
            return None
        if self._space_job is not None:
            self.root.after_cancel(self._space_job)
        self._space_job = self.root.after(60, self._space_clear)    # a repeat would cancel this
        return "break"

    def _space_clear(self):
        self._space_held, self._space_job = False, None

    def ptt_press(self, event=None):
        """Start transmitting. PTT lets go when you toggle it off, on a time-out, when the
        connection drops and when the panel closes (and, if you ask for it, when the window loses
        focus)."""
        if self.ptt_down:
            return
        if not self.radio.connected:
            self.append_log("PTT: not connected to the radio")
            return
        if self.radio.snapshot().get("tuning") or self.tune_deadline:
            self.append_log("PTT: the antenna tuner is tuning")
            return
        if self.nr_cal.active:
            self.append_log("PTT: the noise calibration has the radio on another frequency; press "
                            "Cancel first")
            return
        self.ptt_down = True
        if self.audio_running() and self.has_mic_path() and not self.mic_on:
            self._ptt_auto_mic = True            # the microphone is live only while PTT is held
            self.mic_on = True
            self.mic_btn.configure(text="Mic: ON")
            self.apply_mic_gain()
        self.radio.set_ptt(True)
        self.ptt_lbl.configure(text=PTT_ON_TEXT, bg=RED, fg="white", relief="sunken")
        limit = float(self.cfg.get("ptt_timeout", PTT_TIMEOUT))
        self._ptt_job = self.root.after(
            int(limit * 1000), lambda: self.ptt_release(reason=f" (time-out after {limit:.0f} s)"))
        self.append_log("PTT on" + ("" if self.audio_running() else " (the audio is not running)"))

    def ptt_release(self, event=None, reason=""):
        if not self.ptt_down:
            return
        self.ptt_down = False
        if self._ptt_job is not None:
            self.root.after_cancel(self._ptt_job)
            self._ptt_job = None
        self.radio.set_ptt(False)
        self.root.after(150, lambda: self.radio.set_ptt(False))     # and once more, to be sure
        if self._ptt_auto_mic:
            self._ptt_auto_mic = False
            self.mic_on = False
            self.mic_btn.configure(text="Mic: OFF")
            self.apply_mic_gain()
        self.ptt_lbl.configure(text=PTT_IDLE_TEXT, bg=self.vfo_normal_bg, relief="raised")
        self.append_log("PTT off" + reason)

    def on_focus_out(self, event):
        """Clicking another window does NOT stop PTT. Set "ptt_release_on_focus_loss": true in
        the settings file if you would rather it let go when the window loses focus."""
        if (event.widget is self.root and self.ptt_down
                and self.cfg.get("ptt_release_on_focus_loss", False)):
            self.ptt_release(reason=" (the window lost focus)")

    def update_ptt(self):
        if self.ptt_down and not self.radio.connected:
            self.ptt_release(reason=" - connection lost; the radio's TX time-out (menu 036) "
                                    "will end the transmission")
        if not self.ptt_down:
            ready = self.radio.connected and self.radio.can_ptt
            self.ptt_lbl.configure(fg="#222222" if ready else "#999999",
                                   cursor="hand2" if ready else "")

    # ---- AGC --------------------------------------------------------------------------
    def on_agc(self, event=None):
        label = self.agc_var.get()
        code = self.radio.AGC_SET.get(label)
        if code is None or not self.radio.connected:
            return
        self.agc_hold_until = time.time() + 1.5
        self.radio.set_agc(code)
        self.append_log(f"AGC -> {label}")

    def update_agc(self, s, now):
        ok = self.radio.connected and s["agc"] is not None
        self.agc_cb.configure(state="readonly" if ok else "disabled")
        if not self.radio.connected:
            self.agc_var.set("")
        elif ok and now >= self.agc_hold_until:
            name = self.radio.AGC_NAME.get(s["agc"], "")
            if name and self.agc_var.get() != name:
                self.agc_var.set(name)

    # ---- C4FM / FM ------------------------------------------------------------------
    def on_c4fm(self):
        s = self.radio.snapshot()
        if s["freq"] is None or s["freq"] < 100_000_000:
            self.append_log("C4FM is only used on 144 and 430 MHz")
            return
        if not self.can_send():
            return
        if s["ptt"]:
            self.append_log("Not changing the mode while transmitting")
            return
        target = "FM" if s["mode"] == "C4FM" else "C4FM"
        self.pending_mode = (target, time.time())
        self.mode_var.set(LABEL_OF.get(target, target))
        self.radio.set_mode(target)
        self.append_log(f"Mode -> {LABEL_OF.get(target, target)}")

    # ---- bandwidth --------------------------------------------------------------------
    def on_bw_slide(self, value):
        if self._bw_updating or not self._bw_steps:
            return
        pos = max(0, min(len(self._bw_steps) - 1, int(round(float(value)))))
        hz = self._bw_steps[pos]
        self.bw_label.set(f"{hz} Hz")
        self.bw_hold_until = time.time() + 1.5
        self._bw_target = hz
        if self._bw_job is not None:
            self.root.after_cancel(self._bw_job)
        self._bw_job = self.root.after(180, self._send_bw)    # send when the slider settles

    def _send_bw(self):
        self._bw_job = None
        s = self.radio.snapshot()
        fam = bw_family(s["mode"], s["data_mode"])
        if fam is None or self._bw_target is None or not self.radio.connected:
            return
        cur = bool(s["narrow"])
        narrow, idx = bw_plan(fam, self._bw_target, cur)
        self.radio.set_bandwidth(narrow, idx, narrow != cur)
        self.append_log(f"Bandwidth -> {self._bw_target} Hz")

    def update_bandwidth(self, s, now):
        fam = bw_family(s["mode"], s["data_mode"]) if self.radio.can_bw else None
        if fam != self._bw_family:                       # a different mode: new range for the slider
            self._bw_family = fam
            self._bw_steps = bw_steps(fam) if fam else []
            self.bw_scale.configure(to=max(1, len(self._bw_steps) - 1))
        known = fam and self.radio.connected and s["bw_idx"] is not None and s["narrow"] is not None
        if not known:
            self.bw_scale.state(["disabled"])
            if not self.radio.can_bw:
                self.bw_label.set("Raw CAT only")
            elif not self.radio.connected or s["mode"] is None:
                self.bw_label.set("--")
            else:
                self.bw_label.set("fixed" if fam is None else "--")
            return
        self.bw_scale.state(["!disabled"])
        if now < self.bw_hold_until:
            return                                       # you are moving it: do not fight the poll
        hz = bw_hz(fam, s["narrow"], s["bw_idx"])
        if hz in self._bw_steps:
            self._bw_updating = True
            self.bw_scale.set(self._bw_steps.index(hz))
            self._bw_updating = False
        self.bw_label.set("--" if hz is None else f"{hz} Hz")

    def calibrate_s9(self):
        s = self.radio.snapshot()
        if self.radio.units != "raw":
            messagebox.showinfo("Calibrate", "With the Hamlib connection the S meter is "
                                "already calibrated by Hamlib.")
            return
        raw = s["s_raw"]
        if s["ptt"] or raw is None or raw <= 0:
            messagebox.showinfo("Calibrate", "Receive a signal you know to be S9 (read it on "
                                "the radio's own meter), then press Cal S9.")
            return
        self.s9_raw, self.s9_calibrated = raw, True
        self.save_cfg()
        self.append_log(f"S9 reference set to raw {raw}")

    def update_cal_text(self):
        self.cal_txt.set(f"corrections: PO {len(self.cal['po'])}, "
                         f"SWR {len(self.cal['swr'])}")

    def save_cfg(self):
        self.ports[self.backend] = self.port.get()
        data = dict(host=self.host.get(), backend=self.backend,
                    port_hamlib=self.ports["hamlib"], port_cat=self.ports["cat"],
                    geometry2=self.root.geometry(),
                    cal_po=self.cal_sets["hamlib"]["po"], cal_swr=self.cal_sets["hamlib"]["swr"],
                    cal_po_raw=self.cal_sets["cat"]["po"], cal_swr_raw=self.cal_sets["cat"]["swr"],
                    tune_confirm=self.cfg.get("tune_confirm", False),
                    ptt_timeout=self.cfg.get("ptt_timeout", PTT_TIMEOUT),
                    ptt_release_on_focus_loss=self.cfg.get("ptt_release_on_focus_loss", False),
                    show_cal=self.show_cal,
                    log_hidden=self.log_hidden,
                    band_memory=self.band_mem,
                    **(self.cw_window.settings() if hasattr(self, "cw_window") else {}),
                    s9_raw=self.s9_raw if self.s9_calibrated else None)
        if self.audio_ready:
            data.update(audio_mode=self.audio_mode.get(),
                        nr_on=self.nr_on.get(), nr_level=int(self.nr_level.get()),
                        audio_all_devices=self.audio_all_devices.get(),
                        audio_host=self.audio_host.get().strip(), audio_codec=self.audio_codec.get(),
                        audio_port=self.audio_port.get(), audio_jitter=self.audio_jitter.get(),
                        audio_rx_pct=self.a_rx_pct, audio_tx_pct=self.a_tx_pct,
                        audio_devices=self.a_want,
                        audio_adv=dict(rate=self.audio_rate.get(), frame=self.audio_frame.get(),
                                       pace=self.audio_pace.get(), stereo=self.audio_stereo.get(),
                                       bitrate=self.audio_bitrate.get(), bw=self.audio_bw.get(),
                                       signal=self.audio_signal.get(), fec=self.audio_fec.get(),
                                       loss=self.audio_loss.get(), show=self.a_show_adv))
        try:
            CFG_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    # ---- actions ------------------------------------------------------------------
    def toggle_connect(self):
        if self.radio.thread is not None and not self.radio.stop_evt.is_set():
            if self.ptt_down:
                self.ptt_release(reason=" (disconnecting)")
                time.sleep(0.3)                      # let the release reach the radio first
            self.radio.disconnect()
            self.conn_btn.configure(text="Connect")
            self.host_e.configure(state="normal")
            self.port_e.configure(state="normal")
            self.backend_cb.configure(state="readonly")
            return
        try:
            port = int(self.port.get())
        except ValueError:
            messagebox.showerror("Port", "The port must be a number.")
            return
        host = self.host.get().strip()
        if not host:
            messagebox.showerror("Host", "Enter the server's host name or IP address.")
            return
        self.radio.connect(host, port)
        self.conn_btn.configure(text="Disconnect")
        self.host_e.configure(state="disabled")
        self.port_e.configure(state="disabled")
        self.backend_cb.configure(state="disabled")
        self.save_cfg()

    def can_send(self):
        if not self.radio.connected:
            self.append_log("Not connected")
            return False
        return True

    def on_mode(self, _event=None):
        ham = HAM_OF.get(self.mode_var.get())
        if ham and self.can_send():
            self.pending_mode = (ham, time.time())
            self.radio.set_mode(ham)
            self.append_log(f"Mode -> {self.mode_var.get()}")

    def on_power(self, _event=None):
        try:
            watts = int(self.power_var.get().split()[0])
        except (ValueError, IndexError):
            return
        if self.can_send():
            self.pending_power = (watts, time.time())
            self.radio.set_power(watts)
            self.append_log(f"Power -> {watts} W")

    def on_tuner(self):
        """Toggle the internal ATU: ON puts it in line, OFF bypasses it."""
        s = self.radio.snapshot()
        if self.tune_deadline or s.get("tuning"):
            return                      # tuning in progress; use the Tune button
        if not self.can_send():
            return
        if s["tuner"] is None:
            self.append_log("The ATU state is not known yet; try again in a moment")
            return
        on = not s["tuner"]
        self.radio.set_tuner(on)
        self.append_log("ATU -> " + ("ON (in line)" if on else "OFF (bypassed)"))

    def on_tune(self):
        if not self.can_send():
            return
        if self.nr_cal.active:
            self.append_log("Tune: the noise calibration has the radio on another frequency; press "
                            "Cancel first")
            return
        if self.tune_deadline or self.radio.snapshot().get("tuning"):   # already tuning: stop
            self.radio.tune_stop()
            self.tune_deadline = 0.0
            self.append_log("Tuning stop requested")
            return
        if self.cfg.get("tune_confirm", False):        # off unless you turn it on in the settings file
            if not messagebox.askokcancel(
                    "Start tuning",
                    "The tuner transmits a carrier while it tunes.\n\nMake sure the antenna "
                    "(or dummy load) is connected and the frequency is clear.\n\n"
                    "Start tuning?"):
                return
        self.radio.tune_start()
        self.tune_deadline = time.time() + TUNE_TIMEOUT
        self.append_log("Tuning started")

    def on_power_switch(self, on):
        if not self.radio.can_power:
            self.append_log("Power control needs the Raw CAT connection")
            return
        if not self.radio.connected:
            self.append_log("Not connected")
            return
        if not on and self.radio.snapshot()["ptt"]:
            messagebox.showinfo("Power off", "The radio is transmitting. Stop transmitting first.")
            return
        word = "on" if on else "off"
        self.radio.power(on)
        self.append_log(f"Power {word.upper()} requested (dummy data, then PS{int(on)}; "
                        f"after {POWER_WAIT} s)")

    def calibrate(self, which):
        s = self.radio.snapshot()
        raw = self.radio.units == "raw"
        if raw:
            reading = s["po_raw"] if which == "po" else s["swr_raw"]
        else:
            reading = s["po_w"] if which == "po" else s["swr"]
        floor = 1.0 if (which == "swr" and not raw) else 0
        if not s["ptt"] or reading is None or reading <= floor:
            messagebox.showinfo("Calibrate", "Transmit first (for example into a dummy "
                                "load) so there is a reading to correct.")
            return
        unit = "power in watts" if which == "po" else "SWR (for example 1.5)"
        shown = f"the radio reports raw {reading}" if raw else f"Hamlib reports {reading:.2f}"
        val = simpledialog.askfloat("Calibrate", f"Actual {unit} when {shown}:",
                                    parent=self.root, minvalue=0.0)
        if val is None:
            return
        tol = 0.05 if (which == "swr" and not raw) else 0.5
        self.cal[which] = [p for p in self.cal[which] if abs(p[0] - reading) > tol] + [(reading, val)]
        self.update_cal_text()
        self.save_cfg()
        self.append_log(f"Correction point added: {which.upper()} {reading:g} -> {val}")

    def clear_cal(self):
        if messagebox.askyesno("Clear corrections", "Remove all correction points?"):
            self.cal["po"], self.cal["swr"] = [], []
            self.update_cal_text()
            self.save_cfg()

    def on_close(self):
        try:
            self.root.after_cancel(self._tick_id)
        except (AttributeError, tk.TclError):
            pass
        if self.nr_cal.active:
            self.nr_cal.abort("closing")
            time.sleep(0.5)                          # let the retune reach the radio first
        if self.ptt_down:
            self.ptt_release(reason=" (closing)")
            time.sleep(0.3)                          # let the release reach the radio first
        self.cw_window.shutdown()
        self.save_cfg()
        self.audio_stop() if self.audio_running() else None
        self.radio.disconnect()
        self.root.destroy()

    # ---- periodic update ------------------------------------------------------------
    def tick(self):
        """Runs ten times a second. If something in it fails, the panel says so once and keeps
        running: the PTT time-out, the meters and the frequency all depend on this loop."""
        try:
            self._tick()
        except Exception as exc:
            key = (type(exc).__name__, str(exc))
            if key not in self._tick_errors:
                self._tick_errors.add(key)
                self.append_log(f"Internal error (the panel keeps running): {key[0]}: {key[1]}")
                traceback.print_exc()
        finally:
            self._tick_id = self.root.after(100, self.tick)

    def _tick(self):
        try:
            while True:
                self.append_log(self.logq.get_nowait())
        except queue.Empty:
            pass
        now = time.time()
        s = self.radio.snapshot()
        connected = self.radio.connected
        fresh = connected and (now - s["last_rx"]) < STALE_AFTER
        if not self.radio.thread or self.radio.stop_evt.is_set():
            self.status.set("Not connected")
        elif not connected:
            self.status.set("Connecting...")
        elif not fresh:
            self.status.set("No reply (radio off?)" if self.radio.units == "raw"
                            else "No reply from radio")
        else:
            self.status.set("Connected")
        self.status_lbl.configure(foreground=GREEN if fresh else AMBER if connected else GREY)

        self.show_freq("A", s["freq"])
        self.show_freq("B", s["freq_b"])
        tx_vfo = s["vfo_sel"] or "A"                 # the VFO that transmits (CAT: FT)
        split = bool(s["split"])
        if (split, self.radio.units) != self._vfo_shown:
            self._vfo_shown = (split, self.radio.units)
            self.set_vfo_highlight("A", split)       # VFO A is the operating VFO
        tx_freq = s["freq_b"] if (tx_vfo == "B" and s["freq_b"] is not None) else s["freq"]
        if tx_freq is not None:
            hf = tx_freq <= 100_000_000
            if hf != self.band_hf:
                self.band_hf = hf
                self.power_cb.configure(values=[f"{p} W" for p in (POWERS_HF if hf else POWERS_VHF)])
        if s["mode"]:
            pm, pmt = self.pending_mode
            label = LABEL_OF.get(s["mode"], s["mode"])
            if not (pm and now - pmt < 1.5 and s["mode"] != pm) and self.mode_var.get() != label:
                self.mode_var.set(label)
        pw, pwt = self.pending_power
        if s["power_w"] is not None and not (pw and now - pwt < 1.5 and s["power_w"] != pw):
            text = f"{s['power_w']} W"
            if self.power_var.get() != text:
                self.power_var.set(text)

        transmitting = bool(s["ptt"])
        tuning = (bool(self.tune_deadline) and transmitting) or bool(s.get("tuning"))
        if not fresh:
            self.state_lbl.configure(text="--", bg=GREY)
        elif tuning:
            self.state_lbl.configure(text="TUNING", bg=AMBER)
        elif transmitting:
            self.state_lbl.configure(text="TX", bg=RED)
        else:
            self.state_lbl.configure(text="RX", bg=GREEN)

        if self.tune_deadline:
            started = self.tune_deadline - TUNE_TIMEOUT
            if not transmitting and now > started + 2.0:
                self.tune_deadline = 0.0                # tuning has finished by itself
            elif now > self.tune_deadline:
                self.tune_deadline = 0.0
                if transmitting:                        # still keyed after the time limit
                    self.radio.tune_stop()
                    self.append_log("Tuning timed out - stop sent")
        self.tune_btn.configure(
            text="Stop tune" if (self.tune_deadline or s.get("tuning")) else "Tune")
        if self.tune_deadline or s.get("tuning"):
            led = YELLOW if int(now * 3) % 2 else "#6b5a00"          # flashes while tuning
        else:
            led = GREY if s["tuner"] is None else YELLOW if s["tuner"] else GREEN
        if led != self._atu_led_color:
            self._atu_led_color = led
            self.atu_led.itemconfigure(self.atu_dot, fill=led)

        vhf = s["freq"] is not None and s["freq"] >= 100_000_000
        self.c4fm_btn.configure(
            text="C4FM: " + ("--" if s["mode"] is None else "ON" if s["mode"] == "C4FM" else "OFF"),
            state="normal" if (self.radio.connected and vhf) else "disabled")
        self.update_band(s)
        self.update_bandwidth(s, now)
        self.update_agc(s, now)
        self.update_ptt()
        self.update_audio(now)
        self.nr_cal.step()
        if self.nb.select() == str(self.function_tab):
            self.function_tab.refresh()
        self.cw_window.refresh()
        if self.nb.select() == str(self.settings_tab):
            self.settings_tab.refresh()
        self.split_btn.configure(
            text="Split: " + ("--" if s["split"] is None else "ON" if s["split"] else "OFF"),
            state="normal" if (self.radio.can_split and self.radio.connected) else "disabled")
        can = "normal" if (self.radio.can_power and self.radio.connected) else "disabled"
        self.pon_btn.configure(state=can)
        self.poff_btn.configure(state=can)
        self.update_meters(s, fresh and transmitting, now)

    @staticmethod
    def alc_fraction(s, raw_mode):
        """The ALC reading as a fraction of the radio's own meter scale (see ALC_SCALE)."""
        raw = ((s["alc_raw"] or 0) / 255) if raw_mode else (s["alc"] or 0.0)
        return min(1.0, raw * ALC_SCALE)

    def update_s_meter(self, s, now):
        rx_live = (self.radio.connected and not s["ptt"]
                   and (now - s["last_rx"]) < STALE_AFTER)
        if self.radio.units == "raw":
            raw = s["s_raw"]
            if rx_live and raw is not None:
                s9 = max(1, self.s9_raw)
                if raw <= s9:
                    label = f"S{9.0 * raw / s9:.0f}"
                else:
                    label = f"S9+{60.0 * (raw - s9) / max(1, 255 - s9):.0f} dB"
                self.bars["s"].set(raw / 255, BLUE if raw <= s9 else AMBER)
                # a "~" means the S scale is still approximate (press Cal S9 to fix it)
                self.texts["s"].set(f"{'' if self.s9_calibrated else '~'}{label} ({raw})")
                return
        else:
            db = s["s_db"]                      # Hamlib: dB relative to S9
            if rx_live and db is not None:
                label = f"S{max(0.0, 9 + db / 6):.0f}" if db <= 0 else f"S9+{db:.0f} dB"
                self.bars["s"].set((db + 54) / 114, BLUE if db <= 0 else AMBER)
                self.texts["s"].set(label)
                return
        self.bars["s"].set(0, GREY)
        self.texts["s"].set("--")

    def update_meters(self, s, live, now):
        self.update_s_meter(s, now)
        raw_mode = self.radio.units == "raw"
        full_scale = 100.0 if self.band_hf else 50.0
        # power out
        po_read = (s["po_raw"] if raw_mode else s["po_w"]) or 0
        true_po = interp(self.cal["po"], po_read, 0.0)
        watts = true_po if true_po is not None else (None if raw_mode else po_read)
        if live:
            frac = watts / full_scale if watts is not None else po_read / 255
            if frac >= self.po_peak or now - self.po_peak_t > 2.5:
                self.po_peak, self.po_peak_t = frac, now
            self.bars["po"].set(frac, BLUE, self.po_peak)
            if watts is None:
                self.texts["po"].set(f"raw {po_read} (uncal.)")
            elif true_po is None:
                self.texts["po"].set(f"{watts:.0f} W")
            else:
                src = f"raw {po_read}" if raw_mode else f"Hamlib {po_read:.0f}"
                self.texts["po"].set(f"{watts:.0f} W ({src})")
        else:
            if now - self.po_peak_t > 2.5:
                self.po_peak = 0.0
            self.bars["po"].set(0, GREY, self.po_peak if self.po_peak else None)
            self.texts["po"].set("--")
        # SWR
        swr_read = (s["swr_raw"] or 0) if raw_mode else (s["swr"] or 1.0)
        true_swr = interp(self.cal["swr"], swr_read, 1.0)
        ratio = true_swr if true_swr is not None else (None if raw_mode else swr_read)
        if live and ratio is None:
            self.bars["swr"].set(swr_read / 255, BLUE)
            self.texts["swr"].set(f"raw {swr_read} (uncal.)")
        elif live:
            color = RED if ratio >= 3.0 else AMBER if ratio >= 2.0 else BLUE
            self.bars["swr"].set(min(1.0, (ratio - 1.0) / 4.0), color)   # 1:1 .. 5:1 across the bar
            self.texts["swr"].set(f"{ratio:.1f} : 1")
        else:
            self.bars["swr"].set(0, GREY)
            self.texts["swr"].set("--")
        # ALC
        alc = self.alc_fraction(s, raw_mode)
        if live:
            shown = round(100 * alc)                # the colour follows the number that is shown
            color = RED if shown > 100 * ALC_BAD else AMBER if shown > 100 * ALC_WARN else GREEN
            self.bars["alc"].set(alc, color)
            self.texts["alc"].set(f"{100 * alc:.0f}%")
        else:
            self.bars["alc"].set(0, GREY)
            self.texts["alc"].set("--")


def set_window_icon(root):
    """Show the panel's icon on the window (the Dock on a Mac). A packaged app already has its
    icon built in; this is for running the script directly from the repository."""
    for d in _app_dirs():
        path = os.path.join(d, "packaging", "icons", "ft991a_128.png")
        if os.path.isfile(path):
            try:
                root.iconphoto(True, tk.PhotoImage(file=path))
            except tk.TclError:
                pass
            return


LICENSE_FILES = ("LICENSE", "NOTICE.md", "THIRD-PARTY-LICENSES.txt")
LICENSE_NOTICE = """{name} {version}
Copyright (C) 2026  Charles W. Powell, NK8O

This program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
License for more details.

You should have received a copy of the GNU General Public License along with this program. If not, see
<https://www.gnu.org/licenses/>.
"""


def license_dir():
    """The folder with LICENSE, NOTICE.md and THIRD-PARTY-LICENSES.txt: inside a packaged program (the
    build puts them in a "licenses" folder, which in a Mac app is in Contents/Resources), or beside this
    file when the program is run from its source. None if they are not there."""
    here = Path(__file__).resolve().parent
    bases = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        bases += [Path(bundle) / "licenses", Path(bundle).parent / "Resources" / "licenses"]
    bases += [Path(sys.executable).resolve().parent / "licenses", here, here / "licenses"]
    for d in bases:
        if all((d / name).is_file() for name in LICENSE_FILES):
            return d
    return None


LICENSE_PARTS = {"full": "LICENSE", "credits": "NOTICE.md", "third-party": "THIRD-PARTY-LICENSES.txt"}
LICENSE_HOME = "https://github.com/cwpowell/FT-991a-Companion"


def show_license(what="notice"):
    """What --license prints: the GPL notice, or one of the three texts in full."""
    d = license_dir()
    if what != "notice":
        if d is None:
            print(f"The licence files were not found next to the program. They are in {LICENSE_HOME}")
            return 1
        print((d / LICENSE_PARTS[what]).read_text(encoding="utf-8"))
        return 0
    print(LICENSE_NOTICE.format(name=APP_NAME, version=__version__))
    print("More:  --license full         the GNU General Public License, version 3")
    print("       --license credits      credits, and where the source code is")
    print("       --license third-party  the licences of the software that is included")
    print("       --save-license FOLDER  copy the three files into a folder")
    print(f"Source code: {LICENSE_HOME}")
    return 0


def save_license(folder):
    """Copy LICENSE, NOTICE.md and THIRD-PARTY-LICENSES.txt into a folder (a packaged program keeps
    them inside itself, and a Windows program has no console to print them on)."""
    d = license_dir()
    if d is None:
        print(f"The licence files were not found next to the program. They are in {LICENSE_HOME}")
        return 1
    import shutil
    target = Path(folder).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    for name in LICENSE_FILES:
        shutil.copyfile(d / name, target / name)
    print(f"Copied {', '.join(LICENSE_FILES)} to {target}")
    return 0


def run_selftest(path):
    """Write a short report of what this (packaged) program can see: Tk, the sound library and
    the Opus library. A build runs it to prove that everything was really bundled. Returns the
    exit code: 1 if anything failed."""
    rows = []

    def check(name, fn):
        try:
            rows.append(("OK  ", name, str(fn())))
        except Exception as exc:
            rows.append(("FAIL", name, f"{type(exc).__name__}: {exc}"))

    def need(ok, why):
        if not ok:
            raise RuntimeError(why)

    check("panel version", lambda: __version__)
    check("python", lambda: f"{sys.version.split()[0]} on {sys.platform}, "
                            f"packaged={bool(getattr(sys, 'frozen', False))}")
    check("tkinter and Tcl", lambda: "Tcl " + tk.Tcl().eval("info patchlevel"))

    def sound():
        need(AUDIO_OK, AUDIO_ERR)
        return f"sounddevice {sd.__version__}, {sd.get_portaudio_version()[1]}"

    def opus():
        need(HAVE_OPUS, OPUS_ERR)
        version = opuslib.api.info.get_version_string()
        if isinstance(version, bytes):
            version = version.decode("ascii", "replace")
        return f"{version} (shipped with the program: {_bundled_opus or 'no, using the system copy'})"

    check("sounddevice and PortAudio", sound)
    check("Opus library", opus)

    def numpy_ok():
        need(HAVE_NUMPY, "numpy is not installed, so noise reduction is off")
        nr = SpectralNR(48000)                      # a real pass through the FFT, not just the import
        nr.enabled = True
        for _ in range(4):
            out = nr.process(bytes(960))
        need(len(out) == 960, "the noise reduction returned the wrong amount of audio")
        return f"numpy {np.__version__}, noise reduction ran"

    check("numpy (noise reduction)", numpy_ok)

    def licences():
        d = license_dir()
        if d:
            return f"{', '.join(LICENSE_FILES)} in {d}"
        need(not getattr(sys, "frozen", False),
             "the licence files (LICENSE, NOTICE.md, THIRD-PARTY-LICENSES.txt) are not inside the "
             "packaged program")
        return "not next to this file (fine when it is run on its own)"

    check("licence files", licences)
    text = "\n".join(f"{status} {name}: {detail}" for status, name, detail in rows) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(text, end="")
    return 1 if any(status == "FAIL" for status, _, _ in rows) else 0


def main():
    ap = argparse.ArgumentParser(description="FT-991A remote panel")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    ap.add_argument("--host", help="server host (connects immediately)")
    ap.add_argument("--port", type=int, help="TCP port: rigctld (default 4532) or the ser2net port")
    ap.add_argument("--backend", choices=("hamlib", "cat"),
                    help="hamlib = rigctld (default); cat = raw Yaesu CAT through ser2net")
    ap.add_argument("--license", nargs="?", const="notice", choices=("notice", "full", "credits", "third-party"),
                    help="show the licence (GNU GPL version 3 or later) and exit; 'full' prints the whole "
                         "GPL, 'credits' the notices, 'third-party' the licences of the included software")
    ap.add_argument("--save-license", metavar="FOLDER",
                    help="copy the licence files (LICENSE, NOTICE.md, THIRD-PARTY-LICENSES.txt) into FOLDER and exit")
    ap.add_argument("--selftest", metavar="FILE",
                    help="check that Tk, the sound library and Opus are present, write the "
                         "report to FILE and exit (used by the installer builds)")
    args = ap.parse_args()
    if args.license:
        return show_license(args.license)
    if args.save_license:
        return save_license(args.save_license)
    if args.selftest:
        return run_selftest(args.selftest)
    root = tk.Tk()
    set_window_icon(root)

    def show_error(exc_type, exc, tb):
        import traceback
        traceback.print_exception(exc_type, exc, tb)
        messagebox.showerror("Unexpected error", f"{exc_type.__name__}: {exc}")

    root.report_callback_exception = show_error
    Panel(root, args)
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
