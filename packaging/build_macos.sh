#!/bin/bash
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

# Build "FT-991A Panel.app" and a .dmg for the Mac you are running this on.
#   Apple Silicon Mac  ->  dist/ft-991a-apple-silicon-<version>.dmg
#   Intel Mac          ->  dist/ft-991a-intel-<version>.dmg
# A build only runs on the kind of Mac it was made on, so GitHub builds both (see
# .github/workflows/build.yml). You can also run this by hand on your own Mac.
#
# You need: Python 3.12 from python.org (it includes Tk), and Homebrew (for the Opus library).
set -euo pipefail
cd "$(dirname "$0")/.."

case "$(uname -m)" in
    arm64)  LABEL=apple-silicon ;;
    x86_64) LABEL=intel ;;
    *) echo "Unknown processor: $(uname -m)"; exit 1 ;;
esac
PYTHON="${PYTHON:-python3}"
# the version number lives in ft991a_panel.py (set PANEL_VERSION to override it)
export PANEL_VERSION="${PANEL_VERSION:-$("$PYTHON" packaging/version.py)}"
APP_NAME="FT-991A Panel"

echo "== $LABEL build, version $PANEL_VERSION, $($PYTHON --version)"
"$PYTHON" -c "import tkinter" 2>/dev/null || {
    echo "This Python has no tkinter. Install Python 3.12 from https://www.python.org/downloads/ and retry."
    exit 1; }

# 1. the Opus library, which goes inside the app.
#    ("brew --prefix opus" prints a path even when opus is NOT installed, so ask "brew list".)
command -v brew >/dev/null 2>&1 || {
    echo "Homebrew is needed for the Opus library: https://brew.sh"; exit 1; }
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_ENV_HINTS=1
if ! brew list --formula opus >/dev/null 2>&1; then
    echo "== installing the Opus library with Homebrew"
    brew install opus
fi
LIBOPUS="$(brew --prefix opus)/lib/libopus.0.dylib"
[ -f "$LIBOPUS" ] || { echo "Cannot find $LIBOPUS even after installing opus"; ls -l "$(brew --prefix opus)/lib" || true; exit 1; }
export LIBOPUS
echo "== Opus library: $LIBOPUS ($(lipo -archs "$LIBOPUS"))"

# 2. build tools in a private virtual environment
VENV="build/venv-$LABEL"
"$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
. "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet pyinstaller sounddevice opuslib numpy

# 3. the app
rm -rf "dist/$LABEL" "build/work-$LABEL"
pyinstaller --noconfirm --clean --distpath "dist/$LABEL" --workpath "build/work-$LABEL" packaging/ft991a_panel.spec
APP="dist/$LABEL/$APP_NAME.app"
[ -d "$APP" ] || { echo "The app was not built"; exit 1; }

echo "== libraries inside the app:"
find "$APP" \( -name "libopus*" -o -name "libportaudio*" \) -exec ls -l {} \; || true

# 4. sign it "ad hoc". Apple Silicon will not run unsigned code at all, and this is the most
#    that is possible without a paid Apple Developer account.
codesign --force --deep --sign - "$APP"
codesign --verify --deep "$APP" && echo "== signature ok (ad hoc)"

# 5. check that everything really went into the app: Tk, PortAudio and Opus
mkdir -p build
SELFTEST="build/selftest-$LABEL.txt"
if ! "$APP/Contents/MacOS/$APP_NAME" --selftest "$SELFTEST"; then
    echo "!! THE SELF-TEST FAILED: something is missing from the app (see above)."; exit 1
fi

# 5b. the app must be the version this build says it is. What the program reports and the version
#     in its Info.plist must both equal PANEL_VERSION, which is what the files are named after.
BUILT="$("$APP/Contents/MacOS/$APP_NAME" --version | awk '{print $NF}')"
PLIST="$(python -c 'import plistlib, sys; print(plistlib.load(open(sys.argv[1], "rb")).get("CFBundleShortVersionString", ""))' "$APP/Contents/Info.plist")"
if [ "$BUILT" != "$PANEL_VERSION" ] || [ "$PLIST" != "$PANEL_VERSION" ]; then
    echo "!! VERSION MISMATCH: this build is meant to be $PANEL_VERSION, but the program says '$BUILT' and Info.plist says '$PLIST'."
    exit 1
fi
echo "== version $PANEL_VERSION confirmed (the program and Info.plist agree)"

# 6. the disk image: the app, and a shortcut to Applications to drag it onto
STAGE="build/dmg-$LABEL"
rm -rf "$STAGE" && mkdir -p "$STAGE" "dist"
cp -R "$APP" "$STAGE/"
cp LICENSE NOTICE.md THIRD-PARTY-LICENSES.txt "$STAGE/"      # the licence goes with the app
ln -s /Applications "$STAGE/Applications"
DMG="dist/ft-991a-$LABEL-$PANEL_VERSION.dmg"
rm -f dist/ft-991a-"$LABEL"*.dmg
for attempt in 1 2 3; do
    if hdiutil create -volname "FT-991A Panel" -srcfolder "$STAGE" -ov -format UDZO "$DMG"; then
        break
    fi
    echo "hdiutil failed (attempt $attempt of 3)"
    [ "$attempt" = 3 ] && { echo "Could not make the disk image"; exit 1; }
    sleep 10
done
echo "== built $DMG ($(du -h "$DMG" | cut -f1))"
