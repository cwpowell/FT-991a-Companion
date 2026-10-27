# -*- mode: python ; coding: utf-8 -*-
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

# PyInstaller recipe for the FT-991A panel, for both macOS and Windows.
# Do not run it directly; use the build script for your system:
#     macOS:    bash packaging/build_macos.sh
#     Windows:  packaging\build_windows.bat
#
# The panel is one self-contained Python file, so there is little to collect. What the packaged
# program needs besides Python and Tk is the Opus library, which is added here:
#   Windows: Contributions/opus.dll
#   macOS:   the path in the LIBOPUS environment variable (the build script sets it)
# The sound library (PortAudio) comes inside the sounddevice package on both systems, and
# PyInstaller's own hook for sounddevice collects it.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
ICONS = os.path.join(ROOT, "packaging", "icons")
def _panel_version():
    """The version in ft991a_panel.py, unless the build script says otherwise."""
    import re
    with open(os.path.join(ROOT, "ft991a_panel.py"), encoding="utf-8") as f:
        m = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M)
    return m.group(1) if m else "0.0.0"


VERSION = os.environ.get("PANEL_VERSION") or _panel_version()

binaries = []
if sys.platform == "win32":
    binaries.append((os.path.join(ROOT, "Contributions", "opus.dll"), "."))
elif sys.platform == "darwin" and os.environ.get("LIBOPUS"):
    binaries.append((os.environ["LIBOPUS"], "."))

a = Analysis(
    [os.path.join(ROOT, "ft991a_panel.py")],
    pathex=[ROOT],
    binaries=binaries,
    # the licence files travel inside every packaged program (see license_dir() in the panel)
    datas=[(os.path.join(ROOT, name), "licenses") for name in ("LICENSE", "NOTICE.md", "THIRD-PARTY-LICENSES.txt")],
    hiddenimports=[],
    # numpy is needed (noise reduction); these are not, and leaving them out keeps the app smaller
    excludes=["matplotlib", "scipy", "pandas", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="FT-991A Panel",
        console=False,
        upx=False,
        argv_emulation=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="FT-991A Panel")
    app = BUNDLE(
        coll,
        name="FT-991A Panel.app",
        icon=os.path.join(ICONS, "ft991a.icns"),
        bundle_identifier="io.github.cwpowell.ft991a-remote",
        info_plist={
            "CFBundleName": "FT-991A Panel",
            "CFBundleDisplayName": "FT-991A Panel",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # macOS refuses microphone access to an app that does not say why it wants it
            "NSMicrophoneUsageDescription":
                "The FT-991A Panel sends your microphone to the radio when you transmit.",
        },
    )
else:
    # Windows (and a Linux test build): one file, no console window
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="FT-991A-Panel",
        console=False,
        upx=False,
        icon=os.path.join(ICONS, "ft991a.ico"),      # used on Windows; ignored elsewhere
    )
