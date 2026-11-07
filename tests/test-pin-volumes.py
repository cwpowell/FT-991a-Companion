#!/usr/bin/env python3
# FT-991A Companion
# Copyright (C) 2026  Charles W. Powell, NK8O
# SPDX-License-Identifier: GPL-3.0-or-later
# (The full GPL notice is in the file LICENSE.)
"""Tests for --pin-volumes and --pin-playback in audio-bridge.py: the radio's playback volume (the transmit drive) can be
pinned at its own level while the capture volume and the bridge's own streams are kept at another. They use a fake
`pactl` that keeps volumes, mutes and streams in a file and prints them the way PulseAudio does.
They need no sound card and no PulseAudio.

    python3 tests/test-pin-volumes.py          ("WRONG" marks a failure)
"""
import importlib.util, json, os, stat, subprocess, sys, tempfile, types
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
ok = lambda c: "OK" if c else "WRONG"
class _SD(types.ModuleType):                               # the bridge imports sounddevice; nothing here plays sound
    def __getattr__(self, name): return lambda *a, **k: None
sys.modules["sounddevice"] = _SD("sounddevice")
spec = importlib.util.spec_from_file_location("audio_bridge", os.path.join(ROOT, "audio-bridge.py"))
ab = importlib.util.module_from_spec(spec); sys.modules["audio_bridge"] = ab; spec.loader.exec_module(ab)

work = tempfile.mkdtemp(); STATE = os.path.join(work, "state.json")
FAKE = '''#!/usr/bin/env python3
import json, os, sys
st = json.load(open(os.environ["FAKE_PACTL_STATE"]))
a = sys.argv[1:]
def vol(pct): return "Volume: front-left: %d / %3d%% / -1.00 dB,   front-right: %d / %3d%% / -1.00 dB\\n        balance 0.00\\n" % (pct * 655, pct, pct * 655, pct * 655 and pct)
def save(): json.dump(st, open(os.environ["FAKE_PACTL_STATE"], "w"))
st.setdefault("calls", []); st["calls"].append(a)
cmd = a[0]
if cmd in ("get-sink-volume", "get-source-volume"):
    kind = cmd.split("-")[1]; print(vol(st[kind][a[1]]["vol"]), end="")
elif cmd in ("get-sink-mute", "get-source-mute"):
    kind = cmd.split("-")[1]; print("Mute: " + ("yes" if st[kind][a[1]]["mute"] else "no"))
elif cmd in ("set-sink-volume", "set-source-volume"):
    kind = cmd.split("-")[1]; st[kind][a[1]]["vol"] = int(a[2].rstrip("%"))
elif cmd in ("set-sink-mute", "set-source-mute"):
    kind = cmd.split("-")[1]; st[kind][a[1]]["mute"] = bool(int(a[2]))
elif cmd == "list" and a[1] in ("sink-inputs", "source-outputs"):
    kind, head = ("sink-input", "Sink Input") if a[1] == "sink-inputs" else ("source-output", "Source Output")
    for ident, s in st[kind].items():
        print("%s #%s\\n\\tDriver: protocol-native.c\\n\\tMute: %s\\n\\t%s\\n\\tProperties:\\n\\t\\tapplication.process.id = \\"%d\\"\\n" % (head, ident, "yes" if s["mute"] else "no", vol(s["vol"]).splitlines()[0], s["pid"]))
elif cmd in ("set-sink-input-volume", "set-source-output-volume"):
    kind = cmd[4:-7]; st[kind][a[1]]["vol"] = int(a[2].rstrip("%"))
elif cmd in ("set-sink-input-mute", "set-source-output-mute"):
    kind = cmd[4:-5]; st[kind][a[1]]["mute"] = bool(int(a[2]))
save()
'''
path = os.path.join(work, "pactl"); open(path, "w").write(FAKE); os.chmod(path, 0o755)
os.environ["PATH"] = work + os.pathsep + os.environ["PATH"]; os.environ["FAKE_PACTL_STATE"] = STATE
SINK, SOURCE = "alsa_output.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.analog-stereo", "alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.analog-stereo"
def setup(sink=5, source=30, sink_mute=False, stream=20, stream_mute=True, cap=25):
    json.dump({"sink": {SINK: {"vol": sink, "mute": sink_mute}}, "source": {SOURCE: {"vol": source, "mute": False}},
               "sink-input": {"12": {"vol": stream, "mute": stream_mute, "pid": os.getpid()}, "13": {"vol": 7, "mute": False, "pid": 1}},
               "source-output": {"21": {"vol": cap, "mute": False, "pid": os.getpid()}}}, open(STATE, "w"))
