# Notices

## FT-991A Companion

Copyright (C) 2026  Charles W. Powell, NK8O

FT-991A Companion is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public
License for more details. The full text is in the file `LICENSE`, and at
<https://www.gnu.org/licenses/>.

**Earlier versions.** Versions before 1.1.0 were released under the MIT License. A copy of one of those
versions that you already have stays under the MIT License. From 1.1.0 the program is under the GPL.

**Source code.** The source of every version is in the project's repository,
<https://github.com/cwpowell/FT-991a-Companion>. Each release is tagged with its version number, and the
tag holds the source that was built into the installers of that release.

## Credits

- **K4-Companion** by Dale Farnsworth, W7DA (Copyright (C) 2025, GPL-3.0-or-later), which grew out of
  K4Macro-Python by Charles W. Powell, NK8O, is used with Dale's permission. **The keyer section of
  `ft991a-panel.py`** (from the comment "the keyer: paddles and keys become key-down and key-up edges") is
  derived from it: the iambic A and B decision logic (`Keyer.choose_element` and `paddle_timestamps`), the way
  a MIDI paddle's own timing and clock are used (`MidiPaddles`). They were rewritten for this program, which sends its elements to a keying daemon where
  K4-Companion sends them to a K4, and uses a simpler sidetone. Dale's copyright notice stays on that
  section, as the GPL requires.
- On the Keyboard page the Morse keying is done by **cwdaemon**, a separate program that the panel
  talks to over the network (<https://github.com/acerion/cwdaemon>, GPL-2.0-or-later, originally by
  Joop Stakenborg, PA4TU). It is not part of this program and is not included in the installers.
- `Contributions/opus.dll` is the Opus codec library (BSD 3-Clause), built for Windows.

## Software in the installers

The installers and packaged programs contain Python, Tcl/Tk, numpy, sounddevice, PortAudio, opuslib, the
Opus codec and python-rtmidi (with RtMidi), and were built with PyInstaller. Their licences, and where to find their source, are in
`THIRD-PARTY-LICENSES.txt`. The packaged program has all three files inside it: run it with `--license`
to see where they are.
