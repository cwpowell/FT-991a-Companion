#!/bin/bash
# Apply update-1.1.2.zip to your clone. It renames the files with "git mv", so that git keeps their
# history, and then unpacks the zip over them. It changes nothing else, and does not commit.
#
#   cd into your clone (any folder inside it is fine), then:
#       bash ~/Downloads/apply-1.1.2.sh ~/Downloads/update-1.1.2.zip
set -e
ZIP="${1:-$HOME/Downloads/update-1.1.2.zip}"
[ -f "$ZIP" ] || { echo "Cannot find $ZIP"; exit 1; }
cd "$(git rev-parse --show-toplevel)"
[ "$(git branch --show-current)" = "main" ] || { echo "You are not on the main branch. Run: git switch main"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
    echo "You have uncommitted changes. Commit or stash them first, so this update is a clean step:"
    git status --short
    exit 1
fi
move() {
    if [ -e "$1" ]; then git mv "$1" "$2"; echo "  renamed   $1  ->  $2"
    elif [ -e "$2" ]; then echo "  already   $2"
    else echo "  MISSING   $1 (and no $2): stopping"; exit 1; fi
}
echo "Renaming:"
move freedv_audio_bridge_gui.py        audio-bridge-gui.py
move freedv_audio_bridge.py            audio-bridge.py
move freedv-audio-bridge.service       audio-bridge.service
move freedv_guard.sh                   freedv-guard.sh
move radio_power.sh                    radio-power.sh
move ft991a_panel.py                   ft991a-panel.py
move fake_ft991a.py                    fake-ft991a.py
move fake_rigctld.py                   fake-rigctld.py
move fake_cwdaemon.py                  fake-cwdaemon.py
move packaging/ft991a_panel.spec       packaging/ft991a-panel.spec
move packaging/build_macos.sh          packaging/build-macos.sh
move packaging/build_windows.bat       packaging/build-windows.bat
move packaging/icons/make_icon.py      packaging/icons/make-icon.py
echo; echo "Unpacking $ZIP"
unzip -o "$ZIP" -d .
git add -A
echo; echo "Done. This is what will be committed:"; git status --short
echo; python3 ft991a-panel.py --version
echo "That should say: FT-991a Companion 1.1.2"
echo "Next:  git status --short | grep '^ *D'   (should print nothing)   then commit and push."
