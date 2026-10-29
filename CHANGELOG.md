# Changelog

## 1.4.1

Fixes from the first tries on real devices (HaliKey serial, HaliKey MIDI, a Vail adapter, and the daemon on a
radio's computer).

- **The Vail adapter is set up when you arm.** Left alone it sits in keyboard mode: it keys and sounds its own
  sidetone but sends the program no MIDI, which is what happened. As K4-Companion does, the panel now opens the
  adapter's MIDI output and sends MIDI mode, its sidetone pitch, its speed and its keyer mode (passthrough for
  **Vail adapter (paddles)**, or iambic A or B for **Vail adapter (its own keyer)**), and sends the new speed
  when the speed slider moves. A HaliKey MIDI is Vail-compatible and is probably set up the same way.
- **A Monitor button** shows which MIDI note or serial line moves when a key is pressed, and keys nothing, so that
  a device can be set up by looking at it instead of guessing. For a Vail adapter it also sends the set-up.
- **A serial key is closed when its line differs from how it stood when the port opened**, whichever way that is.
  The first version assumed a closed key raises a line, so a line that rests high gave no key-down on a press
  and a key-down (with no key-up) on the release: no sidetone on the press, then a string of dits. Arm with the
  key open.
- **A paddle held for 30 s without a break is let go** (it is taken to be stuck).
- **The keying daemon no longer shares a UDP port** with another program. It set `SO_REUSEADDR`, which on Linux lets
  two programs (a second keying daemon, or cwdaemon on 6789) bind the same port with no error and split the
  packets. A busy port is now an error with a plain message, and so is a serial port another program holds.
- **`--verbose` works on a real serial line**, saying every key change and who made it (`events` or `text`), and
  the daemon announces the first packet from each sender. This tells a daemon fault from the radio's own keyer.
- `keying-daemon.service` and the README now say how the daemon and cwdaemon fit together (it replaces cwdaemon),
  that no `sudo` is needed (the `dialout` group is enough), and that menu 012 KEYER TYPE must be OFF.
- Still unverified on the real devices: the notes a HaliKey MIDI sends, and which serial lines a HaliKey serial key
  uses (use Monitor to find out, then set them in the box).

## 1.4.0

- **A keyer, for paddles and keys.** The CW window has a new **Paddle or key** box. Choose a device:
  **MIDI straight key**, **MIDI paddles**, **Serial straight key**, **Serial paddles**, **Vail adapter
  (paddles)** (the adapter in its passthrough mode: notes 1 and 2 are the paddles) or **Vail adapter (its
  own keyer)** (the adapter keys by itself and any of its notes 0, 1 and 2 is the key). Then pick its port,
  the notes (MIDI, defaults 20 and 21) or lines (serial: CTS, DSR, DCD or RI, defaults DSR for dit and DCD
  for dah), the mode (**Iambic B** or **Iambic A**) and the dit memory (33%), and tick **Arm**. There is
  a sidetone with its own pitch. Ultimatic is not offered.
  - **Derived from W7DA's keyer.** The iambic decision logic, the use of a MIDI device's own timing and
    clock, and the reading of serial paddle lines come from K4-Companion by Dale Farnsworth, W7DA
    (GPL-3.0-or-later, used with his permission), rewritten for this program: the elements go to the keying
    daemon, where K4-Companion sends them to a K4. His notice is on that section and in `NOTICE.md`.
  - **How it works.** Each element is chosen just before it is due, and both its edges (down and up) are sent
    to the daemon at once, stamped with their true times, so the daemon replays them 150 ms later with
    exactly the spacing the keyer meant. A straight key's edges go straight through.
  - **MIDI** (python-rtmidi): a note on is a key down whatever its velocity (the device uses the velocity
    for its own timing), the device's clock is rebuilt from that timing so that a stalled computer does not
    distort the keying, and a gap of a second or more is taken afresh.
  - **Serial** (pyserial): the lines are read every millisecond; how they stand when the port opens is where
    they rest, so a paddle line must rest low, and a straight key is debounced for 20 ms.
  - **It refuses to key** unless the radio is connected and in CW-U or CW-L, is not calibrating the noise
    reduction, and is not transmitting for another reason. This is checked from the paddle's own thread
    without touching the window (a first version did, and crashed the paddle reader on its first press: the
    tests found it).
  - **The key is let go** on Abort, when the device is closed or disarmed (which also clears what the
    keyer had already scheduled in the daemon), when the connection to the radio is lost (watched even
    while the CW window is hidden), and when the panel closes.
  - Measured with simulated devices through the real sender, UDP and the daemon: the spacing at the line
    matched what the keyer meant to within 0.5 ms, every edge was played a fixed 149 ms after it was meant,
    and a MIDI straight key held 120 and 126 ms came out as 120 and 126 ms.
