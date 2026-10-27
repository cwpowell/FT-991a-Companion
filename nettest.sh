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

# nettest.sh - find out why Hamlib's NET client hangs up on rigctld.
#
# Part 1 needs no radio: it starts rigctld with Hamlib's built-in dummy radio on a spare port
#        and tries the Hamlib client against it.
# Part 2 (optional) times the handshake replies from your real FT-991 daemon, if one is
#        already running on HOST:PORT.
#
# Run:  bash nettest.sh      It prints one report.
# Test the freshly built binaries with:
#   RIGCTLD=$PWD/tests/rigctld RIGCTL=$PWD/tests/rigctl bash ~/nettest.sh
# Nothing here keys the transmitter.

RIGCTLD=${RIGCTLD:-/usr/local/bin/rigctld}
RIGCTL=${RIGCTL:-/usr/local/bin/rigctl}
DUMMY_PORT=${DUMMY_PORT:-4540}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-4532}

# timed HOST PORT 'command': time until the first reply line, and how many lines came back
timed() (
    s=$(date +%s.%N)
    exec 3<>/dev/tcp/$1/$2 2>/dev/null || { echo "  cannot connect to $1:$2"; exit; }
    printf '%s\n' "$3" >&3
    read -t 5 -r first <&3
    e=$(date +%s.%N)
    n=$(( $(timeout 1 cat <&3 | wc -l) + 1 ))
    ms=$(awk "BEGIN{printf \"%.0f\", ($e-$s)*1000}")
    printf '  %-14s first line after %5s ms, %3d lines   first line: %s\n' "$3" "$ms" "$n" "${first:0:40}"
)

echo "== versions"
"$RIGCTLD" --version
"$RIGCTL" --version

echo
echo "== PART 1: dummy radio on port $DUMMY_PORT (no real radio involved)"
setsid "$RIGCTLD" -m 1 -t $DUMMY_PORT -T 127.0.0.1 >/tmp/dummy_rigctld.log 2>&1 &
DP=$!
sleep 2
if ! kill -0 $DP 2>/dev/null; then echo "dummy rigctld did not start:"; tail -5 /tmp/dummy_rigctld.log; fi
echo "-- handshake timing against the dummy daemon:"
for c in '\chk_vfo' '\dump_state' 'f'; do timed 127.0.0.1 $DUMMY_PORT "$c"; done
echo "-- Hamlib client against the dummy daemon:"
timeout 10 "$RIGCTL" -m 2 -r 127.0.0.1:$DUMMY_PORT f
echo "exit code: $?"
kill $DP 2>/dev/null

echo
echo "== PART 2: your real daemon at $HOST:$PORT (skipped if nothing is listening)"
if ( exec 3<>/dev/tcp/$HOST/$PORT ) 2>/dev/null; then
    echo "-- handshake timing against the FT-991 daemon:"
    for c in '\chk_vfo' '\dump_state' 'f'; do timed "$HOST" "$PORT" "$c"; done
    echo "-- Hamlib client against the FT-991 daemon:"
    timeout 10 "$RIGCTL" -m 2 -r $HOST:$PORT f
    echo "exit code: $?"
else
    echo "nothing is listening on $HOST:$PORT - start your daemon first, or ignore this part"
fi
echo
echo "== done"
