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

# freedv-guard.sh - stop FreeDV's audio device settings from being overwritten.
#
#   bash freedv-guard.sh save      remember the current audio settings as the good ones
#   bash freedv-guard.sh check     show how the current audio settings differ from the saved ones
#   bash freedv-guard.sh restore   put the saved audio settings back (FreeDV must be closed)
#   bash freedv-guard.sh run       wait for the radio's audio devices, restore the saved audio
#                                  settings, start FreeDV, and restore them again when it exits
#
# Only the [Audio...] sections of FreeDV's .conf files are saved and restored; everything else in
# the files (callsign, frequencies, window layout ...) is left alone. A timestamped backup of a
# file is made before it is changed. After you deliberately change FreeDV's audio settings, run
# "save" again, or "run" will put the old ones back.
#
# Settings (environment variables):
#   FREEDV_CONF_DIR   FreeDV's settings folder      (default ~/.config/freedv)
#   FREEDV_GOLDEN     where the good copy is kept   (default ~/.config/freedv-golden)
#   FREEDV_BIN        the FreeDV program, for "run" (default: "freedv" on the PATH)
#   RADIO_AUDIO       text found in the radio's audio device names (default Burr-Brown)
#   WAIT              seconds "run" waits for the radio's audio (default 90; 0 = don't wait)
#   KEEP=1            "run": keep whatever FreeDV saved on exit instead of restoring
#   FORCE=1           "save"/"run": carry on even though the radio's audio devices are missing

CONF_DIR=${FREEDV_CONF_DIR:-$HOME/.config/freedv}
GOLD=${FREEDV_GOLDEN:-$HOME/.config/freedv-golden}
BACKUPS=${FREEDV_BACKUPS:-$HOME/.config/freedv-backups}
RADIO_AUDIO=${RADIO_AUDIO:-Burr-Brown}
WAIT=${WAIT:-90}

# the lines of the [Audio...] section(s) of a file
audio_section() { awk '/^\[/ { keep = ($0 ~ /^\[Audio/) } keep' "$1"; }