- The installers now contain pyserial and python-rtmidi; the self-test checks that they are inside, and
  their licences are in `THIRD-PARTY-LICENSES.txt`.
- A sidetone that cannot start (no sound library) is no longer retried on every key press, which had
  delayed the test key's timing by tens of milliseconds.
- `tests/test-keyer.py` and `tests/test-keyer-chain.py` test the keyer without a radio. They need pyserial.
- **Not yet tried on real devices:** a HaliKey MIDI, a Vail adapter or a HaliKey serial key. The MIDI note
  numbers and the serial lines are W7DA's defaults and can be changed in the box.

## 1.3.0

- **The CW window can key the radio through the keying daemon.** A new **Key** row has **Arm**, the
  daemon's port (6790) and a **Test key (hold)** button that works like a straight key: key down while you
  hold it, key up when you let go. The events go to `keying-daemon.py` on the radio's computer, which
  replays them with their spacing kept.
  - While armed, the panel sends a heartbeat every 100 ms carrying its clock, so that the daemon knows how
    long packets take before the first element arrives. Each key event is sent twice, a few milliseconds
    apart, and every packet repeats the last six events.
  - Tested from the panel's button through UDP to the daemon's line (a simulated line): a 250 ms hold
    came out as 251.0 ms, and a run of 12 elements of different lengths kept their spacing to within 2.0 ms,
    including the timing of the button itself.
  - It refuses as the text box does (not in a CW mode, not during a noise calibration, not while the radio
    transmits for another reason), but a second element is not refused while this panel is the one keying.
  - **The key is let go** when the connection to the radio is lost, when the CW window is hidden, when the
    panel closes, on Abort, and (by the daemon) when packets stop.
  - The daemon now counts the heartbeat pings in its statistics.
  - This is the straight-key path. The paddle (iambic) and the MIDI input come next.

## 1.2.1

