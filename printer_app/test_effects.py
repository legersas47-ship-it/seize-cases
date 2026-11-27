"""Genere une planche de previsualisation de tous les effets (sans imprimer).
Usage : python test_effects.py [photo.jpg] [encres ex: CMYK, K, CMY]"""
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

import effects

src = sys.argv[1] if len(sys.argv) > 1 else None
inks = sys.argv[2] if len(sys.argv) > 2 else "CMYK"
if src:
    photo = Image.open(src)
else:  # image synthetique : degrades + formes
    photo = Image.new("RGB", (900, 1200))
    d = ImageDraw.Draw(photo)
    for y in range(1200):
        d.line([(0, y), (900, y)], fill=(int(255 * y / 1200), 120, 255 - int(255 * y / 1200)))
    d.ellipse((150, 200, 750, 800), fill=(240, 200, 160))
    d.ellipse((300, 400, 380, 480), fill=(20, 20, 20)); d.ellipse((520, 400, 600, 480), fill=(20, 20, 20))
    d.arc((300, 500, 600, 700), 20, 160, fill=(200, 0, 0), width=18)
    d.rectangle((0, 950, 900, 1200), fill=(255, 230, 0))

size = (580, 820)  # ~ une case A4/16 a 300 dpi
names = [k for k in effects.EFFECTS if k != "aleatoire"]
sheet = Image.new("RGB", (size[0] * 4 + 50, size[1] * 2 + 30), "white")
for i, n in enumerate(names):
    t = time.time()
    img, used = effects.render(photo, size, n, inks, eco=True)
    print(f"{n:8s} {time.time() - t:5.2f}s")
    sheet.paste(img, (10 + (i % 4) * (size[0] + 10), 10 + (i // 4) * (size[1] + 10)))
out = Path(__file__).parent / "data" / f"apercu_effets_{inks}.png"
out.parent.mkdir(exist_ok=True)
sheet.save(out)
print(out)
