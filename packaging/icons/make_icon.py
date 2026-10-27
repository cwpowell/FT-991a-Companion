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

"""Draw the FT-991A panel icon: the front of a transceiver, with an amber frequency display
above a knurled tuning knob, on a deep-blue tile."""
import math
from PIL import Image, ImageDraw, ImageFilter

S = 2048                                   # draw big, then shrink for smooth edges
def sc(v): return int(round(v * S / 1024))

def vgrad(size, top, bottom):
    img = Image.new("RGB", size, top)
    px = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / (size[1] - 1)
        px.line([(0, y), (size[0], y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img

# ---- the tile
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([sc(40), sc(40), S - sc(40), S - sc(40)], radius=sc(215), fill=255)
tile = vgrad((S, S), (38, 86, 138), (9, 24, 44)).convert("RGBA")
gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(gloss).ellipse([sc(-260), sc(-640), S + sc(260), sc(380)], fill=(255, 255, 255, 30))
gloss = gloss.filter(ImageFilter.GaussianBlur(sc(18)))
tile = Image.alpha_composite(tile, gloss)
art = Image.new("RGBA", (S, S), (0, 0, 0, 0))
art.paste(tile, (0, 0), mask)
d = ImageDraw.Draw(art)

# ---- the frequency display
dx0, dy0, dx1, dy1 = sc(135), sc(150), sc(889), sc(385)
d.rounded_rectangle([dx0 - sc(8), dy0 - sc(8), dx1 + sc(8), dy1 + sc(8)], radius=sc(50), fill=(120, 140, 160, 255))
d.rounded_rectangle([dx0, dy0, dx1, dy1], radius=sc(42), fill=(8, 16, 24, 255))

def seven_segment(draw, x, y, w, h, digit, on, off):
    """One 7-segment digit with bevelled segments. (x, y) is the top-left corner."""
    t = w * 0.19                                         # segment thickness
    g = t * 0.12                                         # gap between segments
    def hseg(cy):                                        # a horizontal segment centred on height cy
        return [(x + t * .5 + g, cy), (x + t + g, cy - t / 2), (x + w - t - g, cy - t / 2),
                (x + w - t * .5 - g, cy), (x + w - t - g, cy + t / 2), (x + t + g, cy + t / 2)]
    def vseg(cx, y0, y1):                                # a vertical segment centred on cx
        return [(cx, y0 + t * .5 + g), (cx + t / 2, y0 + t + g), (cx + t / 2, y1 - t - g),
                (cx, y1 - t * .5 - g), (cx - t / 2, y1 - t - g), (cx - t / 2, y0 + t + g)]
    mid = y + h / 2
    segs = {"a": hseg(y + t / 2), "g": hseg(mid), "d": hseg(y + h - t / 2),
            "f": vseg(x + t / 2, y, mid + t / 2), "b": vseg(x + w - t / 2, y, mid + t / 2),
            "e": vseg(x + t / 2, mid - t / 2, y + h), "c": vseg(x + w - t / 2, mid - t / 2, y + h)}
    lit = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
           "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg"}[digit]
    for name, poly in segs.items():
        draw.polygon(poly, fill=on if name in lit else off)

amber, dim = (255, 176, 0, 255), (52, 38, 8, 255)
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
digits, dw, dh, gap = "14250", sc(100), sc(168), sc(34)
total = len(digits) * dw + (len(digits) - 1) * gap
x = (dx0 + dx1) // 2 - total // 2
y = (dy0 + dy1) // 2 - dh // 2
for i, ch in enumerate(digits):
    seven_segment(gd, x, y, dw, dh, ch, (255, 176, 0, 200), (0, 0, 0, 0))
    seven_segment(d, x, y, dw, dh, ch, amber, dim)
    if i == 1:                                           # the decimal point, between "14" and "250"
        px_, py_ = x + dw + gap // 2, y + dh - sc(12)
        d.ellipse([px_ - sc(13), py_ - sc(13), px_ + sc(13), py_ + sc(13)], fill=amber)
        gd.ellipse([px_ - sc(13), py_ - sc(13), px_ + sc(13), py_ + sc(13)], fill=(255, 176, 0, 200))
    x += dw + gap
glow = glow.filter(ImageFilter.GaussianBlur(sc(14)))
art = Image.alpha_composite(art, glow)
d = ImageDraw.Draw(art)

# ---- the tuning knob: a knurled metal rim, a dished face and an indicator dot
cx, cy, R = sc(512), sc(680), sc(250)
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(shadow).ellipse([cx - R, cy - R + sc(34), cx + R, cy + R + sc(34)], fill=(0, 0, 0, 120))
art = Image.alpha_composite(art, shadow.filter(ImageFilter.GaussianBlur(sc(24))))
d = ImageDraw.Draw(art)
for i in range(R, R - sc(54), -1):                       # rim, lit from the top-left
    t = (R - i) / sc(54)
    g = int(222 - 118 * t)
    d.ellipse([cx - i, cy - i, cx + i, cy + i], fill=(g, g + 5, g + 12, 255))
for n in range(72):                                      # knurling
    ang = math.radians(n * 5)
    r1, r2 = R - sc(2), R - sc(34)
    d.line([(cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)), (cx + r2 * math.cos(ang), cy + r2 * math.sin(ang))],
           fill=(58, 68, 82, 255), width=sc(7))
fr = R - sc(58)
for i in range(fr, 0, -1):                               # the face: a soft dish
    t = i / fr
    g = int(34 + 46 * (1 - t) ** 1.6)
    d.ellipse([cx - i, cy - i, cx + i, cy + i], fill=(g, g + 8, g + 18, 255))
d.ellipse([cx - fr, cy - fr, cx + fr, cy + fr], outline=(18, 26, 36, 255), width=sc(6))
ang = math.radians(-128)                                  # indicator dot
ix, iy = cx + (fr - sc(52)) * math.cos(ang), cy + (fr - sc(52)) * math.sin(ang)
d.ellipse([ix - sc(25), iy - sc(25), ix + sc(25), iy + sc(25)], fill=amber)
d.ellipse([ix - sc(25), iy - sc(25), ix + sc(25), iy + sc(25)], outline=(120, 80, 0, 255), width=sc(4))

icon = art.resize((1024, 1024), Image.LANCZOS)
icon.save("ft991a_1024.png")
print("drawn", icon.size)
