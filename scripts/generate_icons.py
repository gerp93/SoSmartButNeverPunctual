#!/usr/bin/env python3
"""Generates every icon from the one source mark, assets/logo.png (never hand-export sizes).

    python scripts/generate_icons.py

Writes assets/icon.png (window icon, 256px), assets/icon.ico (Windows exe/installer) and
assets/icon.icns (macOS). Needs Pillow (requirements-dev.txt).
"""
from pathlib import Path

from PIL import Image

assets = Path(__file__).resolve().parent.parent / "assets"
source = Image.open(assets / "logo.png").convert("RGBA")

# pad to a square first, so a non-square master is never stretched
side = max(source.size)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(source, ((side - source.width) // 2, (side - source.height) // 2))

square.resize((256, 256), Image.LANCZOS).save(assets / "icon.png")
square.save(assets / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
square.resize((1024, 1024), Image.LANCZOS).save(assets / "icon.icns")
print("wrote assets/icon.png, icon.ico, icon.icns")
