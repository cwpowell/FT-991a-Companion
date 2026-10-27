#!/usr/bin/env python3
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

"""wavstat.py - summarise a bridge debug WAV: level, continuity and tone frequency.
Usage: python3 wavstat.py ~/bridge_play.wav
"""
import array, math, sys, wave

path = sys.argv[1] if len(sys.argv) > 1 else "bridge_play.wav"
w = wave.open(path, "rb")
rate, n = w.getframerate(), w.getnframes()
a = array.array("h"); a.frombytes(w.readframes(n))
if sys.byteorder == "big":
    a.byteswap()
if not len(a):
    sys.exit("File is empty - nothing was recorded.")

def dbfs(x):
    return 20 * math.log10(x / 32768) if x > 0 else -999

peak = max(max(a), -min(a))
print(f"{path}: {n / rate:.1f} s at {rate} Hz, overall peak {peak} ({dbfs(peak):.1f} dBFS)")

blk = rate // 10  # 100 ms blocks
rms = []
for i in range(0, len(a) - blk + 1, blk):
    seg = a[i:i + blk]
    rms.append(math.sqrt(sum(v * v for v in seg) / blk))
loud = max(rms)
silent = sum(1 for r in rms if r < loud * 0.05)
print(f"loudest 100 ms RMS {loud:.0f} ({dbfs(loud):.1f} dBFS);  "
      f"{silent} of {len(rms)} blocks ({100 * silent / len(rms):.0f}%) are near-silent")

# estimate frequency from zero crossings in the loudest block
i = rms.index(loud) * blk
seg = a[i:i + blk]
zc = sum(1 for j in range(1, len(seg)) if (seg[j - 1] < 0) != (seg[j] < 0))
print(f"estimated frequency in loudest block: about {zc / 2 / (blk / rate):.0f} Hz")

print("\nRMS per second (dBFS):")
per = rate // blk
row = []
for k in range(0, len(rms), per):
    chunk = rms[k:k + per]
    row.append(f"{dbfs(sum(chunk) / len(chunk)):6.1f}")
    if len(row) == 10:
        print(" ".join(row)); row = []
if row:
    print(" ".join(row))
