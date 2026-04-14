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

# Uceni pouziva prisnejsi hranici nez OH scraper, aby variantу na okraji
# tolerance byly povazovany za NOVE (a ulozeny). Pri ostrem matchi pak OH
# scraper se svou sirsi toleranci pohodlne matchne. Hodnota = zlomek z
# konfigurovane tolerance TM; typicky 0.7 → uc pri 70% prahu.
LEARN_TOLERANCE_RATIO = 0.55

_last_debug: dict = {}


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
    """Reproduces OH GetBestHammingDistance: font.x_count must be <= seg_len,
    font compared as PREFIX of segment (no width slack). weighted_hd =
    sum(hamming) / sum(lit_pixels). Tohle je 1:1 co dela OH scraper — kdyz
    tady non-match, OH taky nezmatchne → glyph se MUSI naucit jako novy.
    """
    if tolerance <= 0 or not seg_xs:
        return None
    best_hd = 999999.0
    best_ch: str | None = None
    seg_len = len(seg_xs)
    for f in fonts.values():
        # OH porovnava font jako PREFIX segmentu — font nikdy nesmi byt sirsi
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
        region_area = max(1, crop.shape[0] * crop.shape[1])
        if mask.sum() / region_area > 0.9:
            # cube matchne skoro vse → rozbity, neucit nic z nej
            _last_debug["region"] = region.name
            _last_debug["mask_px"] = int(mask.sum())
            _last_debug["n_segs"] = 0
            _last_debug["n_existing"] = len(table.fonts[group])
            _last_debug["fuzzy_tol"] = _font_tolerance(table, group) * LEARN_TOLERANCE_RATIO
            _last_debug["skipped_exact"] = 0
            _last_debug["skipped_fuzzy"] = 0
            _last_debug["skipped_blob"] = 1
            return [], []
        segs = tx.segment_chars(mask)
        existing = table.fonts[group]
        fuzzy_tol = _font_tolerance(table, group)
        _last_debug["region"] = region.name
        _last_debug["mask_px"] = int(mask.sum())
        _last_debug["n_segs"] = len(segs)
        _last_debug["n_existing"] = len(existing)
        _last_debug["fuzzy_tol"] = fuzzy_tol * LEARN_TOLERANCE_RATIO
        _last_debug["skipped_exact"] = 0
        _last_debug["skipped_fuzzy"] = 0
        W_region = crop.shape[1]
        H_region = crop.shape[0]
        region_area = W_region * H_region
        mask_density = (mask.sum() / region_area) if region_area else 0.0
        _last_debug["clipped"] = sum(1 for s in segs if s.height_clipped)
        for s in segs:
            seg_w = s.x_end - s.x_begin + 1
            # reject "whole-region blob" — zly cube matchnul pozadi
            if seg_w > 0.85 * W_region and mask_density > 0.5:
                _last_debug["skipped_blob"] = _last_debug.get("skipped_blob", 0) + 1
                continue
            # glyph musel byt usekavan (height > OH limit) — neuspesne by se ulozil
            # zkomoleny hexmash, radeji vubec neposilame do UI k ulozeni
            if s.height_clipped:
                continue
            # exact hexmash match — already known
            if s.hexmash in existing:
                _last_debug["skipped_exact"] += 1
                continue
            # fuzzy match: if the TM is configured for fuzzy/numeric tolerance
            # and any existing glyph is within weighted-HD tolerance, treat as
            # already covered (don't propose).
            # pri uceni pouzivame o neco prisnejsi prah (LEARN_TOLERANCE_RATIO)
            # → hranicni varianty jsou pro nas "nove" a ulozime je, OH scraper
            # je pak v bezne toleranci pohodlne matchne
            learn_tol = fuzzy_tol * LEARN_TOLERANCE_RATIO
            if learn_tol > 0 and _fuzzy_font_match(s.xvals, existing, learn_tol):
                _last_debug["skipped_fuzzy"] += 1
                continue
            # preview = cely region z TM (neorezavat na segment),
            # aby user videl kontext celeho card regionu
            full_rgba = tx.region_to_rgba_array(crop)
            full_mask = mask.T  # [H, W]
            glyphs.append(GlyphObservation(
                region=region.name, font_group=group,
                hexmash=s.hexmash, xvals=s.xvals,
                pixels=full_rgba, mask_preview=full_mask,
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


def add_glyph(table: tmmod.Tablemap, obs: GlyphObservation, char: str,
              overwrite: bool = False) -> bool:
    if obs.hexmash in table.fonts[obs.font_group] and not overwrite:
        return False
    table.fonts[obs.font_group][obs.hexmash] = tmmod.Font(ch=char, x=list(obs.xvals))
    return True


def add_image(table: tmmod.Tablemap, obs: ImageObservation, name: str,
              overwrite: bool = False) -> str:
    """Add image under `name`. If overwrite=False, auto-suffix _2/_3/... on
    collision; if True, replace existing entry. Returns the name stored."""
    if not name:
        return ""
    final = name
    if not overwrite:
        n = 2
        while final in table.images:
            final = f"{name}_{n}"
            n += 1
    pixels = [tuple(px) for px in obs.pixels.reshape(-1, 4).tolist()]
    pixels = [(int(r), int(g), int(b), int(a)) for r, g, b, a in pixels]
    table.images[final] = tmmod.Image(
        name=final, width=obs.width, height=obs.height, pixels=pixels,
    )
    return final


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


# ---------------- auto color/radius tuning ----------------

def autotune_region_color(crop_bgra: np.ndarray) -> tuple[int, int] | None:
    """Bimodal Otsu split on luminance: minority cluster = text foreground.
    Returns (color 0xAARRGGBB, radius) suitable for TM, or None if region is
    monochrome (no text detectable).
    """
    H, W = crop_bgra.shape[:2]
    if H * W < 4:
        return None
    px = crop_bgra.reshape(-1, 4).astype(int)  # B G R A
    lum = (px[:, 2] * 299 + px[:, 1] * 587 + px[:, 0] * 114) // 1000
    lo, hi = int(lum.min()), int(lum.max())
    if hi - lo < 20:
        return None  # monochrome — no text contrast

    # Otsu
    hist, _ = np.histogram(lum, bins=256, range=(0, 256))
    total = lum.size
    sum_all = float((np.arange(256) * hist).sum())
    best_t, best_var = 0, -1.0
    w_b = 0
    sum_b = 0.0
    for t in range(256):
        w_b += int(hist[t])
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * int(hist[t])
        mean_b = sum_b / w_b
        mean_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (mean_b - mean_f) ** 2
        if var > best_var:
            best_var = var
            best_t = t

    below = lum <= best_t
    above = ~below
    n_below = int(below.sum())
    n_above = int(above.sum())
    if n_below == 0 or n_above == 0:
        return None
    text_mask = below if n_below <= n_above else above
    text_px = px[text_mask]
    mean = text_px.mean(axis=0)  # B G R A
    b, g, r, a = (int(round(v)) for v in mean)

    # radius: max RGBA-cube distance from mean within the text cluster, +5 buffer
    diff = text_px - mean
    dist = np.sqrt((diff ** 2).sum(axis=1))
    radius = int(round(dist.max())) + 5

    color = (a << 24) | (r << 16) | (g << 8) | b
    return color, radius


def _unpack(color: int) -> tuple[int, int, int, int]:
    a = (color >> 24) & 0xff
    r = (color >> 16) & 0xff
    g = (color >> 8) & 0xff
    b = color & 0xff
    return a, r, g, b


def _pack(a: int, r: int, g: int, b: int) -> int:
    a = max(0, min(255, int(round(a))))
    r = max(0, min(255, int(round(r))))
    g = max(0, min(255, int(round(g))))
    b = max(0, min(255, int(round(b))))
    return (a << 24) | (r << 16) | (g << 8) | b


MAX_CUBE_RADIUS = 150


def _expand_cube(c1: int, r1: int, c2: int, r2: int) -> tuple[int, int]:
    """Vrati nejmensi 4D ARGB cube ktery pokryva oba puvodni cuby."""
    a1, r1c, g1, b1 = _unpack(c1)
    a2, r2c, g2, b2 = _unpack(c2)
    new_c = _pack((a1 + a2) / 2, (r1c + r2c) / 2, (g1 + g2) / 2, (b1 + b2) / 2)
    da = a1 - a2; dr = r1c - r2c; dg = g1 - g2; db = b1 - b2
    dist = (da * da + dr * dr + dg * dg + db * db) ** 0.5
    new_r = int(round(max(r1, r2) + dist / 2)) + 2
    new_r = min(new_r, MAX_CUBE_RADIUS)
    return new_c, new_r


def autotune_region_inplace(region: tmmod.Region, crop_bgra: np.ndarray) -> bool:
    """Pokud region.color/radius nezasahuje zadny pixel, najdi novou barvu textu.
    Pokud uz region nejakou barvu zachycuje, NEPRENASTAVUJ. Vraci True kdyz
    doslo ke zmene.

    Pro pripady kdy se text meni (aktivni/neaktivni hrac), volej tuto funkci
    opakovane — pokazde kdyz aktualni cube nezachyti nic, expandne se aby
    zahrnoval i novou variantu.
    """
    H, W = crop_bgra.shape[:2]
    area = max(1, H * W)
    if region.radius != 0:
        mask = tx.build_char_mask(crop_bgra, region.color, region.radius)
        density = mask.sum() / area
        # rozbity cube (matchne skoro cely region) — resetuj a udelej cerstvy
        if density > 0.9:
            region.color = 0
            region.radius = 0
        else:
            if mask.any() and len(tx.segment_chars(mask)) > 0:
                return False
            if abs(region.radius) >= 200:
                return False
    tuned = autotune_region_color(crop_bgra)
    if tuned is None:
        return False
    new_color, new_radius = tuned
    new_mask = tx.build_char_mask(crop_bgra, new_color, new_radius)
    if not new_mask.any() or len(tx.segment_chars(new_mask)) == 0:
        return False
    if region.color == 0 and region.radius == 0:
        region.color = new_color
        region.radius = new_radius
    else:
        # expanduj puvodni cube aby pokryl i novou barvu
        region.color, region.radius = _expand_cube(
            region.color, region.radius, new_color, new_radius
        )
    return True