- **A keying daemon, `keying-daemon.py`** (new; the panel does not use it yet). It runs on the radio's
  computer and keys the radio through the DTR line of its second USB serial port, the way RemoteRig keeps
  its timing: key events carry the sender's clock, and the daemon plays them back after a fixed delay (the
  jitter buffer, 150 ms by default), so the spacing a paddle made is the spacing the radio gets, whatever the
  network did. Run it by hand, or with `keying-daemon.service`. It also understands cwdaemon's text
  commands on UDP port 6789 (text, `ESC 0`, `ESC 2`, `ESC 4` and `ESC h`), so the CW window's text box works
  with it unchanged, and only one program has to own the serial port.
  - Measured over real UDP on one machine, with the sender's heartbeats: with up to 120 ms of random delay
    and reordered packets the worst error in the spacing of 49 elements was under 1 ms, and with 2% of
    the packets lost it was 0.0 ms. A very bad link (30% loss, or delays well above the buffer) degrades
    but keeps the elements in order.
  - The delay is learnt from the sender's clock in every packet and kept fixed while keying is going on,
    because changing it in the middle would move later elements against earlier ones. The tests found
    exactly that fault in a first version.
  - **The key is let go** when packets stop while it is down (0.8 s), when it has been down too long (3 s),
    on an abort packet, when key events arrive while text is being sent (the operator's hand wins), and
    when the daemon stops or receives SIGTERM. The serial line is opened low. Packets can be limited with
    `--token` and `--allow`. All of this is tested, including a real process receiving SIGTERM.
  - `tests/test-keying-daemon.py` is the test (it needs pyserial for one part, `pip install pyserial`).
  - Nothing here touches a radio yet. The panel side (the keyer and the sender) comes next.
- The program version is 1.2.1.

## 1.2.0

- **The CW text box and macros are in a separate window.** The **CW window** button beside the log (on
  every tab of the main window) shows and hides it; the button reads **Hide CW** while it is open. The
  window holds the text box with **Send** and **Abort**, the speed, your call and theirs, the five
  macros, the cwdaemon address, and a **Zero in** button (the radio's CW auto zero-in, which used to be
  on the Memory page). It remembers where it was and whether it was open, and a saved position that is
  off the screen is not trusted. Closing it with its own close button only hides it.
- Closing the panel while a message is being keyed now sends an **abort**, as PTT already lets go.
- A space typed into the CW window, or pressed on one of its buttons, never keys the transmitter
  (tested).
- **The Memory page is gone**, and with it the radio-memory code (`KM`, `KY` and the memory-type checks).
  It worked, but cwdaemon does the same job better, with no 50-character limit. The facts it
  established are kept in `SESSION-NOTES.md`, and the code is in the history (version 1.1.1).
- The Function tab has five pages again: Receive, Noise, Transmit, CW and FM etc.

## 1.1.2

- **The window title is now "FT-991a Companion 1.1.2"** (it was "FT-991A Remote Panel"). `--version`,
  `--license` and the Copy report use the same name.
- **The audio utility is now called `audio-bridge`.** It has grown beyond FreeDV, so the name no
  longer says FreeDV: `audio-bridge.py`, `audio-bridge-gui.py` (its window is titled "Audio Bridge")
  and `audio-bridge.service`.
- **One naming style: hyphens.** File names with more than one word used a mix of hyphens and
  underscores; they now all use hyphens:
  `freedv_audio_bridge.py` to `audio-bridge.py`, `freedv_audio_bridge_gui.py` to
  `audio-bridge-gui.py`, `freedv-audio-bridge.service` to `audio-bridge.service`,
  `ft991a_panel.py` to `ft991a-panel.py`, `fake_ft991a.py`, `fake_rigctld.py` and `fake_cwdaemon.py` to
  `fake-ft991a.py`, `fake-rigctld.py` and `fake-cwdaemon.py`, `radio_power.sh` to `radio-power.sh`,
  `freedv_guard.sh` to `freedv-guard.sh`, and in `packaging/`: `ft991a_panel.spec`, `build_macos.sh`,
  `build_windows.bat` and `icons/make_icon.py` to `ft991a-panel.spec`, `build-macos.sh`,
  `build-windows.bat` and `icons/make-icon.py`.
- The files the programs write follow the same rule: the panel's settings are now
  `~/.ft991a-panel.json`, the bridge window's are `~/.audio-bridge.json`, and the Copy report file is
  `~/ft991a-panel-report.txt`. **Your old settings are not lost:** when the new file does not exist
  yet, the old one (`~/.ft991a_panel.json`, `~/.freedv_audio_bridge.json`) is read, and it is left where
  it is.
- Python cannot import a file whose name has a hyphen, so the bridge window now loads `audio-bridge.py`
  by its path. Anything of your own that does `import ft991a_panel` or `import freedv_audio_bridge`
  must load the file by path (`importlib.util.spec_from_file_location`) or be changed.
- **If you run the bridge as a systemd service,** switch to the new unit:
  `systemctl --user disable --now freedv-audio-bridge`, copy the new `audio-bridge.service` to
  `~/.config/systemd/user/` (check its three CHANGE lines, which point at `audio-bridge.py`), then
  `systemctl --user daemon-reload` and `systemctl --user enable --now audio-bridge`.

## 1.1.1

- **Tune no longer asks for confirmation.** It starts at once, and pressing it again stops it. The
  setting that controlled this was saved as `confirm_tune: true` in every existing settings file, so it
  has a new name, `tune_confirm`, which is off unless you turn it on in `~/.ft991a_panel.json`. The
  old `confirm_tune` is ignored.
- Documentation: the CW Memory page has been confirmed on an FT-991A (Play keys the radio and works very
  well), so the README and the manual say so. The Keyboard page is still tested only against
  simulators and the real cwdaemon.

## 1.1.0

- **The program is now under the GNU General Public License, version 3 or later** (GPL-3.0-or-later).
  It was under the MIT License up to version 1.0.x, and a copy of one of those that you already have
  stays under the MIT License. The change makes it possible to include code from other GPL programs,
  with their authors' permission, such as K4-Companion by Dale Farnsworth, W7DA.
  - `LICENSE` is the GNU GPL, version 3. `NOTICE.md` holds the credits and says where the source is.
    `THIRD-PARTY-LICENSES.txt` holds the licences of the software that the installers include (Python,
    Tcl/Tk, numpy, sounddevice, PortAudio, opuslib, Opus and PyInstaller), taken from their own packages.
  - Every source file has a short GPL header with an SPDX identifier.
  - The three files travel inside every download. The packaged program has them inside it, and
    `--license` shows the notice (`--license full`, `credits` and `third-party` print the texts, and
    `--save-license FOLDER` copies them out). The Windows installer shows the GPL on its licence page and
    installs the files, the macOS disk image contains them, and the draft release says where the source
    is. The self-test checks that a packaged program really contains them, and a build fails if not.

## 1.0.9

- **A Keyboard page** on the Function tab: type text, or press one of five macros (F1 to F5, which you
  can edit, with [MYCALL] and [CALL] replaced by the two call signs you type), and **cwdaemon**
  sends it. cwdaemon is a small free program that runs on the radio's computer and keys the radio
  through the DTR line of its second USB serial port. The Morse timing is done there, so the network
  cannot spoil it.
  - Speed 5 to 60 wpm. **Abort** stops the message at once (the real cwdaemon drops the key the
    moment the abort arrives).
  - The panel tells you when a message has finished (cwdaemon sends a reply), and says so when it does
    not confirm, which usually means that cwdaemon is not running or the address is wrong.
  - Text that cwdaemon cannot key is removed. A + or - would change its speed in the middle of a
    message, so they are left out. A long message goes in pieces of up to 120 characters.
  - It refuses unless the panel is connected and the radio is in CW-U or CW-L, never during a noise
    calibration, and not while the radio is transmitting for another reason. Abort is always allowed.
  - The speed, your call, the macros and the address are remembered.
- The Function tab's pages are now Receive, Noise, Transmit, CW, **Memory** (the radio's own CW
  memories, which were called CW send), **Keyboard** and FM etc.
