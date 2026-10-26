"""Print the panel's version number (the one in ft991a_panel.py), for the build scripts."""
import os
import re

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "ft991a_panel.py")
with open(path, encoding="utf-8") as f:
    match = re.search(r'^__version__ = "([^"]+)"', f.read(), re.M)
print(match.group(1) if match else "0.0.0")
