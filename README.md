<p align="center"><img src="Images/icon.png" width="128" alt="FT-991A Companion icon"></p>

# FT-991A Companion

Tools for operating a Yaesu FT-991A from another computer over a network such as
[Tailscale](https://tailscale.com), or from the same computer:

- **`ft991a-panel.py`** is a compact control panel with a **Radio** tab, an **Audio** tab, a
  **Function** tab and a **Menu** tab.
- **`audio-bridge.py`** is a UDP audio bridge that carries the radio's USB audio and your
  transmit audio, for voice and for [FreeDV](https://freedv.org).

| Radio tab | Audio tab |
| --- | --- |
| ![Radio tab](Images/panel-radio.png) | ![Audio tab](Images/panel-audio.png) |

| Function tab | Menu tab |
| --- | --- |
| ![Function tab](Images/panel-function.png) | ![Menu tab](Images/panel-menu.png) |

The screenshots were taken against the simulated radio in this repository.

## What the panel does

**Radio tab**

- VFO A and VFO B side by side. Tune digit by digit with the mouse wheel, type a frequency, or pick
  a **Band**: it goes to the last frequency and mode you used on that band (the panel remembers
  them). Split on and off, and a VFO swap.
- Mode, an FM / C4FM toggle for VHF and UHF, a bandwidth slider, AGC (off, fast, mid, slow, auto),
  power, the internal antenna tuner (in line, and a tune button). A light beside the ATU button is
  green when the tuner is off, yellow when it is in line, and flashes while it tunes.
- Push-to-talk, just above the log so that it is on every tab: click once to transmit and click
  again to stop, or press the space bar. It has a time-out and lets go when the connection drops or
  the panel closes.
- Switch the radio on and off remotely.
- S meter, power, SWR and ALC meters, with calibration. The ALC meter is on the radio's own scale
  (green to 25%, orange to 50%, red above). Meters for the audio from the radio and for your
  microphone sit beside them.
- Mic level and Volume sliders. These are software levels on your computer, and the radio's own
  volume and menu settings are never changed.
- **Noise reduction**: a check box, a level from 1 to 20 and an **Auto-calibrate** button. It is
  spectral subtraction on the audio that goes to your speakers (the cable that FreeDV reads is never
  touched). Auto-calibrate retunes to a quiet frequency beside yours, learns the noise there, and
  retunes back; PTT and Tune are refused while it is away. Needs numpy.
- A **Hide log** button, for a shorter window.

**Function tab**

- The radio's front-panel functions in five small pages: **Receive** (preamp, attenuator, RF gain,
  squelch, contour, audio peak filter), **Noise** (noise blanker, the radio's own DNR noise
  reduction, auto and manual notch), **Transmit** (speech processor, mic equalizer, mic gain,
  monitor, VOX), **CW** (keyer, break-in, speed, pitch, delay, CW spot) and **FM etc** (repeater
  shift, CTCSS tone, dial lock, fast step).
- It reads the page that is showing, so a change made on the radio appears here. Every change is
  read back, a function the radio does not answer in its present mode is greyed out, VOX asks
  before it is turned on, and the mouse wheel never changes a setting. It needs the raw CAT
  connection.

**CW window** (the **CW window** button beside the log, on every tab)

- A separate window, which remembers where it was, with a text box, a speed, your call and theirs, and
  five macros (`[MYCALL]` and `[CALL]` are filled in). [cwdaemon](https://github.com/acerion/cwdaemon),
  running on the radio's computer, sends the Morse, so the network cannot spoil the timing. The panel
  tells you when a message has finished, **Abort** stops it at once, and **Zero in** starts the radio's CW
  auto zero-in. It refuses unless the radio is in a CW mode and not transmitting. A **Key** row (**Arm**, and a
  **Test key** that you hold like a straight key) sends key-down and key-up to `keying-daemon.py` on the
  radio's computer, which keeps their timing exactly. A **Paddle or key** box adds a keyer: a MIDI or serial
  straight key or paddles, or a Vail adapter, with iambic A or B, a dit memory and a sidetone. Its iambic
  logic and its MIDI and serial input handling come from W7DA's K4-Companion (see `NOTICE.md`).

**Menu tab**

- All 153 items of the radio's menu, in 18 sections, read from the radio and changeable: check
  boxes, pull-downs, sliders and entry fields as each item needs. Every change is read back, nothing
  is written while transmitting, and a few risky items (CAT RATE, REF FREQ ADJ ...) ask first.
  It needs the raw CAT connection.

**Audio tab**

- FreeDV-style routing: radio to computer, computer to speaker, mic to computer, computer to radio.
- **Local radio**: the radio's sound card is on this computer.
  **Remote radio**: the bridge on the server carries the audio over the network.
- Virtual cables (BlackHole on macOS, VB-Cable on Windows) let FreeDV share the audio, or choose
  "direct" to connect the network straight to your speakers and microphone.
- PCM or Opus, with the bridge's own options under Advanced, plus level meters and statistics
  that show where the audio stops if you hear nothing. The status line includes the radio's ALC, and
  says so when your audio is sent but is not reaching the radio.
- A **Copy report** button puts the settings, status and log on the clipboard (IP addresses hidden)
  to paste into a bug report.

The panel talks to the radio through a raw CAT connection (for example
[ser2net](https://github.com/cminyard/ser2net)) or through Hamlib's `rigctld`. Some features, such
as the bandwidth slider, VFO B, remote power on and off, and the Function and Menu tabs, need the
raw CAT connection.

## Quick start

On the radio's computer, make the radio's CAT port reachable (ser2net for raw CAT, or `rigctld`)
and start the audio bridge:

```
python3 audio-bridge.py list          # shows the audio devices
python3 audio-bridge.py server --bind YOUR-TAILSCALE-ADDRESS \
    --in-dev "<radio capture device>" --out-dev "<radio playback device>" --wait
```

`audio-bridge.service` runs the same thing in the background as a systemd user service. Add
`--pin-volumes 100` to keep the PulseAudio / PipeWire volumes of the radio's sound card, and of the
bridge's own streams, from being turned down (every correction is logged).

On the operating computer:

```
pip install sounddevice      # for the Audio tab
pip install numpy            # optional, for noise reduction
pip install opuslib          # optional, for Opus (also needs the Opus library)
python3 ft991a-panel.py --host YOUR-RADIO-COMPUTER --port 9000 --backend cat
```

To try the panel without a radio:

```
python3 fake-ft991a.py --port 4533 --demo
python3 ft991a-panel.py --host 127.0.0.1 --port 4533 --backend cat
```

The panel needs only Python 3 and tkinter. The Audio tab also needs `sounddevice`
and the noise reduction needs numpy (without it that one feature is greyed out). On Windows,
put a 64-bit `opus.dll` next to `ft991a-panel.py` to use Opus. The panel remembers its settings in
`~/.ft991a-panel.json`.

## Installers (no Python needed)

Ready-to-run versions are built by GitHub's own machines, because a Mac app can only be built on a
Mac and a Windows program only on Windows. They are on the **Releases** page of this repository,
with the version in the file names:

- macOS: `ft-991a-apple-silicon-<version>.dmg` and `ft-991a-intel-<version>.dmg`.
- Windows: `FT-991A-Panel-Setup-<version>.exe` (installer) and `FT-991A-Panel-<version>.exe`
  (program on its own).

To make a new build, open the **Actions** tab, choose **Build installers** and click **Run
workflow**, leaving the Version box empty (the version is the one in `ft991a-panel.py`). The
finished files are attached to a draft release; publish it to put them on the Releases page.

The `MacOS/` and `Windows/` folders have a README each with the details, including how to get past
the first-start warnings from macOS and Windows (the apps are not signed). Every build checks itself
before it is published: the program starts and reports whether Tk, numpy, the sound library and Opus
are really inside it, and the build stops if the program is not the version in `ft991a-panel.py`.

You can also build on your own computer: `bash packaging/build-macos.sh` or
`packaging\build-windows.bat`.

## Files

| File | Runs on | What it is |
| --- | --- | --- |
| `ft991a-panel.py` | the operating computer | The control panel. It is one self-contained file and includes the client side of the audio engine. |
| `audio-bridge.py` | the radio's computer | The UDP audio bridge: `server`, `client` and `list` modes, PCM or Opus. |
| `audio-bridge-gui.py` | either | A window for the bridge. It needs `audio-bridge.py` in the same folder. |
| `audio-bridge.service` | the radio's computer | systemd user service that runs the bridge server in the background. |
| `keying-daemon.py` | the radio's computer | Keys the radio through a serial port's DTR line from far away, with the timing of timestamped key events kept by a jitter buffer. It also understands cwdaemon's text commands. Needs `pyserial`. Not used by the panel yet. |
| `keying-daemon.service` | the radio's computer | systemd user service that runs the keying daemon. |
| `tests/test-keying-daemon.py` | any | Tests for the keying daemon. They touch no radio. |
| `tests/test-keyer.py`, `tests/test-keyer-chain.py` | any | Tests for the keyer, alone and through the real sender into the keying daemon. They touch no radio. |
| `radio-power.sh` | the radio's computer | Switches the radio on or off over its CAT port. |
| `freedv-guard.sh` | the radio's computer | Keeps FreeDV's audio device settings from being overwritten. |
| `rigtest.sh`, `nettest.sh` | the radio's computer | Diagnostics for `rigctld` and Hamlib's network client. They never key the transmitter. |
| `udpseq.py` | both ends | UDP packet-loss tester that sends packets the way the bridge does. |
| `wavstat.py` | any | Summarises a bridge debug WAV file: level, continuity and tone frequency. |
| `fake-ft991a.py` | any | A simulated FT-991A for the raw CAT connection. |
| `fake-rigctld.py` | any | A simulated `rigctld`. |
| `fake-cwdaemon.py` | any | A simulated `cwdaemon`, to try the Keyboard page without a radio. |
| `Contributions/` | | `opus.dll`, a 64-bit build of the Opus codec library (libopus 1.6.1) for Windows, with its readme. The Windows program and installer use it. |
| `packaging/` | | The recipe, build scripts and icons (`packaging/icons/`) for the macOS and Windows installers. The version number lives in `ft991a-panel.py`. |
| `.github/workflows/build.yml` | GitHub | Builds the installers. |
| `LICENSE`, `NOTICE.md`, `THIRD-PARTY-LICENSES.txt` | | The GNU General Public License, the credits, and the licences of the software that the installers include. |
| `MacOS/`, `Windows/` | | A README for each installer. The installers themselves are on the Releases page. |

The server and the panel speak the same audio protocol, so either can be updated on its own.

## Keying CW from the panel

The CW window can send text (through `cwdaemon`, or through `keying-daemon.py`, which understands the same
text commands) and can key the radio from a key or paddles, with the timing kept exactly. Both need a small
program on **the radio's computer** that owns the radio's **second USB serial port**.

**One program owns that port: `keying-daemon.py` replaces `cwdaemon`; do not run both.** They conflict over the
serial port and over UDP port 6789. `keying-daemon.py` answers cwdaemon's text commands on port 6789, so the CW
window's text box works unchanged, and it also takes timestamped key events on port 6790.

1. **Stop cwdaemon:** `sudo systemctl stop cwdaemon` or `sudo pkill cwdaemon`.
2. **Let your user open the serial port, so that nothing needs `sudo`:**
   `sudo usermod -aG dialout $USER`, then log out and in again. Check with `ls -l /dev/ttyUSB1` and `groups`.
   Use the stable name from `ls /dev/serial/by-id` for the port.
3. **On the radio:** menu **060 PC KEYING = DTR**, menu **012 KEYER TYPE = OFF** (the radio's own keyer would
   answer a held key line with a string of dits), menu 033 CAT RTS = DISABLE, and a CW mode (CW-U or CW-L).
4. **Install and start the daemon** on the radio's computer (`pip install pyserial`):
   `python3 keying-daemon.py --device /dev/serial/by-id/YOUR-PORT --bind YOUR-TAILSCALE-ADDRESS --verbose`
   (`--verbose` prints every key change and who asked for it; `keying-daemon.service` runs it in the
   background). It says what is wrong, and stops, if the serial port or a UDP port is already in use.
   Try it first without the radio: add `--null` instead of `--device`.
5. **In the CW window:** type the radio's computer in the address box (the text port stays 6789 and the
   daemon port 6790), tick **Arm**, and try the **Test key** on a dummy load.

**Paddles and keys.** Choose a device in the **Paddle or key** box, pick its port, and press **Monitor**: it shows
which note or serial line moves when you press the key, and keys nothing. Set the notes or lines to what it shows,
press Monitor again to stop, then tick **Arm**.
- A **Vail adapter**, and probably a **HaliKey MIDI** (it is Vail-compatible), must be told to use MIDI mode. Choose
  **Vail adapter (paddles)**: the panel sends that, and the speed and pitch, when you arm (without it the adapter
  keys and sounds its own sidetone but sends the program nothing). Its notes are 1 and 2.
- A **serial** key or paddles: a key is closed when its line differs from how it stood when you armed, so arm with
  the key open. The panel opens the port with **DTR and RTS off**, so opening it cannot key anything; tick
  **Drive DTR** or **Drive RTS** only if your interface needs them for power. (Some interfaces have DTR or RTS wired
  to the transmitter's key line: with those on, the transmitter would be keyed as soon as the port opened. Use a dummy
  load while you set a device up.)
- **Use what was seen:** press Monitor, press the dit paddle and then the dah paddle, and click **Use what was seen**
  to set the notes or lines from what the device really sent. Choose **(all MIDI inputs)** in the Port box to see
  which port a device is on. **Test tone** checks the sidetone apart from the keying.
- **If a MIDI device shows events but nothing keys,** the notes in the box are not the ones it sends (the log says
  "MIDI note N is not one of the notes in the box"). **If there is no tone,** use Test tone: it needs the
  `sounddevice` package (and, on Linux, `libportaudio2`).

**MIDI devices need python-rtmidi, which runs in a separate helper process.** python-rtmidi is a native library
that can crash the program it runs in (it did, on a Mac with Python 3.14), so the panel never loads it: it starts
itself as a helper to do all the MIDI work and talks to it over a socket on the same computer. If the helper dies,
the keys are let go, the window says why, and the panel carries on; disarm and arm again to start a new one. If
MIDI will not work on your Python, use Python 3.12 or 3.13, or the installer (built with 3.12): for example
`python3.12 -m venv ~/ft991a-venv`, then `~/ft991a-venv/bin/pip install numpy sounddevice opuslib pyserial
python-rtmidi`, then `~/ft991a-venv/bin/python ft991a-panel.py`.

A **HaliKey MIDI** sends notes 20 and 21 (dit and dah), the defaults for MIDI paddles. A **Vail adapter** always
starts in keyboard mode and needs the MIDI-mode message that the panel sends when you arm; its MIDI port must show in
the Monitor's list of MIDI input ports, or there is nothing to send to.

**If the radio sends a string of dits**, run the daemon with `--verbose`. If it prints `key DOWN` and `key up`
again and again, the cause is on the panel's side: disarm, and use Monitor to check the device. If it prints
nothing while the radio sends dits, the radio's own keyer is answering the line: set menu 012 KEYER TYPE to OFF.

## Safety

- The panel can transmit (PTT, and the tuner's tuning carrier) and it changes radio settings.
  Try everything on a dummy load first.
- Keep the radio's own transmit time-out enabled (menu 036 TX TOT).
- Neither the bridge nor a ser2net or `rigctld` port has a password. Bind them to a private
  network such as Tailscale, and never expose them to the internet.

## Status

Version 1.1.0 (see `CHANGELOG.md`).

Developed against the simulators in this repository. The maintainer has run it against a real
FT-991A over ser2net from macOS: the control panel, remote power on and off, push-to-talk, and
audio in both directions with PCM and Opus,  and the local-radio audio mode. The Function tab, the 
Band pull-down and the noise reduction have also been tested and seem to function within normal 
operating parameters. The CW window, its keyer, and the MIDI and serial inputs have so far been tested
only against simulated devices, the simulators and the real `cwdaemon`. Not yet tested under Windows. Expect rough edges there, and please open 
an issue with the log and the status text from the Audio tab.

## Documentation

Each program describes its options in its header comments and with `--help`. A full user manual
(setting up each program, every control, troubleshooting and known issues) is in `Documentation/`.

## License

FT-991A Companion is free software under the **GNU General Public License, version 3 or later**
(GPL-3.0-or-later). See `LICENSE` for the text and `NOTICE.md` for the credits. The installers
contain other software under its own licences, listed in `THIRD-PARTY-LICENSES.txt`. Every packaged
program has these three files inside it: run it with `--license` (or `--save-license FOLDER` to copy
them out).

Versions before 1.1.0 were released under the MIT License, and a copy of one of those that you already
have stays under it.

The source of every version is in this repository. Each release is tagged with its version number,
and the tag holds the source that was built into that release's installers.
