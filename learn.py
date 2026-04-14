"""Learning core: given a captured client-area frame and a Tablemap, for each
region produce observations — new glyphs (T regions) or new image candidates
(I regions). Decisions about labeling/saving are made by the GUI layer; this
module only classifies "new" vs "already covered within tolerance".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import tm as tmmod
import transform as tx


@dataclass
class GlyphObservation:
    region: str
    font_group: int
    hexmash: str
    xvals: list[int]
    pixels: np.ndarray     # RGBA crop of the glyph bbox (for display)
    mask_preview: np.ndarray  # bool bbox (H, W), True = foreground


@dataclass
class ImageObservation:
    region: str
    width: int
    height: int
    pixels: np.ndarray     # (H, W, 4) RGBA
    # Matches (exact/near) found among existing i$ records of same size:
    exact_name: str | None = None
    near_matches: list[tuple[str, int]] = field(default_factory=list)  # (name, diff)


DEFAULT_FUZZY_TOLERANCE = 0.05      # OH default
IMAGE_MATCH_FRACTION = 0.65         # OH ITypeTransform: 65% pixel threshold


def _font_tolerance(table: tmmod.Tablemap, group: int) -> float:
    """Read s$tNtype to decide if fuzzy matching is on for this font group.
    Returns 0.0 if plain (exact hexmash only), else weighted-hd tolerance."""
    sym = table.symbols.get(f"t{group}type")
    if sym is None:
        return 0.0
    txt = sym.text.strip().lower()
    if txt == "fuzzy":
        return DEFAULT_FUZZY_TOLERANCE
    try:
        v = float(txt)
        return v if v > 0 else 0.0
    except ValueError:
        return 0.0


def _fuzzy_font_match(seg_xs: list[int], fonts: dict[str, tmmod.Font],
                      tolerance: float) -> str | None:
    """Reproduces GetBestHammingDistance: weighted_hd = sum(hamming) / sum(lit_pixels).
    Returns matched char if best_weighted_hd < tolerance, else None."""
    if tolerance <= 0 or not seg_xs:
        return None
    best_hd = 999999.0
    best_ch: str | None = None
    seg_len = len(seg_xs)
    for f in fonts.values():
        if f.x_count > seg_len:
            continue
        tot = 0.000001
        lit = 0.000001
        for j in range(f.x_count):
            tot += tx.hamming_distance(f.x[j], seg_xs[j])
            lit += bin(f.x[j]).count("1")
        whd = tot / lit
        if whd < tolerance and whd < best_hd:
            best_hd = whd
            best_ch = f.ch
            if tot > lit:
                break
    return best_ch


def _image_matches(obs: np.ndarray, ref: np.ndarray, region_radius: int) -> bool:
    """Same idea as ITypeTransform: count pixels that differ (using region.radius
    as RGB-distance threshold) and accept if failed_pixels < (1 - 65%) of total."""
    if obs.shape != ref.shape:
        return False
    total = obs.shape[0] * obs.shape[1]
    threshold_failed = int(total * (1.0 - IMAGE_MATCH_FRACTION))
    diff = obs.astype(int) - ref.astype(int)
    if region_radius > 0:
        dsq = (diff ** 2).sum(axis=-1)
        failed = int((dsq > region_radius * region_radius).sum())
    else:
        failed = int((diff != 0).any(axis=-1).sum())
    return failed < threshold_failed


def observe_region(frame_bgra: np.ndarray, region: tmmod.Region,
                   table: tmmod.Tablemap,
                   image_tolerance_px: int = 0
                   ) -> tuple[list[GlyphObservation], list[ImageObservation]]:
    crop = frame_bgra[region.top:region.bottom + 1, region.left:region.right + 1]
    if crop.size == 0:
        return [], []

    t = region.transform
    kind = t[0] if t else "N"

    glyphs: list[GlyphObservation] = []
    images: list[ImageObservation] = []

    if kind == "T":
        group = int(t[1]) if len(t) > 1 and t[1].isdigit() else 0
        mask = tx.build_char_mask(crop, region.color, region.radius)  # [W, H]
        segs = tx.segment_chars(mask)
        existing = table.fonts[group]
        fuzzy_tol = _font_tolerance(table, group)
        for s in segs:
            # exact hexmash match — already known
            if s.hexmash in existing:
                continue
            # fuzzy match: if the TM is configured for fuzzy/numeric tolerance
            # and any existing glyph is within weighted-HD tolerance, treat as
            # already covered (don't propose).
            if fuzzy_tol > 0 and _fuzzy_font_match(s.xvals, existing, fuzzy_tol):
                continue
            H = crop.shape[0]
            y0, y1 = max(0, s.y_begin), min(H, s.y_end + 1)
            x0, x1 = s.x_begin, s.x_end + 1
            glyph_rgba = tx.region_to_rgba_array(crop[y0:y1, x0:x1])
            sub_mask = mask[x0:x1, y0:y1].T
            glyphs.append(GlyphObservation(
                region=region.name, font_group=group,
                hexmash=s.hexmash, xvals=s.xvals,
                pixels=glyph_rgba, mask_preview=sub_mask,
            ))

    elif kind == "I":
        w = crop.shape[1]
        h = crop.shape[0]
        if w > tx.MAX_IMAGE_WIDTH or h > tx.MAX_IMAGE_HEIGHT:
            return [], []
        rgba = tx.region_to_rgba_array(crop)
        obs_flat = rgba
        match: str | None = None
        near: list[tuple[str, int]] = []
        for name, img in table.images.items():
            if img.width != w or img.height != h:
                continue
            existing_arr = np.array(img.pixels, dtype=np.uint8).reshape((h, w, 4))
            if _image_matches(obs_flat, existing_arr, region.radius):
                match = name
                break
            diff = tx.image_diff_count(obs_flat, existing_arr, image_tolerance_px)
            near.append((name, diff))
        near.sort(key=lambda x: x[1])
        images.append(ImageObservation(
            region=region.name, width=w, height=h, pixels=rgba,
            exact_name=match, near_matches=near[:5],
        ))

    return glyphs, images


def add_glyph(table: tmmod.Tablemap, obs: GlyphObservation, char: str) -> bool:
    if obs.hexmash in table.fonts[obs.font_group]:
        return False
    table.fonts[obs.font_group][obs.hexmash] = tmmod.Font(ch=char, x=list(obs.xvals))
    return True


def add_image(table: tmmod.Tablemap, obs: ImageObservation, name: str) -> bool:
    if name in table.images:
        return False
    pixels = [tuple(px) for px in obs.pixels.reshape(-1, 4).tolist()]
    pixels = [(int(r), int(g), int(b), int(a)) for r, g, b, a in pixels]
    table.images[name] = tmmod.Image(
        name=name, width=obs.width, height=obs.height, pixels=pixels,
    )
    return True


# ---------------- pruning ----------------

def find_font_collisions(table: tmmod.Tablemap) -> list[tuple[int, str, list[str]]]:
    """Return groups where multiple chars share the SAME hexmash (impossible
    with the current keying — hexmash is the key) or where two hexmashes map
    to DIFFERENT chars but are very similar (low hamming)."""
    out = []
    for g, fonts in enumerate(table.fonts):
        by_char: dict[str, list[str]] = {}
        for hm, f in fonts.items():
            by_char.setdefault(f.ch, []).append(hm)
        for ch, mashes in by_char.items():
            if len(mashes) > 50:
                out.append((g, ch, mashes))
    return out


def find_duplicate_images(table: tmmod.Tablemap, tol_px: int = 0) -> list[tuple[str, str, int]]:
    """Find (name_a, name_b, diff_px) image pairs of same size within tol."""
    dups: list[tuple[str, str, int]] = []
    by_size: dict[tuple[int, int], list[str]] = {}
    for name, img in table.images.items():
        by_size.setdefault((img.width, img.height), []).append(name)
    for (w, h), names in by_size.items():
        arrs = {n: np.array(table.images[n].pixels, dtype=np.uint8).reshape((h, w, 4))
                for n in names}
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                d = tx.image_diff_count(arrs[a], arrs[b], 0)
                if d <= tol_px:
                    dups.append((a, b, d))
    return dups


def remove_image(table: tmmod.Tablemap, name: str) -> bool:
    return table.images.pop(name, None) is not None


def remove_font(table: tmmod.Tablemap, group: int, hexmash: str) -> bool:
    if 0 <= group < tmmod.N_FONT_GROUPS:
        return table.fonts[group].pop(hexmash, None) is not None
    return False