- `fake_cwdaemon.py`: a simulated cwdaemon, to try the page without a radio.
- Tested against the simulated cwdaemon and against the real cwdaemon 0.10.2 on its dummy device (the
  text, the speed code, the abort and the end-of-message reply all behaved as documented). Not yet
  tried on a radio. The cwdaemon in Ubuntu 24.04 is 0.10.2 and has no -o option for choosing the
  keying lines; newer versions do.

## 1.0.8

- **Fix: Play on the CW send page did nothing on the radio.** Version 1.0.7 played the memories with
  the codes in Yaesu's CAT table (KY 1 to 5), which play the memories *recorded with the paddle*. A
  text memory 1 to 5 is played by **KY 6 to A**, as logging software that works with these radios
  uses them. The page now sends those, and uses KY 1 to 5 for its "Recorded with the paddle" buttons.
- Text written into a memory now ends with the **}** that the radio's contest memory keyer needs. A
  message holds 49 characters of text, and the } is hidden on screen.
- The page reads the radio's menu items 018 to 022 and says when a memory is set to MESSAGE (shown as
  "(recorded)"). Store and Play then refuse and explain, because that memory cannot hold text.
- The simulator now behaves this way (text played by KY6 to KYA, needs TEXT and the closing brace, and a
  paddle-recorded memory played by KY1 to KY5), and the test checks that the panel never sends anything
  that would play silently.
- These facts come from a logging program's documentation for Yaesu radios, and are not yet confirmed
  on an FT-991A.

## 1.0.7

- **CW send**, a new page on the Function tab (after CW), for the radio's CW memories. The FT-991A
  cannot be told to send arbitrary text: over CAT it plays back what is stored in its five keyer
  memories (KY 1 to 5) or its recorded message keys (KY 6 to A), and text goes into a memory with KM,
  up to 50 characters. The page edits and plays them:
  - five memory rows, each with the text the radio holds, **Store** and **Play**. Store cleans the
    text (capitals; only letters, digits, space and . , ? / = - ; at most 50 characters), writes it,
    reads it back, and the log says whether the radio kept it. A * marks text that is not stored yet,
    and a change made on the radio itself appears here.
  - Play only plays what is stored. It never stores and plays in one go, so nothing can be sent later
    by surprise.
  - buttons for the five recorded messages, **Stop (TX off)** (the same TX0 command as the PTT
    button; the manual does not say whether it ends a message that is already playing) and **Zero
    in** (the radio's CW auto zero-in).
  - Play, the recorded messages and Zero in are refused, with a message, unless the radio is connected
    over raw CAT, is in CW-U or CW-L and is not transmitting, and they are always refused during a
    noise calibration. While the page shows, the status line reminds you to try it on a dummy load.
- A **CW spot** toggle (a tone for tuning to a signal) on the CW page. The Function tab now has six
  pages, with FM and more as the sixth.
- Written from Yaesu's CAT manual and tested against the simulator, which now understands KM, KY, ZI
  and CS. Not yet tried on a real radio.

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
