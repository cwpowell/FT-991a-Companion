#!/usr/bin/env python3
"""
ft991a_panel.py - remote status and control panel for a Yaesu FT-991A.

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
  - a push-to-talk button: one click (or one press of the space bar) to transmit, again to stop,
    with release safeguards and a time-out
  - Mic level and Volume sliders, the same controls as on the Audio tab
  - an Audio tab with FreeDV-style routing: radio to computer, computer to speaker, mic to
    computer, computer to radio. Local radio: the radio's sound card is on this computer.
    Remote radio: the bridge on the server connects to the same radio-side endpoints (virtual
    cables such as BlackHole, or direct to the speakers and microphone). Volume and Mic level
    sliders and a Mic on/off button. The audio engine is built in (the client side of freedv_audio_bridge.py,
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
opuslib for Opus).   python3 ft991a_panel.py [--host H --port P]

Meter values come from Hamlib's own calibration tables. If your wattmeter disagrees, use
"Cal PO" / "Cal SWR" while transmitting into a dummy load to add correction points.
"""
import argparse
import json
import math
import queue
import re
import socket
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

# ======================================================================================
# AUDIO ENGINE, built in. This is the client side of freedv_audio_bridge.py (the same engine
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

CFG_FILE = Path.home() / ".ft991a_panel.json"

# (label shown, Hamlib mode name). DATA-USB is what FreeDV uses (Hamlib: PKTUSB).
MODES = [("DATA-USB", "PKTUSB"), ("USB", "USB"), ("LSB", "LSB"), ("DATA-LSB", "PKTLSB"),
         ("CW-U", "CW"), ("CW-L", "CWR"), ("AM", "AM"), ("AM-N", "AMN"), ("FM", "FM"),
         ("FM-N", "FMN"), ("DATA-FM", "FM-D"), ("RTTY-LSB", "RTTY"), ("RTTY-USB", "RTTYR"),
         ("C4FM", "C4FM")]
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

ALC_WARN, ALC_BAD = 0.05, 0.25    # ALC meter fraction (0-1); adjust to taste
BLUE, GREEN, AMBER, RED, GREY = "#2a7fd4", "#1a8f3c", "#d98a00", "#c62828", "#9a9a9a"
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
    try:
        return json.loads(CFG_FILE.read_text())
    except Exception:
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
                elif c == "EX" and len(msg) == 6 and msg[2:5] == "062":
                    st["data_mode"] = msg[5]                   # menu 062 DATA MODE: 0 PSK, 1 OTHER
                elif c == "GT" and len(msg) == 4 and msg[3].isdigit():
                    st["agc"] = int(msg[3])
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


