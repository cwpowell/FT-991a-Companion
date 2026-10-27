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

"""Print the panel's version number (the one in ft991a_panel.py), for the build scripts."""
import os
import re

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "ft991a_panel.py")
with open(path, encoding="utf-8") as f:
    match = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M)
print(match.group(1) if match else "0.0.0")