def state(): return json.load(open(STATE))
def sets(): return [c for c in state()["calls"] if c[0].startswith("set-")]
def pinner(percent, playback=None):
    ab.PulsePinner._pulse_name = staticmethod(lambda dev: {9: SINK, 18: SOURCE}.get(dev))
    logs = []
    return ab.PulsePinner(types.SimpleNamespace(out_dev=9, in_dev=18), percent, 1.0, log=logs.append, playback_percent=playback), logs

print("--- 1. as before: --pin-volumes alone puts everything at that level")
setup(); p, logs = pinner(100); p.apply(); s = state()
print("   sink, source and this program's streams:", s["sink"][SINK]["vol"], s["source"][SOURCE]["vol"], s["sink-input"]["12"]["vol"], s["source-output"]["21"]["vol"], ok(all(v == 100 for v in (s["sink"][SINK]["vol"], s["source"][SOURCE]["vol"], s["sink-input"]["12"]["vol"], s["source-output"]["21"]["vol"]))))
print("   the stream that was muted is unmuted, and another program's stream (pid 1) is left alone:", ok(not s["sink-input"]["12"]["mute"] and s["sink-input"]["13"]["vol"] == 7))
print("--- 2. the user's case: the radio's playback at 45% (for the ALC), everything else at 100%")
setup(sink=5); p, logs = pinner(100, 45); p.apply(); s = state()
print("   the playback slider was 5% and is now", s["sink"][SINK]["vol"], "| capture", s["source"][SOURCE]["vol"], "| this program's playback stream", s["sink-input"]["12"]["vol"], "(NOT 45: it would be attenuated twice)", ok((s["sink"][SINK]["vol"], s["source"][SOURCE]["vol"], s["sink-input"]["12"]["vol"], s["source-output"]["21"]["vol"]) == (45, 100, 100, 100)))
print("   the log says what it did:", [l for l in logs if "playback volume" in l], ok(any("the radio's playback volume was 5%, set to 45%" in l for l in logs)))
print("--- 3. once right, it leaves things alone")
n = len(sets()); logs.clear(); p.apply()
print("   a second check changes nothing and logs nothing:", ok(len(sets()) == n and not logs))
print("--- 4. someone turns it up or mutes it: it is pulled back")
st = state(); st["sink"][SINK]["vol"] = 117; json.dump(st, open(STATE, "w")); logs.clear(); p.apply()
print("   a mixer set it to 117%:", state()["sink"][SINK]["vol"], ok(state()["sink"][SINK]["vol"] == 45), "|", logs)
st = state(); st["sink"][SINK]["mute"] = True; json.dump(st, open(STATE, "w")); logs.clear(); p.apply()
print("   it was muted:", ok(not state()["sink"][SINK]["mute"]), "|", logs)
print("--- 5. only the radio's playback device (no --pin-volumes): nothing else is touched")
setup(sink=5, source=30, stream=20, stream_mute=True); p, logs = pinner(0, 45); p.apply(); s = state()
print("   playback", s["sink"][SINK]["vol"], "| capture still", s["source"][SOURCE]["vol"], "| stream still", s["sink-input"]["12"]["vol"], "and muted:", s["sink-input"]["12"]["mute"], ok((s["sink"][SINK]["vol"], s["source"][SOURCE]["vol"], s["sink-input"]["12"]["vol"], s["sink-input"]["12"]["mute"]) == (45, 30, 20, True)))
print("--- 6. a sink that is already right is not touched")
setup(sink=45); st = state(); p, logs = pinner(100, 45); p.apply()
print("   a sink already at 45% is not touched:", ok(not any(c[0] == "set-sink-volume" for c in sets())))
print("--- 7. the command line")
def run(*args):
    code = "import sys, types, runpy\nclass M(types.ModuleType):\n    def __getattr__(self, n): return lambda *a, **k: None\nsys.modules['sounddevice'] = M('sounddevice')\nsys.argv = ['audio-bridge.py'] + sys.argv[1:]\nrunpy.run_path(%r, run_name='__main__')" % os.path.join(ROOT, "audio-bridge.py")
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=30)
for bad in ("0", "151", "-5", "abc"):
    r = run("server", "--pin-playback", bad)
    print(f"   --pin-playback {bad!r} is refused:", r.returncode, repr(r.stderr.strip().splitlines()[-1][:90]) if r.stderr.strip() else "", ok(r.returncode == 2 and "pin-playback" in r.stderr))
r = run("server", "--pin-volumes", "300"); print("   --pin-volumes 300 is refused:", r.returncode, ok(r.returncode == 2 and "pin-volumes" in r.stderr))
r = run("--help"); print("   --pin-playback is in the help:", ok("--pin-playback" in r.stdout))
print("   the bridge's version text mentions it:", ok("pin-playback" in ab.__version__))
