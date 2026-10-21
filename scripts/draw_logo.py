#!/usr/bin/env python3
"""Draws the source logo (assets/logo.png): a clock stuck at ten past ten under Wi-Fi arcs.

One-off; run `python scripts/draw_logo.py` (needs Pillow, see requirements-dev.txt), then
`python scripts/generate_icons.py` to regenerate every icon size from it. Replace
assets/logo.png with any square image to rebrand; the icon script only needs that file.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
SCALE = 2  # draw at 2x, then shrink for smooth edges
NAVY = (13, 71, 161, 255)
FACE = (250, 247, 235, 255)
INK = (24, 32, 56, 255)
RED = (214, 40, 57, 255)
WIFI = (150, 200, 255, 255)

out = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
s = SIZE * SCALE
img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=NAVY)

cx, cy, r = s * 0.5, s * 0.58, s * 0.31
d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=FACE, outline=INK, width=int(s * 0.018))
for hour in range(12):
    a = math.radians(hour * 30 - 90)
    inner = r * (0.82 if hour % 3 == 0 else 0.90)
    d.line((cx + inner * math.cos(a), cy + inner * math.sin(a), cx + r * 0.96 * math.cos(a), cy + r * 0.96 * math.sin(a)),
           fill=INK, width=int(s * (0.016 if hour % 3 == 0 else 0.009)))


def hand(angle_deg, length, width, color):
    a = math.radians(angle_deg - 90)
    d.line((cx, cy, cx + length * math.cos(a), cy + length * math.sin(a)), fill=color, width=int(width))


hand(305, r * 0.52, s * 0.030, INK)  # hour hand: about ten
hand(60, r * 0.76, s * 0.022, INK)   # minute hand: about ten past
hand(200, r * 0.80, s * 0.010, RED)  # second hand, going nowhere in particular
d.ellipse((cx - s * 0.02, cy - s * 0.02, cx + s * 0.02, cy + s * 0.02), fill=RED)

# Wi-Fi arcs above the clock: smart, and connected 24/7
wx, wy = s * 0.5, s * 0.235
for i, radius in enumerate((s * 0.05, s * 0.095, s * 0.14)):
    d.arc((wx - radius, wy - radius, wx + radius, wy + radius), start=225, end=315, fill=WIFI, width=int(s * 0.022))
d.ellipse((wx - s * 0.014, wy - s * 0.014, wx + s * 0.014, wy + s * 0.014), fill=WIFI)

out.parent.mkdir(parents=True, exist_ok=True)
img.resize((SIZE, SIZE), Image.LANCZOS).save(out)
print(f"wrote {out}")
