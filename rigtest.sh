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

# rigtest.sh - start rigctld on this machine only, probe it over plain TCP, print one report.
# Run:  bash rigtest.sh      It prints one report.
# Nothing here keys the transmitter: it only reads values.
# To probe a daemon that is ALREADY running (without restarting it):  SKIP_START=1 bash rigtest.sh

MODEL=${MODEL:-1035}
DEV=${DEV:-/dev/yaesu-cat}
SPEED=${SPEED:-38400}
PORT=${PORT:-4532}
HOST=${HOST:-127.0.0.1}      # set HOST=<tailscale-address> if rigctld is bound to the Tailscale address
LOG=/tmp/rigctld_test.log
CAPS=/tmp/rig_caps.txt
RIGCTL=${RIGCTL:-/usr/local/bin/rigctl}
RIGCTLD=${RIGCTLD:-/usr/local/bin/rigctld}

# ask "command" [seconds to wait]: send one command over plain TCP, print the reply on one line
ask() {
    local out
    out=$( ( exec 3<>/dev/tcp/$HOST/$PORT && printf '%s\n' "$1" >&3 && timeout "${2:-1}" cat <&3 ) 2>/dev/null | tr '\n' ' ')
    printf '  %-26s -> %s\n' "$1" "${out:-(no reply)}"
}

if [ -z "$SKIP_START" ]; then
    echo "== stopping any running rigctld"
    pkill -x rigctld 2>/dev/null
    sleep 1
    echo "== starting rigctld (log: $LOG)"
    setsid "$RIGCTLD" --model=$MODEL --rig-file=$DEV --port=$PORT --serial-speed=$SPEED \
        -T 127.0.0.1 -vvv >"$LOG" 2>&1 &
    PID=$!
    sleep 4
    if kill -0 $PID 2>/dev/null; then echo "rigctld is running (pid $PID)"; else echo "rigctld EXITED during start-up"; fi
fi

echo "== listening?"
ss -ltn | grep ":$PORT" || echo "nothing is listening on $PORT"

echo "== live values over plain TCP (read-only)"
for c in f m t "l RFPOWER" "l RFPOWER_METER" "l RFPOWER_METER_WATTS" "l SWR" "l ALC" \
         "l STRENGTH" "u TUNER" "u COMP"; do
    ask "$c"
done

echo "== capability dump -> $CAPS"
( exec 3<>/dev/tcp/$HOST/$PORT && printf '\\dump_caps\n' >&3 && timeout 3 cat <&3 ) >"$CAPS" 2>/dev/null
echo "($(wc -l <"$CAPS") lines)"
grep -iE 'get level|set level|get func|set func|vfo op|has get|has set|mode list|model name|version' "$CAPS" | head -40

echo "== Hamlib client, verbose (this is the one that fails)"
timeout 10 "$RIGCTL" -m 2 -r $HOST:$PORT -vvv f 2>&1 | tail -15

if [ -z "$SKIP_START" ]; then
    echo "== is rigctld still alive?"
    if kill -0 $PID 2>/dev/null; then echo "yes"; else echo "NO - rigctld has exited"; fi
    echo "== last lines of the rigctld log"
    tail -12 "$LOG"
    kill $PID 2>/dev/null
    echo "== done (rigctld stopped)"
fi
