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

- Dale Farnsworth, W7DA, wrote K4-Companion (Copyright (C) 2025, GPL-3.0-or-later), which grew out of
  K4Macro-Python by Charles W. Powell, NK8O. Dale has given permission for his code to be used in this
  project. **No code from K4-Companion is in this program yet.** When some is added, the file that
  contains it will keep his copyright notice in its header, and it will be listed here.
- On the Keyboard page the Morse keying is done by **cwdaemon**, a separate program that the panel
  talks to over the network (<https://github.com/acerion/cwdaemon>, GPL-2.0-or-later, originally by
  Joop Stakenborg, PA4TU). It is not part of this program and is not included in the installers.
- `Contributions/opus.dll` is the Opus codec library (BSD 3-Clause), built for Windows.

## Software in the installers

The installers and packaged programs contain Python, Tcl/Tk, numpy, sounddevice, PortAudio, opuslib and
the Opus codec, and were built with PyInstaller. Their licences, and where to find their source, are in
`THIRD-PARTY-LICENSES.txt`. The packaged program has all three files inside it: run it with `--license`
to see where they are.
