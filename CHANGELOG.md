# Changelog

## 1.0.6

- **Auto-calibrate now finds a clear frequency where it used to fail.** A real log showed it rejecting
  all six frequencies, five of them empty (S-meter 0), each for the same steady line of about 27 dB.
  A line that stays at the **same pitch on three or more different frequencies** cannot be a signal,
  because a signal changes pitch when the radio is retuned: it comes from the receiver or the audio path
  (a spur, a tone, hum). The calibration now ignores such a line when it judges the frequencies, picks
  the nearest one that is otherwise clear, says so in the log, and leaves the line in the profile so
  that it is reduced like the rest. Genuinely busy frequencies (a different carrier on each) are still
  rejected, and the "NO CLEAR FREQUENCY" warning is kept for them.
- **Better candidate frequencies in SSB-type modes:** 3.75, 6.25 and 8.75 kHz either side, which are
  1.25 kHz away from the busy whole and half kilohertz. They are counted on the absolute frequency grid
  (they end in .25 or .75 kHz wherever you are tuned) and none is nearer than 3.5 kHz, because closer
  in, the signal you are listening to would still be inside the audio passband and would be learned as
  noise. AM and FM are unchanged.
- The log now gives the pitch of each frequency's strongest line, and offsets such as +3.75 kHz.
- **A fix in the noise reduction itself:** the calibrated noise profile followed the raw power while the
  gain was judged against smoothed power, so over a few seconds the profile became spiky and let a
  steady tone through: a pure tone was cut by only 5 to 8 dB while the hiss was cut by up to 28 dB. A
  tone is now cut as much as the hiss. As a side effect "musical noise" fell by 60 to 70%; the
  reduction of steady noise changed by 0.1 to 1.6 dB (a little deeper), and speech quality at the
  recommended levels is the same within 0.2 dB.

## 1.0.5

- **A Function tab**, between Audio and Menu, with the radio's front-panel functions in five small
  pages: **Receive** (preamp IPO/AMP 1/AMP 2, attenuator, RF gain, squelch, contour and its
  frequency, the audio peak filter), **Noise** (noise blanker and its level, the radio's own DNR noise
  reduction and its level, auto notch, manual notch and its frequency), **Transmit** (speech
  processor and its level, mic equalizer, mic gain, monitor and its level, VOX with its gain and
  delay), **CW** (keyer, break-in, speed, sidetone pitch, break-in delay) and **FM and more**
  (repeater shift, tone mode, CTCSS tone, dial lock, fast step). Things the Radio tab or the meters
  already show are not repeated (AGC, ATU, bandwidth, power, ALC, SWR), and the radio's own volume is
  never touched.
- It reads the page that is showing every couple of seconds, so a change made on the radio's own
  controls appears here. A change you make is written at once and read back, and the control then
  shows what the radio kept; if the radio refuses it, the control goes back and the log says so. A
  function that the radio does not answer in its present mode (the manual notch in FM, say) is greyed
  out and says so. The mouse wheel never changes a setting.
- Turning **VOX** on asks first, because it makes the radio transmit whenever sound reaches its
  microphone or data input, including the panel's own audio. Receiver functions are refused while a
  noise calibration is running, and a note in the log says when a receiver change may have made the
  calibration out of date.
- It needs the raw CAT connection, like the Menu tab. All the command formats come from Yaesu's CAT
  manual, and the simulator checks them strictly: a command with the wrong number of digits is refused,
  as the real radio would.
- The periodic update (`tick`) no longer stops the whole panel if something in it fails. It reports the
  error once in the log and carries on, because the PTT time-out, the meters and the frequency all
  depend on it.

## 1.0.4

- **Band pull-down** to the left of the frequency entry. It shows the band the radio is on, and picking
  a band goes to the last frequency and mode you used on it (the first time, a usual starting point,
  for example 14.200 USB on 20 m and 146.520 FM on 2 m). The panel remembers this between runs. The
  mode is sent after the frequency, not before it, and nothing is remembered while the radio is
  catching up. Picking a band is refused, with a message, while the radio is transmitting.
- **PTT is now just above the log**, which puts it on every tab, not only on the Radio tab.
- **Hide log** button (it becomes **Show log**). The log keeps collecting while it is hidden, the
  window shrinks to fit, and the choice is remembered.
- **Noise reduction moved to the Radio tab**, below the ATU row and the level sliders and above the
  meters. It works as before. Without the sound library it is greyed out with a reason, and the
  panel no longer depends on the Audio tab having been built.
- The **Settings tab is now called Menu**, which is what it is: the radio's own 153 menu items.

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
- The installers carry the version in their file names. A build now fails if the program it made is
  not the version in `ft991a_panel.py`, and a version typed into the workflow must match it.

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
