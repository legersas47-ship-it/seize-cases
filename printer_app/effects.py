"""Moteur d'effets : transforme une photo en image "imprimable" avec les encres disponibles.

Toutes les fonctions renvoient une image RGB composee uniquement de primaires
d'imprimante (cyan, magenta, jaune, noir) superposees en mode "multiply", pour
que le pilote envoie au maximum des encres pures.
"""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageEnhance

# Couleurs RGB qui correspondent a une encre pure pour le pilote
INK_RGB = {
    "C": (0, 255, 255),
    "M": (255, 0, 255),
    "Y": (255, 255, 0),
    "K": (0, 0, 0),
}
# Angles de trame classiques (evite le moire)
ANGLES = {"C": 15, "M": 75, "Y": 0, "K": 45}

EFFECTS = {
    "comics": "Trame Ben-Day CMJN + contours encres, facon comics annees 60",
    "popart": "Pop art : couleurs aplaties, gros points",
    "eco": "Points noirs clairsemes, minimum d'encre",
    "lignes": "Trame de lignes, une seule encre",
    "grain": "Tramage aleatoire (pointillisme numerique)",
    "contour": "Uniquement les contours, presque pas d'encre",
    "fantome": "Image a peine visible en jaune / encre la plus claire",
    "aleatoire": "Le hasard choisit",
}


# --------------------------------------------------------------------------- utilitaires
def _fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Recadre la photo pour remplir exactement la case (sans deformation)."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))


def _separate(img: Image.Image, inks: str, gcr: float = 0.9) -> dict[str, np.ndarray]:
    """RGB -> couverture d'encre (0..1) pour chaque encre DISPONIBLE.

    Les encres manquantes sont redistribuees : le noir manquant devient un
    noir composite CMJ ; une couleur manquante se reporte (partiellement) sur le noir.
    """
    a = np.asarray(img, dtype=np.float32) / 255.0
    c, m, y = 1 - a[..., 0], 1 - a[..., 1], 1 - a[..., 2]
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]

    # Cas mono-encre : on travaille sur la luminance
    if len(inks) == 1:
        return {inks: np.clip(1 - lum, 0, 1)}

    if "K" in inks:
        k = np.minimum(np.minimum(c, m), y) * gcr
        denom = np.maximum(1 - k, 1e-4)
        c, m, y = (c - k) / denom, (m - k) / denom, (y - k) / denom
    else:
        k = np.zeros_like(c)

    out = {"C": c, "M": m, "Y": y, "K": k}
    for ch in "CMY":
        if ch not in inks:
            if "K" in inks:
                out["K"] = out["K"] + out[ch] * 0.35
            out[ch] = None
    return {ch: np.clip(v, 0, 1) for ch, v in out.items() if ch in inks and v is not None}


def _halftone(cov: np.ndarray, pitch: float, angle: float, max_r: float = 0.72) -> np.ndarray:
    """Trame de points ronds pour un canal. Renvoie un masque 0..1 (1 = encre)."""
    h, w = cov.shape
    src = Image.fromarray((cov * 255).astype(np.uint8), "L")
    rot = src.rotate(angle, expand=True, resample=Image.BILINEAR, fillcolor=0)
    RW, RH = rot.size
    small = rot.resize((max(1, int(RW / pitch)), max(1, int(RH / pitch))), Image.BOX)
    sa = np.asarray(small, dtype=np.float32) / 255.0

    ss = 2  # sur-echantillonnage pour des points nets
    canvas = Image.new("L", (RW * ss, RH * ss), 0)
    d = ImageDraw.Draw(canvas)
    for j in range(sa.shape[0]):
        cy = (j + 0.5) * pitch * ss
        for i in range(sa.shape[1]):
            v = sa[j, i]
            if v < 0.02:
                continue
            r = math.sqrt(v) * pitch * max_r * ss
            cx = (i + 0.5) * pitch * ss
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    canvas = canvas.resize((RW, RH), Image.LANCZOS)
    back = canvas.rotate(-angle, expand=True, resample=Image.BILINEAR, fillcolor=0)
    bw, bh = back.size
    left, top = (bw - w) // 2, (bh - h) // 2
    back = back.crop((left, top, left + w, top + h))
    return np.asarray(back, dtype=np.float32) / 255.0


def _compose(masks: dict[str, np.ndarray], shape: tuple[int, int]) -> Image.Image:
    """Superpose les masques d'encre (multiply) -> image RGB."""
    out = np.ones((shape[0], shape[1], 3), dtype=np.float32)
    for ch, mk in masks.items():
        col = np.array(INK_RGB[ch], dtype=np.float32) / 255.0
        out *= 1 - mk[..., None] * (1 - col)
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8), "RGB")


