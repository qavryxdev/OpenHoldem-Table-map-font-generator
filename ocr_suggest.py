"""OCR suggestions via pytesseract for pre-segmented glyphs.

Optional dependency: if pytesseract or the Tesseract binary is missing,
suggest_glyph() returns [] and the GUI falls back to manual labeling.
"""
from __future__ import annotations

import os
import shutil
from functools import lru_cache

import numpy as np
from PIL import Image as PILImage

try:
    import pytesseract
    _HAS_PYTESSERACT = True
except ImportError:
    _HAS_PYTESSERACT = False


_WIN_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


_unavailable_reason: str = ""


@lru_cache(maxsize=1)
def is_available() -> bool:
    global _unavailable_reason
    if not _HAS_PYTESSERACT:
        _unavailable_reason = "pytesseract not installed (pip install pytesseract)"
        return False
    exe = shutil.which("tesseract")
    if not exe:
        for p in _WIN_PATHS:
            if os.path.exists(p):
                pytesseract.pytesseract.tesseract_cmd = p
                exe = p
                break
    if not exe:
        _unavailable_reason = "tesseract.exe not found in PATH or Program Files"
        return False
    tessdata = os.path.join(os.path.dirname(exe), "tessdata")
    if not os.path.exists(os.path.join(tessdata, "eng.traineddata")):
        _unavailable_reason = (
            f"eng.traineddata missing from {tessdata} - download from "
            "https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata"
        )
        return False
    return True


def unavailable_reason() -> str:
    return _unavailable_reason


def _mask_to_pil(mask: np.ndarray, scale: int = 6, pad_cells: int = 6) -> PILImage.Image:
    """bool mask (H, W) → black-on-white PIL image, upscaled with whitespace
    margin (Tesseract needs ~10px padding on each side to recognize a glyph).
    """
    h, w = mask.shape
    arr = np.where(mask, 0, 255).astype(np.uint8)
    img = PILImage.fromarray(arr, mode="L")
    img = img.resize((w * scale, h * scale), PILImage.NEAREST)
    pad = pad_cells * scale
    new = PILImage.new("L", (img.width + 2 * pad, img.height + 2 * pad), 255)
    new.paste(img, (pad, pad))
    return new


def suggest_glyph(mask: np.ndarray, whitelist: str | None = None,
                  top_n: int = 3) -> list[tuple[str, float]]:
    """Return [(char, confidence_percent)] sorted desc. Empty if unavailable."""
    if not is_available() or mask.size == 0 or not mask.any():
        return []
    img = _mask_to_pil(mask)
    config_parts = ["--psm 10"]
    if whitelist:
        config_parts += ["-c", f"tessedit_char_whitelist={whitelist}"]
    config = " ".join(config_parts)
    try:
        data = pytesseract.image_to_data(
            img, config=config, output_type=pytesseract.Output.DICT
        )
    except Exception:
        return []
    out: list[tuple[str, float]] = []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        if not txt or txt.isspace():
            continue
        try:
            c = float(conf)
        except (TypeError, ValueError):
            continue
        if c < 0:
            continue
        for ch in txt:
            if not ch.isspace():
                out.append((ch, c))
    out.sort(key=lambda t: t[1], reverse=True)
    seen: set[str] = set()
    dedup: list[tuple[str, float]] = []
    for ch, c in out:
        if ch in seen:
            continue
        seen.add(ch)
        dedup.append((ch, c))
        if len(dedup) >= top_n:
            break
    return dedup
