#!/bin/bash
# Build "FT-991A Panel.app" and a .dmg for the Mac you are running this on.
#   Apple Silicon Mac  ->  dist/ft-991a-apple-silicon.dmg
#   Intel Mac          ->  dist/ft-991a-intel.dmg
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
export PANEL_VERSION="${PANEL_VERSION:-1.0.0}"
PYTHON="${PYTHON:-python3}"
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
python -m pip install --quiet pyinstaller sounddevice opuslib

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

# 6. the disk image: the app, and a shortcut to Applications to drag it onto
STAGE="build/dmg-$LABEL"
rm -rf "$STAGE" && mkdir -p "$STAGE" "dist"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/ft-991a-$LABEL.dmg"
rm -f "$DMG"
for attempt in 1 2 3; do
    if hdiutil create -volname "FT-991A Panel" -srcfolder "$STAGE" -ov -format UDZO "$DMG"; then
        break
    fi
    echo "hdiutil failed (attempt $attempt of 3)"
    [ "$attempt" = 3 ] && { echo "Could not make the disk image"; exit 1; }
    sleep 10
done
echo "== built $DMG ($(du -h "$DMG" | cut -f1))"
