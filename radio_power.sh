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

# radio_power.sh - switch the FT-991A on or off, or ask whether it answers, over its CAT port.
#
#   bash radio_power.sh on
#   bash radio_power.sh off
#   bash radio_power.sh status
#
# The CAT manual says the PS (power switch) set command, for ON *and* OFF, needs dummy data
# sent first and the command sent between one and two seconds later. This script does that.
#
# Stop rigctld and ser2net first: only one program may use the radio's serial port.
# Settings come from the environment: DEV (default /dev/yaesu-cat), SPEED (38400, which must
# match menu 031 CAT RATE on the radio) and WAIT (seconds after the dummy data, default 1.4).

DEV=${DEV:-/dev/yaesu-cat}
SPEED=${SPEED:-38400}
WAIT=${WAIT:-1.4}
ACTION=${1:-}

case "$ACTION" in
    on)     P=1 ;;
    off)    P=0 ;;
    status) P= ;;
    *) echo "usage: bash radio_power.sh on|off|status"; exit 2 ;;
esac

if [ ! -e "$DEV" ]; then
    echo "$DEV does not exist: the radio's serial port is not present."
    ls -l /dev/serial/by-id/ 2>/dev/null
    exit 1
fi

if command -v fuser >/dev/null 2>&1; then
    HOLDERS=$(fuser "$DEV" 2>/dev/null)
    if [ -n "$HOLDERS" ]; then
        echo "WARNING: another program has $DEV open (process $HOLDERS)."
        echo "         Stop rigctld / ser2net first, or the radio's replies will be taken by it."
    fi
fi

stty -F "$DEV" "$SPEED" cs8 -cstopb -parenb -ixon -ixoff -crtscts raw -echo || exit 1
exec 3<>"$DEV" || exit 1

# read_reply [seconds]: whatever the radio sends back within the time
# (stray null bytes, which the radio can emit while it powers up or down, are dropped here:
# bash cannot hold them in a variable and would print a warning)
read_reply() { timeout "${1:-1}" cat <&3 2>/dev/null | tr -d '\000'; }

read_reply 0.3 >/dev/null          # discard anything stale

printf 'ID;' >&3
ID=$(read_reply 1)
echo "ID ->  ${ID:-(no answer)}   (an FT-991A answers ID0670;)"

if [ -n "$P" ]; then
    echo "sending dummy data, waiting $WAIT s, then PS$P; ..."
    printf ';' >&3
    sleep "$WAIT"
    printf 'PS%s;' "$P" >&3
    sleep 1.5
fi

printf 'PS;' >&3
ST=$(read_reply 1)
echo "PS ->  ${ST:-(no answer)}   (PS1; = on, PS0; = off)"
if [ -z "$ST" ] && [ "$ACTION" = "off" ]; then
    echo "No answer is normal if the radio has just switched off."
fi

exec 3>&-