class MeterBar(tk.Canvas):
    def __init__(self, master, width=430, height=13):
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
            self.create_line(x, h - 4, x, h, fill="#777")
        if self.peak is not None and self.peak > 0:
            x = min(w - 2, w * min(1.0, self.peak))
            self.create_line(x, 0, x, h, fill="#222", width=2)


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
        root.title("FT-991A Remote Panel")
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
        self._vfo_shown = None
        self.status = tk.StringVar(value="Not connected")
        self.mode_var = tk.StringVar()
        self.power_var = tk.StringVar()
        self.po_peak, self.po_peak_t = 0.0, 0.0
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
        outer.rowconfigure(2, weight=1)
        self.nb = ttk.Notebook(outer)               # tabs: Radio, Audio
        self.nb.grid(row=0, column=0, sticky="ew")
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
        ttk.Label(ef, text="Go to:").grid(row=0, column=0)
        self.entry_var = tk.StringVar()
        self.freq_entry = ttk.Entry(ef, textvariable=self.entry_var, width=11)
        self.freq_entry.grid(row=0, column=1, padx=3)
        self.freq_entry.bind("<Return>", lambda e: self.on_freq_entry("A"))
        ttk.Button(ef, text="Set A", width=5, command=lambda: self.on_freq_entry("A")).grid(
            row=0, column=2, padx=1)
        ttk.Button(ef, text="Set B", width=5, command=lambda: self.on_freq_entry("B")).grid(
            row=0, column=3, padx=1)
        ttk.Label(ef, text="14.074  7074k  145.5m", foreground="#666666").grid(
            row=0, column=4, padx=6)

        # ---- push to talk: click once to transmit, click again to stop (the space bar does too)
        self.ptt_lbl = tk.Label(main, text=PTT_IDLE_TEXT, relief="raised", bd=3,
                                padx=6, pady=7, font=("Helvetica", 13, "bold"), fg="#999999",
                                bg=self.vfo_normal_bg)
        self.ptt_lbl.grid(row=2, column=0, sticky="ew", pady=(4, 2))
        self.ptt_lbl.bind("<ButtonPress-1>", self.ptt_toggle)
        self.bind_space_ptt()
        r.bind("<FocusOut>", self.on_focus_out)

        # ---- controls: mode, C4FM, power on the first row; ATU, tune, split, radio power below
        f = ttk.Frame(main)
        f.grid(row=3, column=0, sticky="ew", pady=4)
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
        self.tuner_btn = ttk.Button(g, text="ATU: --", width=9, command=self.on_tuner)
        self.tuner_btn.grid(row=0, column=0, padx=(0, 3))
        self.tune_btn = ttk.Button(g, text="Tune", width=6, command=self.on_tune)
        self.tune_btn.grid(row=0, column=1, padx=3)
        self.split_btn = ttk.Button(g, text="Split: --", width=9, command=self.on_split)
        self.split_btn.grid(row=0, column=2, padx=3)
        self.pon_btn = ttk.Button(g, text="Power on", width=8,
                                  command=lambda: self.on_power_switch(True))
        self.pon_btn.grid(row=0, column=3, padx=(10, 3))
        self.poff_btn = ttk.Button(g, text="Power off", width=8,
                                   command=lambda: self.on_power_switch(False))
        self.poff_btn.grid(row=0, column=4, padx=3)

        # ---- bandwidth
        bf = ttk.Frame(main)
        bf.grid(row=4, column=0, sticky="ew", pady=(0, 2))
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

        # ---- meters
        m = ttk.LabelFrame(main, text="Meters", padding=(6, 3))
        m.grid(row=5, column=0, sticky="ew", pady=4)
        m.columnconfigure(1, weight=1)
        self.bars, self.texts = {}, {}
        for i, (key, label) in enumerate((("s", "S meter"), ("po", "Power"),
                                          ("swr", "SWR"), ("alc", "ALC"))):
            ttk.Label(m, text=label + ":").grid(row=i, column=0, sticky="e", pady=1)
            bar = MeterBar(m, width=170)
            bar.grid(row=i, column=1, sticky="ew", padx=6)
            txt = tk.StringVar(value="--")
            ttk.Label(m, textvariable=txt, width=17).grid(row=i, column=2, sticky="w")
            self.bars[key], self.texts[key] = bar, txt
        # calibration: a small toggle; its buttons appear on the line below when wanted
        self.cal_toggle = ttk.Button(m, text="Calibration", width=12, command=self.toggle_cal)
        self.cal_toggle.grid(row=4, column=0, columnspan=2, sticky="w", pady=(3, 0))
        b = ttk.Frame(m)
        self.cal_frame = b
        b.grid(row=5, column=0, columnspan=3, sticky="w", pady=(3, 0))
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

        # ---- log
        ttk.Label(outer, text="Log").grid(row=1, column=0, sticky="w", pady=(4, 0))
        box = ttk.Frame(outer)
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.log = tk.Text(box, height=5, width=32, state="disabled", wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        self.update_cal_text()

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
        self.a_txdiag = {"mic": 0, "sent": 0}               # transmit peaks over the last second
        self.a_tick, self.a_was_linked = 0, False
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

        ttk.Label(tab, text="Transmit", font=bold).grid(row=8, column=0, columnspan=3,
                                                        sticky="w", pady=(8, 0))
        ttk.Label(tab, text="Mic to computer:").grid(row=9, column=0, sticky="w")
        self.a_mic_cb = ttk.Combobox(tab, textvariable=self.a_mic, width=21, state="readonly")
        self.a_mic_cb.grid(row=9, column=1, sticky="ew", padx=4, pady=1)
        self.a_mic_cb.bind("<<ComboboxSelected>>", lambda e: self.on_audio_dev_pick("mic"))
        ttk.Button(tab, text="Rescan", width=7, command=self.refresh_audio_devices).grid(
            row=9, column=2)
        ttk.Label(tab, text="Computer to radio:").grid(row=10, column=0, sticky="w")
        self.a_radio_out_cb = ttk.Combobox(tab, textvariable=self.a_radio_out, width=21,
                                           state="readonly")
        self.a_radio_out_cb.grid(row=10, column=1, sticky="ew", padx=4, pady=1)
        self.a_radio_out_cb.bind("<<ComboboxSelected>>",
                                 lambda e: self.on_audio_dev_pick("radio_out"))
        self.a_all_ck = ttk.Checkbutton(tab, text="Show all", variable=self.audio_all_devices,
                                        command=self.apply_audio_devices)
        self.a_all_ck.grid(row=10, column=2)
        ttk.Label(tab, text="Mic level:").grid(row=11, column=0, sticky="w")
        self.a_tx_scale = ttk.Scale(tab, from_=0, to=200, orient="horizontal",
                                    variable=self.tx_var, command=self.on_audio_tx_slide)
        self.a_tx_scale.grid(row=11, column=1, sticky="ew", padx=4)
        ttk.Label(tab, textvariable=self.a_tx_lbl, width=6).grid(row=11, column=2, sticky="w")
        ttk.Label(tab, text="Level:").grid(row=12, column=0, sticky="w")
        self.a_tx_bar = MeterBar(tab, width=200)
        self.a_tx_bar.grid(row=12, column=1, sticky="ew", padx=4, pady=1)
        self.mic_btn = ttk.Button(tab, text="Mic: OFF", width=10, command=self.toggle_mic)
        self.mic_btn.grid(row=13, column=0, sticky="w", pady=(3, 0))
        ttk.Label(tab, text="The microphone is sent only while Mic is ON, or while PTT is on.",
                  foreground="#666666", wraplength=250, justify="left").grid(
            row=13, column=1, columnspan=2, sticky="w", padx=4, pady=(3, 0))

        ttk.Separator(tab).grid(row=14, column=0, columnspan=3, sticky="ew", pady=6)
        g = ttk.Frame(tab)
        g.grid(row=15, column=0, columnspan=3, sticky="w", pady=(0, 2))
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
        h.grid(row=16, column=0, columnspan=3, sticky="w", pady=(2, 2))
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
            row=17, column=0, columnspan=3, sticky="w", pady=(6, 0))
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
        r = self.root                       # grow the window to fit when the options open
        r.update_idletasks()
        if r.winfo_reqheight() > r.winfo_height():
            r.geometry(f"{r.winfo_width()}x{r.winfo_reqheight()}")
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
        self.a_tx_cable = tx_cable
        self.mic_on = False
        self.mic_btn.configure(text="Mic: OFF")
        for key in ("radio_in", "speakers", "mic", "radio_out"):      # remember what worked
            self.on_audio_dev_pick(key)
        self.a_btn.configure(text="Stop audio")
        self.a_was_linked = False
        self.audio_status.set("Connecting..." if remote else "Local audio running.")
        self.update_adv_states()
        where = f"to {host}:{port}" if remote else "locally"
        self.append_log(f"Audio started {where} ("
                        + ("microphone muted" if self.has_mic_path() else "listen only") + ")")

    def audio_stop(self):
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
            self.a_rx_bar.set(0, GREY)
            self.a_tx_bar.set(0, GREY)
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
        d, self.a_txdiag = self.a_txdiag, {"mic": 0, "sent": 0}
        pct = lambda v: f"{v * 100 // 32768}%"
        if txr is None:
            if node is not None and not node.args.rx_only:       # only a program on the cable (FreeDV)
                return f"Transmit: audio from the radio cable sent to the server {pct(d['sent'])}."
            return ""
        parts = [f"microphone {pct(d['mic'])} ({'Mic ON' if self.mic_on else 'Mic muted'})",
                 (f"sent to the server {pct(d['sent'])}" if node is not None
                  else f"to the radio {pct(d['sent'])}")]
        hint = ""
        if self.mic_on:
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
        self.a_rx_bar.set(self.a_disp["rx"], BLUE if self.a_disp["rx"] < 0.9 else AMBER)
        self.a_tx_bar.set(self.a_disp["tx"], GREEN if (self.mic_on or txr is None) else GREY)
        self.a_tick += 1
        if self.a_tick % 10:                            # the text below changes about once a second
            return
        lines = []
        if node is not None:
            st = node.stats()
            if st["linked"] != self.a_was_linked:
                self.a_was_linked = st["linked"]
                self.append_log("Audio link up" if st["linked"] else "Audio link lost")
            lines.append(("Connected to the server." if st["linked"] else "No data from the server.")
                         + f"\nRX {st['rx']} packets; lost {st['lost']} ({st['loss_events']} events, "
                         f"longest {st['max_gap']}); late {st['late']}; bad {st['bad']}; underruns "
                         f"{st['underruns']}; dropped {st['dropped']}; buffer {st['buf']} frames.\n"
                         f"TX {st['tx_kbps']:.0f} kbps ({st['tx_pkts']} packets, errors "
                         f"{st['tx_err']}); send timing {st['tx_tight']} too close, {st['tx_slow']} "
                         f"too late, longest gap {st['tx_gap_max']:.0f} ms.")
        else:
            lines.append("Local audio running.")
        for r in (rxr, txr):
            if r is not None:
                lines.append(f"{r.name.capitalize()} path: underruns {r.underruns}, dropped "
                             f"{r.dropped}.")
        lines.append(self.receive_diagnosis())
        tx = self.transmit_diagnosis()
        if tx:
            lines.append(tx)
        self.audio_status.set("\n".join(lines))

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
                    confirm_tune=self.cfg.get("confirm_tune", True),
                    ptt_timeout=self.cfg.get("ptt_timeout", PTT_TIMEOUT),
                    ptt_release_on_focus_loss=self.cfg.get("ptt_release_on_focus_loss", False),
                    show_cal=self.show_cal,
                    s9_raw=self.s9_raw if self.s9_calibrated else None)
        if self.audio_ready:
            data.update(audio_mode=self.audio_mode.get(),
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
        if self.tune_deadline or self.radio.snapshot().get("tuning"):   # already tuning: stop
            self.radio.tune_stop()
            self.tune_deadline = 0.0
            self.append_log("Tuning stop requested")
            return
        if self.cfg.get("confirm_tune", True):
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
        if self.ptt_down:
            self.ptt_release(reason=" (closing)")
            time.sleep(0.3)                          # let the release reach the radio first
        self.save_cfg()
        self.audio_stop() if self.audio_running() else None
        self.radio.disconnect()
        self.root.destroy()

    # ---- periodic update ------------------------------------------------------------
    def tick(self):
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
            atu = "TUNING"
        else:
            atu = "--" if s["tuner"] is None else "ON" if s["tuner"] else "OFF"
        self.tuner_btn.configure(text="ATU: " + atu)

        vhf = s["freq"] is not None and s["freq"] >= 100_000_000
        self.c4fm_btn.configure(
            text="C4FM: " + ("--" if s["mode"] is None else "ON" if s["mode"] == "C4FM" else "OFF"),
            state="normal" if (self.radio.connected and vhf) else "disabled")
        self.update_bandwidth(s, now)
        self.update_agc(s, now)
        self.update_ptt()
        self.update_audio(now)
        self.split_btn.configure(
            text="Split: " + ("--" if s["split"] is None else "ON" if s["split"] else "OFF"),
            state="normal" if (self.radio.can_split and self.radio.connected) else "disabled")
        can = "normal" if (self.radio.can_power and self.radio.connected) else "disabled"
        self.pon_btn.configure(state=can)
        self.poff_btn.configure(state=can)
        self.update_meters(s, fresh and transmitting, now)
        self._tick_id = self.root.after(100, self.tick)

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
        alc = ((s["alc_raw"] or 0) / 255) if raw_mode else (s["alc"] or 0.0)
        if live:
            color = RED if alc >= ALC_BAD else AMBER if alc >= ALC_WARN else GREEN
            self.bars["alc"].set(alc, color)
            self.texts["alc"].set(f"{100 * alc:.0f}%")
        else:
            self.bars["alc"].set(0, GREY)
            self.texts["alc"].set("--")


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
    text = "\n".join(f"{status} {name}: {detail}" for status, name, detail in rows) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(text, end="")
    return 1 if any(status == "FAIL" for status, _, _ in rows) else 0


def main():
    ap = argparse.ArgumentParser(description="FT-991A remote panel")
    ap.add_argument("--host", help="server host (connects immediately)")
    ap.add_argument("--port", type=int, help="TCP port: rigctld (default 4532) or the ser2net port")
    ap.add_argument("--backend", choices=("hamlib", "cat"),
                    help="hamlib = rigctld (default); cat = raw Yaesu CAT through ser2net")
    ap.add_argument("--selftest", metavar="FILE",
                    help="check that Tk, the sound library and Opus are present, write the "
                         "report to FILE and exit (used by the installer builds)")
    args = ap.parse_args()
    if args.selftest:
        return run_selftest(args.selftest)
    root = tk.Tk()

    def show_error(exc_type, exc, tb):
        import traceback
        traceback.print_exception(exc_type, exc, tb)
        messagebox.showerror("Unexpected error", f"{exc_type.__name__}: {exc}")

    root.report_callback_exception = show_error
    Panel(root, args)
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
