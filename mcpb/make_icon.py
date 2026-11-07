"""Render mcpb/icon.png (512x512): an eye whose pupil is a play button, above a waveform."""

import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 512
BG = (28, 28, 33, 255)
ORANGE, LIGHT = (232, 140, 72, 255), (245, 196, 150, 255)


def arc_points(x0, x1, y, apex_y, n=60):
    """Points of the circular arc through (x0, y), (x1, y) whose middle is at apex_y."""
    half, sag = (x1 - x0) / 2, abs(y - apex_y)
    r = (half**2 + sag**2) / (2 * sag)
    cx = (x0 + x1) / 2
    cy = apex_y + r if apex_y < y else apex_y - r
    a = math.asin(half / r)
    pts = []
    for i in range(n + 1):
        t = -a + 2 * a * i / n
        dx, dy = r * math.sin(t), r * math.cos(t)
        pts.append((cx + dx, cy - dy if apex_y < y else cy + dy))
    return pts


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((16, 16, S - 16, S - 16), radius=110, fill=BG)

top = arc_points(88, 424, 200, 92)
bottom = arc_points(88, 424, 200, 308)
d.line(top, fill=ORANGE, width=22, joint="curve")
d.line(bottom, fill=ORANGE, width=22, joint="curve")
for x, y in ((88, 200), (424, 200)):
    d.ellipse((x - 11, y - 11, x + 11, y + 11), fill=ORANGE)
d.ellipse((196, 140, 316, 260), fill=ORANGE)
d.polygon([(240, 172), (240, 228), (286, 200)], fill=BG)

heights = [24, 48, 74, 56, 90, 66, 40, 80, 52, 28]
x = 96
for h in heights:
    d.rounded_rectangle((x, 440 - h, x + 18, 440), radius=9, fill=LIGHT)
    x += 34
img.save(Path(__file__).with_name("icon.png"))
print("icon.png")