def _edges(img: Image.Image, thresh: int = 40, thick: int = 3) -> np.ndarray:
    g = ImageOps.grayscale(img).filter(ImageFilter.GaussianBlur(1.6))
    e = g.filter(ImageFilter.FIND_EDGES)
    e = ImageOps.autocontrast(e)
    e = e.point(lambda p: 255 if p > thresh else 0)
    if thick > 1:
        e = e.filter(ImageFilter.MaxFilter(thick if thick % 2 else thick + 1))
    a = np.asarray(e, dtype=np.float32) / 255.0
    b = 4 + thick  # pas de cadre parasite sur les bords de l'image
    a[:b, :] = 0; a[-b:, :] = 0; a[:, :b] = 0; a[:, -b:] = 0
    return a


def _darkest_ink(inks: str) -> str:
    for ch in "KCMY":
        if ch in inks:
            return ch
    return "K"


def _lightest_ink(inks: str) -> str:
    for ch in "YCMK":
        if ch in inks:
            return ch
    return "K"


# --------------------------------------------------------------------------- effets
def fx_comics(img, inks, eco, dpi):
    pitch = dpi / 30  # ~30 points par pouce : bien visible
    img = ImageEnhance.Color(img).enhance(1.5)
    img = ImageEnhance.Contrast(img).enhance(1.25)
    img = ImageOps.posterize(img, 3)
    sep = _separate(img, inks)
    cap = 0.6 if eco else 1.0
    masks = {ch: _halftone(v * cap, pitch, ANGLES[ch]) for ch, v in sep.items()}
    line = _edges(img, thresh=60 if eco else 45, thick=2 if eco else 3)
    k = _darkest_ink(inks)
    masks[k] = np.maximum(masks.get(k, 0), line)
    return _compose(masks, line.shape)


def fx_popart(img, inks, eco, dpi):
    pitch = dpi / 18
    img = ImageEnhance.Color(img).enhance(2.0)
    img = ImageOps.posterize(ImageOps.autocontrast(img), 2)
    sep = _separate(img, inks, gcr=0.5)
    cap = 0.55 if eco else 0.95
    masks = {ch: _halftone(np.round(v * 2) / 2 * cap, pitch, ANGLES[ch], 0.8) for ch, v in sep.items()}
    k = _darkest_ink(inks)
    line = _edges(img, thresh=70, thick=3)
    masks[k] = np.maximum(masks.get(k, 0), line)
    return _compose(masks, line.shape)


def fx_eco(img, inks, eco, dpi):
    pitch = dpi / 22
    g = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=2)
    cov = (1 - np.asarray(g, dtype=np.float32) / 255.0) ** 1.6 * 0.6
    k = _darkest_ink(inks)
    m = _halftone(cov, pitch, 45, max_r=0.55)
    return _compose({k: m}, cov.shape)


def fx_lignes(img, inks, eco, dpi):
    g = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    a = 1 - np.asarray(g, dtype=np.float32) / 255.0
    if eco:
        a *= 0.65
    h, w = a.shape
    period = dpi / 25
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    phase = ((xx * 0.5 + yy * 0.866) % period) / period  # lignes inclinees 30 deg
    wave = np.abs(phase - 0.5) * 2  # 0 au centre de la ligne
    mask = (wave < a).astype(np.float32)
    return _compose({_darkest_ink(inks): mask}, a.shape)


def fx_grain(img, inks, eco, dpi):
    img = ImageEnhance.Contrast(img).enhance(1.2)
    sep = _separate(img, inks)
    cap = 0.5 if eco else 0.85
    masks = {}
    for ch, v in sep.items():
        L = Image.fromarray((255 - v * cap * 255).astype(np.uint8), "L")
        # tramage par diffusion d'erreur, a une resolution plus basse pour un grain visible
        small = L.resize((L.width // 3, L.height // 3), Image.BOX).convert("1")
        big = small.convert("L").resize(L.size, Image.NEAREST)
        masks[ch] = 1 - np.asarray(big, dtype=np.float32) / 255.0
    return _compose(masks, next(iter(masks.values())).shape)


def fx_contour(img, inks, eco, dpi):
    line = _edges(img, thresh=22, thick=2)
    return _compose({_darkest_ink(inks): line}, line.shape)


def fx_fantome(img, inks, eco, dpi):
    pitch = dpi / 40
    g = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=2)
    cov = (1 - np.asarray(g, dtype=np.float32) / 255.0) * 0.45
    ch = _lightest_ink(inks)
    return _compose({ch: _halftone(cov, pitch, ANGLES[ch], 0.6)}, cov.shape)


_FX = {
    "comics": fx_comics,
    "popart": fx_popart,
    "eco": fx_eco,
    "lignes": fx_lignes,
    "grain": fx_grain,
    "contour": fx_contour,
    "fantome": fx_fantome,
}


def render(photo: Image.Image, size: tuple[int, int], effect: str = "comics",
           inks: str = "CMYK", eco: bool = True, dpi: int = 300) -> tuple[Image.Image, str]:
    """Rend la photo a la taille `size` (pixels) avec l'effet demande.

    Renvoie (image, nom_effet_reellement_utilise).
    """
    inks = "".join(ch for ch in "CMYK" if ch in inks.upper()) or "K"
    if effect not in _FX:
        effect = random.choice(list(_FX))
    img = _fit(photo, size)
    return _FX[effect](img, inks, eco, dpi), effect
