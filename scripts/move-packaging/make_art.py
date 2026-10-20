#!/usr/bin/env python3
"""Icons and installer pictures for Cherry Move, from desktop/icons/cherry-move.svg.

  make_art.py ICON_SVG OUT_DIR

OUT_DIR gets icon-<size>.png (16 to 1024), CherryMove.ico (Windows), CherryMove.icns (Mac) and welcome.bmp
(the picture down the side of the Windows installer's first and last pages)."""
import subprocess
import sys
from pathlib import Path

from PIL import Image

svg, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
icons = {}
for size in SIZES:
    path = out / f"icon-{size}.png"
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), svg, "-o", str(path)], check=True)
    icons[size] = Image.open(path).convert("RGBA")

ico_sizes = (16, 24, 32, 48, 64, 128, 256)
icons[256].save(out / "CherryMove.ico", sizes=[(s, s) for s in ico_sizes],
                append_images=[icons[s] for s in ico_sizes if s != 256])
icons[1024].save(out / "CherryMove.icns", append_images=[icons[s] for s in (16, 32, 64, 128, 256, 512)])

# 164x314 is the size NSIS's welcome page expects; twice that so it stays sharp on high-DPI screens.
w, h = 328, 628
top, bottom = (0x5a, 0x12, 0x38), (0x1a, 0x0a, 0x16)
side = Image.new("RGB", (w, h))
px = side.load()
for y in range(h):
    t = min(1.0, y / (h * 0.7))
    row = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    for x in range(w):
        px[x, y] = row
logo = icons[256].resize((176, 176), Image.LANCZOS)
side.paste(logo, ((w - 176) // 2, 120), logo)
side.save(out / "welcome.bmp")
