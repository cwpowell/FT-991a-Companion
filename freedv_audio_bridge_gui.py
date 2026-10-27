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
freedv_audio_bridge_gui.py - graphical front end for freedv_audio_bridge.py.

Run it on the radio computer in Server mode, and on the FreeDV computer in
Client mode. Keep this file, freedv_audio_bridge.py and (on Windows) opus.dll
in the same folder. Settings are remembered in ~/.freedv_audio_bridge.json.

Requires:  pip install sounddevice opuslib
"""
import json
import math
import queue
import re
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

import sounddevice as sd

import freedv_audio_bridge as fb

CFG_FILE = Path.home() / ".freedv_audio_bridge.json"
RATES = [str(r) for r in fb.OPUS_RATES]
FRAMES = [str(f) for f in fb.OPUS_FRAME_MS]
BW_CHOICES = {"narrowband (4 kHz)": "nb", "wideband (8 kHz)": "wb",
              "super-wide (12 kHz)": "swb", "full (20 kHz)": "fb"}
GREEN, AMBER, GREY = "#1a8f3c", "#c77700", "#777777"

LABELS = {
    "server": ("Capture - radio RX audio:", "Playback - radio TX audio input:"),
    "client": ("Capture - FreeDV TX (virtual cable B):",
               "Playback - RX audio to FreeDV (virtual cable A):"),
}


def level_pct(peak):
    """Map a 16-bit peak value to 0..100 over a -60..0 dBFS scale."""
    if peak <= 0:
        return 0.0
    db = 20 * math.log10(min(peak, 32768) / 32768)
    return max(0.0, min(100.0, (db + 60) / 60 * 100))


def load_cfg():
    try:
        return json.loads(CFG_FILE.read_text())
    except Exception:
        return {}


class DeviceMissing(ValueError):
    """A saved audio device is not currently present."""


class App:
    def __init__(self, root):
        self.root = root
        root.title("FreeDV Audio Bridge")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.node = None
        self.logq = queue.SimpleQueue()
        self.tick_n = 0
        self.was_linked = False
        self.in_disp = self.out_disp = 0.0

        cfg = self.cfg = load_cfg()
        self.dev_cfg = cfg.get("devices", {})
        self.want = {"in": None, "out": None}     # the devices you MEAN to use (saved)
        self.missing = {"in": "", "out": ""}      # placeholder text for each missing device
        self.start_pending = False                # Start pressed while a device was missing
        self._last_scan = 0.0
        self.in_map, self.out_map, self.dev_key = {}, {}, {}

        sv, bv = tk.StringVar, tk.BooleanVar
        self.mode = sv(value=cfg.get("mode", "client"))
        self.prev_mode = self.mode.get()
        self.host = sv(value=cfg.get("host", ""))
        self.port = sv(value=str(cfg.get("port", fb.DEFAULTS["port"])))
        self.bind = sv(value=cfg.get("bind", fb.DEFAULTS["bind"]))
        self.in_dev, self.out_dev = sv(), sv()
        default_codec = "opus" if fb.HAVE_OPUS else "pcm"
        self.codec = sv(value=cfg.get("codec", default_codec))
        if not fb.HAVE_OPUS:
            self.codec.set("pcm")
        self.rate = sv(value=str(cfg.get("rate", fb.DEFAULTS["rate"])))
        self.frame = sv(value=str(cfg.get("frame_ms", fb.DEFAULTS["frame_ms"])))
        self.bitrate = sv(value=str(cfg.get("bitrate_kbps", 64)))
        bw_saved = cfg.get("opus_bw", "nb")
        self.opus_bw = sv(value=next((k for k, v in BW_CHOICES.items() if v == bw_saved),
                                     "narrowband (4 kHz)"))
        self.opus_signal = sv(value=cfg.get("opus_signal", "music"))
        self.complexity = sv(value=str(cfg.get("complexity", fb.DEFAULTS["complexity"])))
        self.loss = sv(value=str(cfg.get("loss", fb.DEFAULTS["loss"])))
        self.jitter = sv(value=str(cfg.get("jitter_ms", fb.DEFAULTS["jitter_ms"])))
        self.cbr = bv(value=cfg.get("cbr", False))
        self.fec = bv(value=cfg.get("fec", False))
        self.out_ch = sv(value=str(cfg.get("out_channels", 2)))
        self.gain_db = {"in": tk.DoubleVar(value=float(cfg.get("in_gain_db", 0.0))),
                        "out": tk.DoubleVar(value=float(cfg.get("out_gain_db", 0.0)))}
        self.pace = bv(value=cfg.get("pace", True))
        self.pin_vol = bv(value=cfg.get("pin_volumes", False))
        self.pinner = None
        self.debug = bv(value=False)  # deliberately not remembered between runs

        self.build()
        self.apply_geometry()
        self.refresh_devices(reinit=False)
        self.restore_devices(self.mode.get())     # saved devices, even if not present now
        self.on_mode_change(initial=True)
        if not fb.HAVE_OPUS:
            self.append_log(f"Opus unavailable ({fb.OPUS_ERR}) - PCM only. Put "
                            "opus.dll next to this script (Windows) or install libopus.")
        self.tick()

    # ---- UI construction -------------------------------------------------
    def build(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(0, weight=1)
        main = ttk.Frame(r, padding=10)
        main.grid(sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)
        self.cfg_widgets = []  # (widget, normal_state, group)

        def reg(w, normal="normal", group="always"):
            self.cfg_widgets.append((w, normal, group))
            return w

        # mode
        f = ttk.LabelFrame(main, text="Mode", padding=6)
        f.grid(row=0, column=0, sticky="ew", pady=3)
        for i, (txt, val) in enumerate((("Server (at the radio)", "server"),
                                        ("Client (where FreeDV runs)", "client"))):
            reg(ttk.Radiobutton(f, text=txt, value=val, variable=self.mode,
                                command=self.on_mode_change)).grid(
                row=0, column=i, padx=10)

        # network
        f = ttk.LabelFrame(main, text="Network", padding=6)
        f.grid(row=1, column=0, sticky="ew", pady=3)
        ttk.Label(f, text="Server host/IP:").grid(row=0, column=0, sticky="e")
        reg(ttk.Entry(f, textvariable=self.host, width=22), group="client").grid(
            row=0, column=1, padx=6, pady=2)
        ttk.Label(f, text="UDP port:").grid(row=0, column=2, sticky="e")
        reg(ttk.Entry(f, textvariable=self.port, width=7)).grid(row=0, column=3, padx=6)
        ttk.Label(f, text="Bind address:").grid(row=1, column=0, sticky="e")
        reg(ttk.Entry(f, textvariable=self.bind, width=22), group="server").grid(
            row=1, column=1, padx=6, pady=2)

        # devices
        f = ttk.LabelFrame(main, text="Audio devices", padding=6)
        f.grid(row=2, column=0, sticky="ew", pady=3)
        f.columnconfigure(1, weight=1)
        self.lbl_in = ttk.Label(f)
        self.lbl_in.grid(row=0, column=0, sticky="e")
        self.cb_in = reg(ttk.Combobox(f, textvariable=self.in_dev, width=58,
                                      state="readonly"), "readonly")
        self.cb_in.grid(row=0, column=1, sticky="ew", padx=6, pady=2)
        self.lbl_out = ttk.Label(f)
        self.lbl_out.grid(row=1, column=0, sticky="e")
        self.cb_out = reg(ttk.Combobox(f, textvariable=self.out_dev, width=58,
                                       state="readonly"), "readonly")
        self.cb_out.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
        self.cb_in.bind("<<ComboboxSelected>>", lambda e: self.on_dev_pick("in"))
        self.cb_out.bind("<<ComboboxSelected>>", lambda e: self.on_dev_pick("out"))
        reg(ttk.Button(f, text="Refresh", command=self.refresh_devices)).grid(
            row=0, column=2, rowspan=2, padx=4)

        # codec & link
        f = ttk.LabelFrame(main, text="Codec and link", padding=6)
        f.grid(row=3, column=0, sticky="ew", pady=3)

        def field(row, col, text, widget):
            ttk.Label(f, text=text).grid(row=row, column=col * 2, sticky="e", pady=2)
            widget.grid(row=row, column=col * 2 + 1, sticky="w", padx=6)
            return widget

        codecs = ["opus", "pcm"] if fb.HAVE_OPUS else ["pcm"]
        cb = reg(ttk.Combobox(f, textvariable=self.codec, values=codecs, width=8,
                              state="readonly"), "readonly")
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_states())
        field(0, 0, "Transmit codec:", cb)
        field(0, 1, "Sample rate (Hz):", reg(ttk.Combobox(
            f, textvariable=self.rate, values=RATES, width=8, state="readonly"),
            "readonly"))
        field(0, 2, "Frame (ms):", reg(ttk.Combobox(
            f, textvariable=self.frame, values=FRAMES, width=5, state="readonly"),
            "readonly"))
        field(1, 0, "Opus bitrate (kbps):", reg(ttk.Spinbox(
            f, from_=6, to=510, textvariable=self.bitrate, width=7), group="opus"))
        field(1, 1, "Opus complexity:", reg(ttk.Spinbox(
            f, from_=0, to=10, textvariable=self.complexity, width=7), group="opus"))
        field(1, 2, "Jitter buffer (ms):", reg(ttk.Spinbox(
            f, from_=20, to=1000, increment=10, textvariable=self.jitter, width=7)))
        reg(ttk.Checkbutton(f, text="Constant bitrate (CBR)", variable=self.cbr),
            group="opus").grid(row=2, column=0, columnspan=2, sticky="w", pady=2)
        reg(ttk.Checkbutton(f, text="In-band FEC", variable=self.fec),
            group="opus").grid(row=2, column=2, columnspan=2, sticky="w")
        field(2, 2, "Expected loss (%):", reg(ttk.Spinbox(
            f, from_=0, to=100, textvariable=self.loss, width=7), group="opus"))
        field(3, 0, "Playback channels:", reg(ttk.Combobox(
            f, textvariable=self.out_ch, values=["2", "1"], width=5,
            state="readonly"), "readonly"))
        field(3, 1, "Opus bandwidth:", reg(ttk.Combobox(
            f, textvariable=self.opus_bw, values=list(BW_CHOICES), width=18,
            state="readonly"), "readonly", group="opus"))
        field(3, 2, "Opus signal:", reg(ttk.Combobox(
            f, textvariable=self.opus_signal, values=["music", "voice", "auto"], width=7,
            state="readonly"), "readonly", group="opus"))
        reg(ttk.Checkbutton(f, text="Smooth packet timing (recommended)",
                            variable=self.pace)).grid(
            row=4, column=0, columnspan=6, sticky="w", pady=2)
        reg(ttk.Checkbutton(
            f, text="Keep the radio's sound volumes at 100% (PulseAudio / PipeWire)",
            variable=self.pin_vol)).grid(row=5, column=0, columnspan=6, sticky="w", pady=2)
        reg(ttk.Checkbutton(
            f, text="Debug: save bridge_play.wav and bridge_capture.wav "
                    "in the home folder", variable=self.debug)).grid(
            row=6, column=0, columnspan=6, sticky="w", pady=2)

        # buttons
        f = ttk.Frame(main)
        f.grid(row=4, column=0, sticky="ew", pady=6)
        self.btn_start = ttk.Button(f, text="Start", width=12, command=self.on_start)
        self.btn_start.grid(row=0, column=0, padx=4)
        self.btn_stop = ttk.Button(f, text="Stop", width=12, command=self.on_stop,
                                   state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=4)
        self.link = ttk.Label(f, text="Stopped", foreground=GREY,
                              font=("TkDefaultFont", 11, "bold"))
        self.link.grid(row=0, column=2, padx=16)

        # status + log
        f = ttk.LabelFrame(main, text="Status", padding=6)
        f.grid(row=5, column=0, sticky="nsew", pady=3)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(5, weight=1)
        self.lbl_lvl_in = ttk.Label(f)
        self.lbl_lvl_in.grid(row=0, column=0, sticky="e")
        self.bar_in = ttk.Progressbar(f, maximum=100)
        self.bar_in.grid(row=0, column=1, sticky="ew", padx=6, pady=2)
        self.lbl_lvl_out = ttk.Label(f)
        self.lbl_lvl_out.grid(row=1, column=0, sticky="e")
        self.bar_out = ttk.Progressbar(f, maximum=100)
        self.bar_out.grid(row=1, column=1, sticky="ew", padx=6, pady=2)

        # gain sliders (take effect immediately, even while running)
        self.gain_lbl = {}
        for row, which in ((0, "in"), (1, "out")):
            sc = ttk.Scale(f, from_=-40, to=12, orient="horizontal", length=170)
            sc.set(self.gain_db[which].get())
            sc.configure(command=lambda v, w=which: self.on_gain(w, v))
            sc.grid(row=row, column=2, padx=4)
            lbl = ttk.Label(f, width=14)
            lbl.grid(row=row, column=3)
            self.gain_lbl[which] = (sc, lbl)
            ttk.Button(f, text="0 dB", width=5,
                       command=lambda w=which: self.reset_gain(w)).grid(
                row=row, column=4, padx=2)
            self.show_gain(which)

        self.stat = tk.StringVar(value="-")
        self.stat_lbl = ttk.Label(f, textvariable=self.stat, justify="left")
        self.stat_lbl.grid(row=2, column=0, columnspan=5, sticky="w", pady=2)
        self.stat2 = tk.StringVar(value="")
        self.stat2_lbl = ttk.Label(f, textvariable=self.stat2, justify="left")
        self.stat2_lbl.grid(row=3, column=0, columnspan=5, sticky="w", pady=1)

        def rewrap(event):  # wrap status text instead of forcing the window wider
            width = max(200, event.width - 24)
            self.stat_lbl.configure(wraplength=width)
            self.stat2_lbl.configure(wraplength=width)
        f.bind("<Configure>", rewrap)
        ttk.Separator(f).grid(row=4, column=0, columnspan=5, sticky="ew", pady=3)
        box = ttk.Frame(f)
        box.grid(row=5, column=0, columnspan=5, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.log = tk.Text(box, height=7, width=80, state="disabled", wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

    # ---- window size and position -------------------------------------------
    def apply_geometry(self):
        """Fix the window size so it no longer resizes itself when status text changes.
        Restores the size and position used last time (kept on screen)."""
        r = self.root
        r.update_idletasks()
        w, h = r.winfo_reqwidth(), r.winfo_reqheight()
        m = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", self.cfg.get("geometry", "") or "")
        if m:
            sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
            gw = min(max(int(m.group(1)), 640), sw)
            gh = min(max(int(m.group(2)), 560), sh)
            x = min(max(int(m.group(3)), 100 - gw), sw - 100)
            y = min(max(int(m.group(4)), 0), sh - 100)
            r.geometry(f"{gw}x{gh}{x:+d}{y:+d}")
        else:
            r.geometry(f"{w}x{h}")

    # ---- gain ------------------------------------------------------------
    def show_gain(self, which):
        db = self.gain_db[which].get()
        self.gain_lbl[which][1].configure(text=f"Gain {db:+.1f} dB")

    def on_gain(self, which, value):
        db = round(float(value) * 2) / 2  # 0.5 dB steps
        self.gain_db[which].set(db)
        self.show_gain(which)
        if self.node is not None:
            self.node.set_gain_db(which, db)

    def reset_gain(self, which):
        self.gain_lbl[which][0].set(0.0)
        self.on_gain(which, 0.0)

    # ---- helpers ---------------------------------------------------------
    def append_log(self, msg):
        stamp = time.strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {msg}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 300:
            self.log.delete("1.0", "50.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def log_ts(self, msg):  # safe to call from worker threads
        self.logq.put(msg)

    def refresh_devices(self, reinit=True):
        if reinit and self.node is None:
            try:  # make PortAudio re-scan (private API, hence the guard)
                sd._terminate()
                sd._initialize()
            except Exception:
                pass
        self.in_map, self.out_map, self.dev_key = {}, {}, {}
        try:
            apis = sd.query_hostapis()
            for i, d in enumerate(sd.query_devices()):
                api = apis[d["hostapi"]]["name"]
                label = f"{i}: {d['name']} [{api}]"
                self.dev_key[label] = (d["name"], api)
                if d["max_input_channels"] > 0:
                    self.in_map[label] = i
                if d["max_output_channels"] > 0:
                    self.out_map[label] = i
        except Exception as exc:
            self.append_log(f"Could not list audio devices: {exc}")
        self.apply_devices()

    def _find_label(self, want, mp):
        """The dropdown label for a saved (name, api) device: exact match first, then a
        name-only match if it is unique. Empty string if the device is not present."""
        if not want:
            return ""
        want = tuple(want)
        for label in mp:
            if self.dev_key.get(label) == want:
                return label
        same = [label for label in mp if self.dev_key.get(label, ("",))[0] == want[0]]
        return same[0] if len(same) == 1 else ""

    def apply_devices(self):
        """Fill both dropdowns. A saved device that is not present stays visible as a
        '(not present)' entry, so what you configured is never silently replaced."""
        defaults = {}
        try:
            defaults = {"in": sd.default.device[0], "out": sd.default.device[1]}
        except Exception:
            pass
        for kind, cb, var, mp in (("in", self.cb_in, self.in_dev, self.in_map),
                                  ("out", self.cb_out, self.out_dev, self.out_map)):
            want = self.want[kind]
            values = list(mp)
            label, placeholder = self._find_label(want, mp), ""
            if want and not label:
                placeholder = f"(not present) {want[0]} [{want[1]}]"
                values.insert(0, placeholder)
                label = placeholder
            elif not want:        # nothing saved yet: suggest the system default (not saved)
                label = next((lb for lb, idx in mp.items() if idx == defaults.get(kind)), "")
            cb["values"] = values
            var.set(label)
            self.missing[kind] = placeholder

    def restore_devices(self, mode):
        saved = self.dev_cfg.get(mode, {})
        self.want = {k: (tuple(saved[k]) if saved.get(k) else None) for k in ("in", "out")}
        self.apply_devices()

    def remember_devices(self, mode):
        """Store the devices you intend to use. This is NOT taken from the dropdowns, so a
        fallback shown while the radio is switched off can never overwrite your choice."""
        self.dev_cfg[mode] = {"in": list(self.want["in"] or ()),
                              "out": list(self.want["out"] or ())}

    def on_dev_pick(self, kind):
        var = self.in_dev if kind == "in" else self.out_dev
        mp = self.in_map if kind == "in" else self.out_map
        label = var.get()
        if label in mp:                       # a real device was chosen: remember it
            self.want[kind] = self.dev_key[label]
            self.save_cfg()
        self.apply_devices()                  # (re)build the lists, dropping stale placeholders

    def on_mode_change(self, initial=False):
        mode = self.mode.get()
        if not initial and mode != self.prev_mode:
            self.remember_devices(self.prev_mode)
            self.restore_devices(mode)
            self.prev_mode = mode
        li, lo = LABELS[mode]
        self.lbl_in.configure(text=li)
        self.lbl_out.configure(text=lo)
        if mode == "server":
            self.lbl_lvl_in.configure(text="RX audio from radio:")
            self.lbl_lvl_out.configure(text="TX audio to radio:")
        else:
            self.lbl_lvl_in.configure(text="TX audio from FreeDV:")
            self.lbl_lvl_out.configure(text="RX audio to FreeDV:")
        self.update_states()

    def update_states(self):
        running = self.node is not None
        mode = self.mode.get()
        opus = self.codec.get() == "opus"
        for w, normal, group in self.cfg_widgets:
            ok = not running
            if group == "client" and mode != "client":
                ok = False
            if group == "server" and mode != "server":
                ok = False
            if group == "opus" and not opus:
                ok = False
            w.configure(state=normal if ok else "disabled")
        busy = running or self.start_pending
        self.btn_start.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")

    # ---- settings --------------------------------------------------------
    def collect(self):
        mode = self.mode.get()
        try:
            port = int(self.port.get())
            rate = int(self.rate.get())
            frame = int(self.frame.get())
            bitrate = int(self.bitrate.get()) * 1000
            complexity = int(self.complexity.get())
            loss = int(self.loss.get())
            jitter = int(self.jitter.get())
        except ValueError:
            raise ValueError("Numeric fields must contain whole numbers.")
        if not 1 <= port <= 65535:
            raise ValueError("UDP port must be between 1 and 65535.")
        host = self.host.get().strip()
        if mode == "client" and not host:
            raise ValueError("Enter the server's host name or IP address.")
        for kind, var, mp, what in (("in", self.in_dev, self.in_map, "capture (input)"),
                                    ("out", self.out_dev, self.out_map, "playback (output)")):
            if var.get() not in mp:
                if self.missing[kind]:
                    raise DeviceMissing(f"The {what} device is not present: "
                                        f"{self.missing[kind]}")
                raise ValueError(f"Select a {what} device.")
        a = fb.make_args(
            host=host, port=port, bind=self.bind.get().strip() or "0.0.0.0",
            rate=rate, frame_ms=frame, jitter_ms=jitter, codec=self.codec.get(),
            bitrate=bitrate, opus_bw=BW_CHOICES.get(self.opus_bw.get(), "nb"),
            opus_signal=self.opus_signal.get(), complexity=max(0, min(10, complexity)),
            cbr=self.cbr.get(), fec=self.fec.get(), loss=max(0, min(100, loss)),
            in_dev=self.in_map[self.in_dev.get()],
            out_dev=self.out_map[self.out_dev.get()],
            out_channels=int(self.out_ch.get()), pace=self.pace.get(),
            in_gain_db=self.gain_db["in"].get(), out_gain_db=self.gain_db["out"].get(),
            dump_play=str(Path.home() / "bridge_play.wav") if self.debug.get() else None,
            dump_capture=str(Path.home() / "bridge_capture.wav") if self.debug.get() else None)
        err = fb.check_args(a)
        if err:
            raise ValueError(err)
        return a

    def save_cfg(self):
        self.remember_devices(self.mode.get())
        data = dict(
            mode=self.mode.get(), host=self.host.get(), port=self.port.get(),
            bind=self.bind.get(), codec=self.codec.get(), rate=self.rate.get(),
            frame_ms=self.frame.get(), bitrate_kbps=self.bitrate.get(),
            complexity=self.complexity.get(), loss=self.loss.get(),
            opus_bw=BW_CHOICES.get(self.opus_bw.get(), "nb"),
            opus_signal=self.opus_signal.get(),
            jitter_ms=self.jitter.get(), cbr=self.cbr.get(), fec=self.fec.get(),
            out_channels=self.out_ch.get(), pace=self.pace.get(), devices=self.dev_cfg,
            pin_volumes=self.pin_vol.get(),
            geometry=self.root.geometry(),
            in_gain_db=self.gain_db["in"].get(), out_gain_db=self.gain_db["out"].get())
        try:
            CFG_FILE.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    # ---- run control -----------------------------------------------------
    def on_start(self):
        try:
            args = self.collect()
        except DeviceMissing as exc:
            if not self.start_pending:
                self.append_log(f"{exc}. Waiting for it - the bridge will start by itself "
                                "when it appears (press Stop to cancel).")
            self.start_pending = True
            self.link.configure(text="Waiting for audio device...", foreground=AMBER)
            self.update_states()
            return
        except ValueError as exc:
            messagebox.showerror("Check settings", str(exc))
            return
        self.start_pending = False
        try:
            node = fb.Node(args, self.mode.get() == "server", log=self.log_ts)
            node.start()
        except Exception as exc:
            self.update_states()
            messagebox.showerror("Could not start", f"{type(exc).__name__}: {exc}")
            return
        self.node = node
        self.was_linked = False
        if self.pin_vol.get():
            self.pinner = fb.PulsePinner(node, 100, 10.0, log=self.log_ts)
            self.pinner.start()
        # the bridge started with these devices, so they are now the ones to remember
        self.want = {"in": self.dev_key.get(self.in_dev.get()),
                     "out": self.dev_key.get(self.out_dev.get())}
        self.save_cfg()
        self.link.configure(
            text="Waiting for client..." if self.mode.get() == "server"
            else "Waiting for server...", foreground=AMBER)
        self.update_states()

    def on_stop(self):
        if self.start_pending:
            self.start_pending = False
            self.append_log("Waiting cancelled")
        if self.pinner is not None:
            self.pinner.stop()
            self.pinner = None
        if self.node is not None:
            self.node.stop()
            self.node = None
            self.append_log("Stopped")
        self.link.configure(text="Stopped", foreground=GREY)
        self.bar_in["value"] = self.bar_out["value"] = 0
        self.in_disp = self.out_disp = 0.0
        self.stat.set("-")
        self.stat2.set("")
        self.update_states()

    def on_close(self):
        try:
            self.root.after_cancel(self._tick_id)
        except (AttributeError, tk.TclError):
            pass
        self.save_cfg()
        if self.pinner is not None:
            self.pinner.stop()
        if self.node is not None:
            self.node.stop()
        self.root.destroy()

    # ---- periodic UI update ----------------------------------------------
    def tick(self):
        self.tick_n += 1
        try:
            while True:
                self.append_log(self.logq.get_nowait())
        except queue.Empty:
            pass
        if (self.node is None and (self.start_pending or any(self.missing.values()))
                and time.time() - self._last_scan >= 3.0):
            self._last_scan = time.time()
            before = dict(self.missing)
            self.refresh_devices(reinit=True)     # picks up devices that have just appeared
            for kind in ("in", "out"):
                if before[kind] and not self.missing[kind]:
                    shown = self.in_dev.get() if kind == "in" else self.out_dev.get()
                    self.append_log(f"Audio device is now present: {shown}")
            if self.start_pending and not any(self.missing.values()):
                self.start_pending = False
                self.on_start()
        n = self.node
        if n is not None:
            p, n.in_peak = n.in_peak, 0
            q, n.out_peak = n.out_peak, 0
            self.in_disp = max(level_pct(p), self.in_disp - 6)
            self.out_disp = max(level_pct(q), self.out_disp - 6)
            self.bar_in["value"] = self.in_disp
            self.bar_out["value"] = self.out_disp
            if self.tick_n % 10 == 0:
                self.update_stats(n)
        self._tick_id = self.root.after(100, self.tick)

    def update_stats(self, n):
        s = n.stats()
        server = self.mode.get() == "server"
        if s["linked"]:
            who = f"Client {s['peer'][0]}:{s['peer'][1]}" if server else "Server"
            self.link.configure(text=f"Connected - {who}", foreground=GREEN)
        else:
            self.link.configure(
                text="Waiting for client..." if server else "No data from server",
                foreground=AMBER)
        if s["linked"] != self.was_linked:
            self.append_log("Link up" if s["linked"] else "Link lost")
            self.was_linked = s["linked"]
        self.stat.set(f"TX {s['tx_kbps']:.0f} kbps ({s['tx_pkts']} pkts)    "
                      f"RX packets {s['rx']}    "
                      f"Lost {s['lost']} ({s['loss_events']} events, longest {s['max_gap']})    "
                      f"Late {s['late']}    "
                      f"TX errors {s['tx_err']}    Bad {s['bad']}    "
                      f"Underruns {s['underruns']}    Dropped {s['dropped']}    "
                      f"Buffer {s['buf']} frames")
        self.stat2.set(f"Send timing: {s['tx_tight']} too-close, {s['tx_slow']} too-late, "
                       f"longest gap {s['tx_gap_max']:.0f} ms (one frame = "
                       f"{self.frame.get()} ms)")


def main():
    root = tk.Tk()

    def show_error(exc_type, exc, tb):  # never fail silently inside a callback
        traceback.print_exception(exc_type, exc, tb)
        messagebox.showerror("Unexpected error", f"{exc_type.__name__}: {exc}")

    root.report_callback_exception = show_error
    App(root)
    root.minsize(640, 560)
    root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