# replace_audio GOLDFILE CURRENTFILE: put GOLDFILE's [Audio...] section(s) into CURRENTFILE
replace_audio() {
    awk -v goldfile="$1" '
        BEGIN {
            while ((getline line < goldfile) > 0) {
                if (line ~ /^\[/) ina = (line ~ /^\[Audio/)
                if (ina) gold = gold line "\n"
            }
        }
        /^\[/ { skipping = ($0 ~ /^\[Audio/)
                if (skipping && !done) { printf "%s", gold; done = 1 } }
        !skipping { print }
        END { if (!done) printf "%s", gold }' "$2" > "$2.tmp" && mv "$2.tmp" "$2"
}

# radio_audio_present: do both a source and a sink matching RADIO_AUDIO exist?
radio_audio_present() {
    command -v pactl >/dev/null 2>&1 || return 2          # cannot tell
    pactl list short sinks   2>/dev/null | grep -q "$RADIO_AUDIO" &&
    pactl list short sources 2>/dev/null | grep -q "$RADIO_AUDIO"
}

need_conf() {
    if ! ls "$CONF_DIR"/*.conf >/dev/null 2>&1; then
        echo "No .conf files in $CONF_DIR (set FREEDV_CONF_DIR if FreeDV keeps them elsewhere)."
        exit 1
    fi
}

do_save() {
    need_conf
    radio_audio_present; rc=$?
    if [ $rc -eq 1 ] && [ -z "$FORCE" ]; then
        echo "The radio's audio devices ('$RADIO_AUDIO') are not present, so FreeDV's current"
        echo "audio settings may already be wrong. Switch the radio on, check FreeDV's audio"
        echo "settings, then run this again (or set FORCE=1)."
        exit 1
    fi
    mkdir -p "$GOLD"
    n=0
    for f in "$CONF_DIR"/*.conf; do
        if [ -z "$(audio_section "$f")" ]; then
            echo "skipped $(basename "$f"): it has no [Audio...] section"
            continue
        fi
        cp -p "$f" "$GOLD/$(basename "$f")"
        echo "saved   $(basename "$f")  ($(audio_section "$f" | grep -c '=') audio settings)"
        n=$((n + 1))
    done
    [ $n -gt 0 ] || { echo "Nothing saved: no [Audio...] section found. Run:  grep -n '^\\[' $CONF_DIR/*.conf"; exit 1; }
    echo "Good copy kept in $GOLD"
}

do_check() {
    [ -d "$GOLD" ] || { echo "Nothing saved yet. Run 'save' first."; exit 1; }
    same=1
    for g in "$GOLD"/*.conf; do
        [ -e "$g" ] || continue
        f="$CONF_DIR/$(basename "$g")"
        [ -e "$f" ] || { echo "$(basename "$g"): missing from $CONF_DIR"; same=0; continue; }
        if ! diff <(audio_section "$g") <(audio_section "$f") >/tmp/freedv-guard.diff; then
            same=0
            echo "$(basename "$g"): audio settings differ (< saved, > now):"
            sed 's/^/   /' /tmp/freedv-guard.diff
        fi
    done
    [ $same -eq 1 ] && echo "Audio settings match the saved copy."
}

do_restore() {
    [ -d "$GOLD" ] || { echo "Nothing saved yet. Run 'save' first."; exit 1; }
    if pgrep -x freedv >/dev/null 2>&1; then
        echo "FreeDV is running; it would overwrite the files when it exits. Close it first."
        exit 1
    fi
    mkdir -p "$BACKUPS"
    stamp=$(date +%Y%m%d-%H%M%S-%N)
    for g in "$GOLD"/*.conf; do
        [ -e "$g" ] || continue
        f="$CONF_DIR/$(basename "$g")"
        [ -e "$f" ] || { cp -p "$g" "$f"; echo "restored $(basename "$g") (it was missing)"; continue; }
        if diff -q <(audio_section "$g") <(audio_section "$f") >/dev/null; then
            echo "unchanged $(basename "$g")"
            continue
        fi
        cp -p "$f" "$BACKUPS/$(basename "$f").$stamp"
        replace_audio "$g" "$f" && echo "restored $(basename "$g") audio settings (backup in $BACKUPS)"
    done
}

do_run() {
    BIN=${FREEDV_BIN:-$(command -v freedv)}
    if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
        echo "Cannot find the FreeDV program. Set FREEDV_BIN, for example:"
        echo "  FREEDV_BIN=\$HOME/freedv-gui/build_linux/src/freedv bash freedv-guard.sh run"
        exit 1
    fi
    if [ "$WAIT" -gt 0 ] && [ -z "$FORCE" ]; then
        echo "Waiting up to $WAIT s for the radio's audio devices ('$RADIO_AUDIO') ..."
        waited=0
        while true; do
            radio_audio_present; rc=$?
            [ $rc -eq 0 ] && break
            if [ $rc -eq 2 ]; then echo "pactl not found, so the wait is skipped."; break; fi
            if [ $waited -ge "$WAIT" ]; then
                echo "The radio's audio devices did not appear. Not starting FreeDV, because it"
                echo "would save the wrong devices. Switch the radio on and try again (FORCE=1 to start anyway)."
                exit 1
            fi
            sleep 2; waited=$((waited + 2))
        done
    fi
    do_restore
    echo "starting FreeDV ..."
    "$BIN" "$@"
    status=$?
    if [ -z "$KEEP" ]; then
        echo "FreeDV closed (exit $status); putting the saved audio settings back."
        do_restore
    else
        echo "FreeDV closed (exit $status); KEEP=1, so its saved settings are left as they are."
    fi
}

case "$1" in
    save) do_save ;;
    check) do_check ;;
    restore) do_restore ;;
    run) shift; do_run "$@" ;;
    *) sed -n '2,12p' "$0"; exit 2 ;;
esac
