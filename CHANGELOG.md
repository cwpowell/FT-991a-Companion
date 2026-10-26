# Changelog

## 1.0.2

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
