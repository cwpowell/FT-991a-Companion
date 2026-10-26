# Changelog

## 1.0.3

- **Noise reduction** on the Audio tab: a check box, a level from 1 to 20, and an **Auto-calibrate**
  button. It is spectral subtraction (Boll, with Berouti's over-subtraction and spectral floor and
  smoothing against "musical noise") applied to the audio that goes to your speakers. The cable that
  FreeDV reads is never touched. It takes about 1% of one processor core.
- Auto-calibrate retunes VFO A to a quiet frequency beside the one you are on (a carrier, a whistle,
  speech, static and fading are all recognised and skipped), measures the noise there for four
  seconds, tunes the subtraction to the level and to how steady the noise is, and retunes to your
  frequency. The speakers are silent while it runs, PTT and Tune are refused, and anything unexpected
  puts the radio back first.
- It needs numpy (`pip install numpy`). Without numpy the rest of the panel works and the noise
  reduction controls are greyed out. The Mac and Windows installers include it, which makes them
  larger.
- The self-test (`--selftest`) now also runs the noise reduction.

## 1.0.2

**Repository**

- The user manual is a PDF in `Documentation/`, and the screenshots are in `Images/` (both at the top
  level, replacing `docs/` and `docs/img/`).

**Panel**

- The version number is shown in the title bar, and `--version` prints it. It is defined once, in
  `ft991a_panel.py`, and the Mac app, the Windows installer and the report all use it.
- New **Settings** tab: all 153 items of the radio's menu, in 18 sections, read from the radio and
  changeable (check boxes, pull-downs, sliders, entry fields). Each change is read back, nothing is
  written while transmitting, and risky items ask first. Raw CAT connection only.
- The ALC meter is on the radio's own scale: mid-scale on the radio reads 50%. Green to 25%, orange to
  50%, red above 50%.
- A light beside the ATU button: green when the tuner is off, yellow when it is in line, flashing while
  it tunes. The buttons in that row are only as wide as their labels.
- Meters for the audio from the radio and for the microphone on the Radio tab, as on the Audio tab.
  All meters are about half their earlier height.
- The window is as tall as the tab that is showing, so a short tab has no empty space under it.
- The Audio tab's status text was cut off at the bottom of the window, hiding the lines that say where
  audio stops. The window now grows to fit, the key lines come first and the packet counters show
  under Advanced.
- The Audio tab says when audio is leaving the panel but the radio's ALC is not moving.
- A **Copy report** button for bug reports (IP addresses hidden).
- A Test button for the speakers; Mac and Windows icons.

**Bridge** (`freedv_audio_bridge.py` 2026-10-26a)

- `--pin-volumes PERCENT` (and `--pin-interval`) keeps the PulseAudio / PipeWire volumes of the radio's
  sound card and of the bridge's own streams from being turned down, and logs each correction. The
  bridge window has a checkbox for it.
- With `--wait` the bridge also recovers when the radio is switched off and on again.

**Installers**

- The Mac and Windows builds use the new icon and carry the version number.
