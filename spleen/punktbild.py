"""Aufruf: python spleen/punktbild.py bild.jpg [weitere.jpg ...]

Abhängigkeit: Pillow (im Import-Werkzeug bereits enthalten).
"""

import sys
from PIL import Image, ImageOps, ImageEnhance
# Braille 2x4 Punkte je Zeichen: sieht aus wie eine Punktwolke.
BITS = [(0,0,0x01),(0,1,0x02),(0,2,0x04),(1,0,0x08),(1,1,0x10),(1,2,0x20),(0,3,0x40),(1,3,0x80)]
def braille(path, cols=100, invert=False):
    im = Image.open(path).convert("L")
    im = ImageOps.autocontrast(im, cutoff=2); im = ImageEnhance.Contrast(im).enhance(1.2)
    w = cols * 2; h = int(im.height / im.width * w) // 4 * 4
    im = im.resize((w, h))
    if invert: im = ImageOps.invert(im)
    im = im.convert("1")  # Floyd-Steinberg
    px = im.load(); rows = []
    for y in range(0, h, 4):
        rows.append("".join(chr(0x2800 + sum(b for dx, dy, b in BITS if px[x+dx, y+dy])) for x in range(0, w, 2)).rstrip("⠀"))
    return "\n".join(rows)
for p in sys.argv[1:]:
    print("== " + p.rsplit("/", 1)[-1]); print(braille(p)); print()
