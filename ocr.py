"""Volitelny Tesseract OCR wrapper pro predvyplneni LabelDialog.

Import je mekky — kdyz pytesseract/Tesseract neni k dispozici, `available()`
vrati False a volajici aplikace OCR nenabidne.
"""
from __future__ import annotations

import os
from typing import Optional

import numpy as np

try:
    import pytesseract  # type: ignore
    from PIL import Image as PILImage  # type: ignore
    _HAVE = True
except Exception:
    _HAVE = False

# Vlastni cesta k tesseract.exe (Windows). Zmenit podle instalace.
_CANDIDATE_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]
if _HAVE:
    for p in _CANDIDATE_PATHS:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break

# Suits bitmapy se vetsinou OCRnout neda (slozite symboly) — mapujeme manualne
# podle dominantni barvy v obrázku.
SUIT_CHARS = ("h", "d", "c", "s")
RANK_CHARS = ("2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A")


def available() -> bool:
    if not _HAVE:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _rgba_to_pil(pix: np.ndarray) -> "PILImage.Image":
    return PILImage.fromarray(pix, mode="RGBA").convert("RGB")


def guess_text(pix: np.ndarray, whitelist: Optional[str] = None,
               single_char: bool = True) -> str:
    """Vrati tesseract navrh. single_char=True → --psm 10 (jeden znak),
    jinak --psm 7 (single line)."""
    if not available() or pix is None or pix.size == 0:
        return ""
    cfg = "--psm 10" if single_char else "--psm 7"
    if whitelist:
        cfg += f" -c tessedit_char_whitelist={whitelist}"
    try:
        img = _rgba_to_pil(pix)
        # upscale pro mensi glyfy (tesseract ma rad ~30+ px vysku)
        if img.height < 30:
            s = max(2, 40 // max(1, img.height))
            img = img.resize((img.width * s, img.height * s), PILImage.LANCZOS)
        txt = pytesseract.image_to_string(img, lang="eng", config=cfg)
        return txt.strip()
    except Exception:
        return ""


def guess_suit(pix: np.ndarray) -> str:
    """Hrube odvozeni barvy: cervena ~hearts/diamonds, cerna ~clubs/spades.
    Rozliseni hearts vs diamonds a clubs vs spades pres tesseract (symboly)."""
    if pix is None or pix.size == 0:
        return ""
    # kolik pixelu je cervenych vs cernych
    r = pix[..., 0].astype(int)
    g = pix[..., 1].astype(int)
    b = pix[..., 2].astype(int)
    red = ((r > 140) & (g < 100) & (b < 100)).sum()
    black = ((r < 80) & (g < 80) & (b < 80)).sum()
    if red == 0 and black == 0:
        return ""
    # samotny barevny test neodlisi h/d ani c/s; necham tesseract
    t = guess_text(pix, whitelist="hdcs", single_char=True).lower()
    if t and t[0] in SUIT_CHARS:
        return t[0]
    # fallback: vratime aspon barevne spravnou dvojici
    return "h" if red > black else "c"


def guess_rank(pix: np.ndarray) -> str:
    t = guess_text(pix, whitelist="".join(RANK_CHARS) + "10", single_char=True)
    if not t:
        return ""
    t = t.upper()
    if t.startswith("10"):
        return "T"
    if t[0] in RANK_CHARS:
        return t[0]
    return ""


def guess_glyph(pix: np.ndarray, region_hint: str = "") -> str:
    """Univerzalni navrh pro glyph dialog."""
    rl = region_hint.lower()
    if "rank" in rl:
        return guess_rank(pix)
    if "suit" in rl:
        return guess_suit(pix)
    # cisla + carka/tecka (stacky, pot, ante)
    return guess_text(pix, whitelist="0123456789,.$KkMm", single_char=True)
