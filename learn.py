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

# Tiny-glyph filter: segment sirsi nez TINY_MAX_WIDTH sloupcu nebo vyssi
# nez TINY_MAX_HEIGHT radku se povazuje za "normalni" (cislice, pismeno).
# Segmenty mensi nez oba prahy jsou "tiny" (carka, tecka, artefakt) a
# nabidnou se k uceni JEN kdyz ve stejnem regionu existuje aspoň jeden
# normalni segment — tj. carka je soucasti textoveho pasu s cisly.
TINY_MAX_WIDTH = 4    # vcetne — segment s width<=4 je "tiny"
TINY_MAX_HEIGHT = 5   # vcetne — segment s height<=5 je "tiny"

# Cap na toleranci pri UCENI. TM muze mit s$tNtype=0.35, ale OH/OpenScrape
# uzivatel casto jede na stricter pragu (~0.20). Kdyz learner pouzije raw
# 0.35, preskoci varianty, ktere by pri 0.20 prahu uz nezmatchly — a OH je
# pak nenascrapuje. Tento cap omezi "uz pokryto" rozhodnuti na stricter
# hodnotu, takze se nasbira vic bitmap a OH si vystaci i pri nizsi tolerance.
# Efektivni prah = min(TM_tolerance, LEARN_FUZZY_CAP). 0 = bez capu.
LEARN_FUZZY_CAP = 0.20

_last_debug: dict = {}


def set_learn_fuzzy_cap(v: float) -> None:
    """Runtime override capu z GUI/CLI. v<=0 cap vypne (vrati se k raw TM tol)."""
    global LEARN_FUZZY_CAP
    LEARN_FUZZY_CAP = max(0.0, float(v))


def _font_tolerance(table: tmmod.Tablemap, group: int) -> float:
    """Read s$tNtype to decide if fuzzy matching is on for this font group.
    Returns 0.0 if plain (exact hexmash only), else weighted-hd tolerance.
    Tohle je RAW TM hodnota — co OH scraper pouzije za behu."""
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


def _learn_tolerance(table: tmmod.Tablemap, group: int) -> float:
    """Efektivni prah, ktery LEARNER pouziva pro rozhodnuti "uz pokryto".
    Capovany na LEARN_FUZZY_CAP (kdyz > 0), aby se nasbiralo vic bitmap pro
    scrapery bezici na stricter toleranci nez TM deklaruje."""
    raw = _font_tolerance(table, group)
    if raw <= 0:
        return 0.0
    if LEARN_FUZZY_CAP > 0:
        return min(raw, LEARN_FUZZY_CAP)
    return raw


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


def suggest_from_table(xvals: list[int], fonts: dict[str, tmmod.Font],
                       top_n: int = 3, min_conf: float = 30.0
                       ) -> list[tuple[str, float]]:
    """k-NN over learned glyphs by weighted Hamming distance. Returns
    [(char, confidence_pct)] sorted desc; empty if no candidate beats min_conf.
    Same scoring as OH GetBestHammingDistance but exposed as ranked list."""
    if not xvals or not fonts:
        return []
    seg_len = len(xvals)
    best_per_char: dict[str, float] = {}
    for f in fonts.values():
        if f.x_count > seg_len or f.x_count == 0:
            continue
        tot = 0
        lit = 0
        for j in range(f.x_count):
            tot += bin(f.x[j] ^ xvals[j]).count("1")
            lit += bin(f.x[j]).count("1")
        if lit == 0:
            continue
        whd = tot / lit
        conf = max(0.0, (1.0 - whd) * 100.0)
        if conf < min_conf:
            continue
        if f.ch not in best_per_char or conf > best_per_char[f.ch]:
            best_per_char[f.ch] = conf
    return sorted(best_per_char.items(), key=lambda t: t[1], reverse=True)[:top_n]


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
            _last_debug["fuzzy_tol"] = _font_tolerance(table, group)
            _last_debug["learn_tol"] = _learn_tolerance(table, group)
            _last_debug["skipped_exact"] = 0
            _last_debug["skipped_fuzzy"] = 0
            _last_debug["skipped_blob"] = 1
            return [], []
        segs = tx.segment_chars(mask)
        existing = table.fonts[group]
        tm_tol = _font_tolerance(table, group)
        fuzzy_tol = _learn_tolerance(table, group)

        # tiny-glyph filter: rozdelime segmenty na normalni a tiny
        def _is_tiny(s: tx.CharSegment) -> bool:
            w = s.x_end - s.x_begin + 1
            h = s.y_end - s.y_begin + 1
            return w <= TINY_MAX_WIDTH and h <= TINY_MAX_HEIGHT

        has_normal = any(not _is_tiny(s) for s in segs)
        n_tiny_skipped = 0
        if not has_normal:
            n_tiny_skipped = sum(1 for s in segs if _is_tiny(s))

        _last_debug["region"] = region.name
        _last_debug["mask_px"] = int(mask.sum())
        _last_debug["n_segs"] = len(segs)
        _last_debug["n_existing"] = len(existing)
        _last_debug["fuzzy_tol"] = tm_tol
        _last_debug["learn_tol"] = fuzzy_tol
        _last_debug["skipped_exact"] = 0
        _last_debug["skipped_fuzzy"] = 0
        _last_debug["skipped_tiny"] = n_tiny_skipped
        W_region = crop.shape[1]
        H_region = crop.shape[0]
        region_area = W_region * H_region
        mask_density = (mask.sum() / region_area) if region_area else 0.0
        for s in segs:
            # tiny segment bez kontextu (zadna normalni cislice v regionu) = sum
            if _is_tiny(s) and not has_normal:
                continue
            seg_w = s.x_end - s.x_begin + 1
            # reject "whole-region blob" — zly cube matchnul pozadi
            if seg_w > 0.85 * W_region and mask_density > 0.5:
                _last_debug["skipped_blob"] = _last_debug.get("skipped_blob", 0) + 1
                continue
            # exact hexmash match — already known
            if s.hexmash in existing:
                _last_debug["skipped_exact"] += 1
                continue
            # fuzzy match: if the TM is configured for fuzzy/numeric tolerance
            # and any existing glyph is within weighted-HD tolerance, treat as
            # already covered (don't propose).
            if fuzzy_tol > 0 and _fuzzy_font_match(s.xvals, existing, fuzzy_tol):
                _last_debug["skipped_fuzzy"] += 1
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


# ---------------- pre-save validation ----------------

def pre_validate_glyph(xvals: list[int], label: str,
                       fonts: dict[str, 'tmmod.Font'],
                       tolerance: float) -> list[str]:
    """Zkontroluj jestli ulozeni tohoto glyphu jako 'label' muze zpusobit
    miss-scrape. Vraci seznam varovani (prazdny = OK).

    Kontroly:
    - separator label: podobnost s existujicimi cisilcemi (OH by je zamenovalo)
    - separator label: neobvykla sirka (sirsi nez typicka carka/tecka)
    - digit label: podobnost s existujicimi separatory (carka by matchla cislici)
    """
    warnings: list[str] = []
    seg_len = len(xvals)
    # efektivni tolerance pro krizovou kontrolu
    check_tol = max(tolerance * VALIDATE_TOLERANCE_BOOST, VALIDATE_MIN_TOLERANCE)

    if label in SEPARATOR_CHARS:
        # separator by nemel byt sirsi nez TINY_MAX_WIDTH
        if seg_len > TINY_MAX_WIDTH + 1:
            warnings.append(
                f"unusually wide for '{label}' ({seg_len} cols)")
        # krizova podobnost: sep vs cislice
        for f in fonts.values():
            if not f.ch.isdigit() or f.x_count == 0:
                continue
            cmp_len = min(f.x_count, seg_len)
            tot = sum(_popcount(f.x[j] ^ xvals[j]) for j in range(cmp_len))
            lit = sum(_popcount(f.x[j]) for j in range(cmp_len))
            if lit < 1:
                continue
            whd = tot / lit
            if whd < check_tol:
                warnings.append(
                    f"similar to digit '{f.ch}' (WHD={whd:.2f}<{check_tol:.2f})")
                break

    elif label.isdigit():
        # krizova podobnost: cislice vs existujici separatory
        for f in fonts.values():
            if f.ch not in SEPARATOR_CHARS or f.x_count == 0:
                continue
            if f.x_count > seg_len:
                continue
            tot = sum(_popcount(f.x[j] ^ xvals[j]) for j in range(f.x_count))
            lit = sum(_popcount(f.x[j]) for j in range(f.x_count))
            if lit < 1:
                continue
            whd = tot / lit
            if whd < check_tol:
                warnings.append(
                    f"similar to separator '{f.ch}' (WHD={whd:.2f}<{check_tol:.2f})")
                break

    return warnings


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


# ---------------- separator validation ----------------

SEPARATOR_CHARS = frozenset({',', '.'})

# Validace pouziva vyssi toleranci nez TM, aby odchytila borderline matche
# ktere se pri mirne zmene renderingu mohou "preklopit".
VALIDATE_TOLERANCE_BOOST = 1.50
# Minimalni tolerance pro validaci — i kdyz TM deklaruje exact matching,
# validace vzdy zkusi aspon tento prah aby odhalila potencialni false matche.
VALIDATE_MIN_TOLERANCE = 0.10


@dataclass
class SuspiciousGlyph:
    region: str
    font_group: int
    hexmash: str | None
    matched_char: str
    scraped_text: str
    char_index: int
    reason: str
    pixels: np.ndarray
    mask_preview: np.ndarray


def _popcount(v: int) -> int:
    return bin(v).count("1")


def _col_xval(mask: np.ndarray, x: int, y_top: int, y_bot: int) -> int:
    """Compute column xval using numpy slice — replaces inner y-loop."""
    bits = mask[x, y_top:y_bot + 1]
    if not bits.any():
        return 0
    # bits[0]=y_top .. bits[-1]=y_bot; OH encodes from y_bot upward
    n = len(bits)
    powers = np.int64(1) << np.arange(n - 1, -1, -1, dtype=np.int64)
    return int(bits.astype(np.int64).dot(powers))


def _oh_font_scan(mask: np.ndarray, fonts: dict[str, 'tmmod.Font'],
                  tolerance: float
                  ) -> list[tuple[str, str, int, int, int, int]]:
    """OH-verna simulace DoPlainFontScan: na kazde pozici zkusi vsechny fonty
    a vybere nejlepsi match. Y bounds se pocitaji PER-FONT (jen ze sloupcu
    ktere font pokryva), presne jako OH GetShiftLeftDownIndexes + CalcHexmash.
    Vraci [(char, hexmash, x0, x1, y_top, y_bot)]."""
    W, H = mask.shape   # mask je [W, H] (column-major)
    if W < 2 or H < 2 or not fonts:
        return []

    background = ~mask.any(axis=1)   # bool[W]

    # precompute per-column y bounds
    col_top = np.full(W, H, dtype=int)
    col_bot = np.full(W, -1, dtype=int)
    for x in range(W):
        fg = np.where(mask[x, :])[0]
        if len(fg) > 0:
            col_top[x] = int(fg[0])
            col_bot[x] = int(fg[-1])

    # precompute font lit-pixel counts (invariant)
    font_lit: dict[str, int] = {}
    for hm, f in fonts.items():
        font_lit[hm] = sum(_popcount(v) for v in f.x)

    results: list[tuple[str, str, int, int, int, int]] = []
    pos = 0

    while pos < W:
        if background[pos]:
            pos += 1
            continue

        best_ch: str | None = None
        best_hm: str | None = None
        best_hd = 999999.0
        best_w = 0
        best_yt = 0
        best_yb = 0

        for hm, f in fonts.items():
            fw = f.x_count
            if fw == 0 or pos + fw > W:
                continue
            lit = font_lit[hm]
            if lit < 1:
                continue

            # per-font y bounds — jen ze sloupcu [pos..pos+fw)
            sl = slice(pos, pos + fw)
            tops = col_top[sl]
            bots = col_bot[sl]
            valid = bots >= 0
            if not valid.any():
                continue
            f_yt = int(tops[valid].min())
            f_yb = int(bots[valid].max())

            # compute WHD with numpy-accelerated xval
            tot = 0
            for j in range(fw):
                xval = _col_xval(mask, pos + j, f_yt, f_yb)
                tot += _popcount(f.x[j] ^ xval)
            whd = tot / lit
            if whd < tolerance and whd < best_hd:
                best_hd = whd
                best_ch = f.ch
                best_hm = hm
                best_w = fw
                best_yt = f_yt
                best_yb = f_yb

        if best_ch is not None:
            results.append((best_ch, best_hm, pos, pos + best_w - 1,
                            best_yt, best_yb))
            pos += best_w
        else:
            pos += 1

    return results


def _check_grouping(chars: list[str], sep: str) -> set[int]:
    """Zkontroluj jestli skupiny cislic mezi separatory 'sep' davaji smysl.
    Vraci mnozinu INDEXU separatoru ktere jsou podezrele.

    Validni formaty:
      - jediny separator: cokoliv (12,5  nebo  1.234)
      - 2+ stejnych separatoru: tisicovy format — prvni skupina 1-3 cislice,
        vsechny dalsi skupiny presne 3 cislice (1,234,567)
    """
    sep_positions = [i for i, ch in enumerate(chars) if ch == sep]
    if len(sep_positions) < 2:
        return set()

    boundaries = [-1] + sep_positions + [len(chars)]
    bad: set[int] = set()
    for gi in range(len(boundaries) - 1):
        start = boundaries[gi] + 1
        end = boundaries[gi + 1]
        group = chars[start:end]
        group_digits = all(c.isdigit() for c in group)
        glen = len(group)
        if gi == 0:
            if not group_digits or glen < 1 or glen > 3:
                bad.update(sep_positions)
        else:
            if not group_digits or glen != 3:
                bad.update(sep_positions)
    return bad


def validate_region_fonts(frame_bgra: np.ndarray, region: tmmod.Region,
                          table: 'tmmod.Tablemap') -> list[SuspiciousGlyph]:
    """OH-verna trial-scrape regionu s mirne zvysenou toleranci. Pouziva
    font-lookup scanning (jako OH DoPlainFontScan), ne blob segmentaci.
    Kontroly:
    1) separator bez cislice po obou stranach
    2) vicenasobny stejny separator s nevalidnim seskupenim (napr. 2,6,601)
    3) ruzne separatory vedle sebe (.,  ,.)
    """
    crop = frame_bgra[region.top:region.bottom + 1, region.left:region.right + 1]
    if crop.size == 0:
        return []
    t = region.transform
    if not t or t[0] != "T":
        return []
    group = int(t[1]) if len(t) > 1 and t[1].isdigit() else 0
    mask = tx.build_char_mask(crop, region.color, region.radius)
    region_area = max(1, crop.shape[0] * crop.shape[1])
    if mask.sum() / region_area > 0.9:
        return []
    fonts = table.fonts[group]
    if not fonts:
        return []
    raw_tol = _font_tolerance(table, group)
    tol = max(raw_tol * VALIDATE_TOLERANCE_BOOST, VALIDATE_MIN_TOLERANCE)

    scan = _oh_font_scan(mask, fonts, tol)
    if len(scan) < 2:
        return []

    chars = [ch for ch, _, _, _, _, _ in scan]
    text = ''.join(chars)
    if not any(c.isdigit() for c in text):
        return []

    # --- sbírej podezrele pozice ---
    bad_positions: dict[int, str] = {}

    # 1) separator bez cislice po obou stranach
    for i, ch in enumerate(chars):
        if ch not in SEPARATOR_CHARS:
            continue
        left_ok = i > 0 and chars[i - 1].isdigit()
        right_ok = i < len(chars) - 1 and chars[i + 1].isdigit()
        if not (left_ok and right_ok):
            bad_positions[i] = "separator without digit on both sides"

    # 2) vicenasobne stejne separatory s nevalidnim seskupenim
    for sep in (',', '.'):
        for idx in _check_grouping(chars, sep):
            if idx not in bad_positions:
                bad_positions[idx] = f"invalid digit grouping around '{sep}'"

    # 3) ruzne separatory vedle sebe: ,. nebo .,
    for i in range(len(chars) - 1):
        if chars[i] in SEPARATOR_CHARS and chars[i + 1] in SEPARATOR_CHARS:
            if i not in bad_positions:
                bad_positions[i] = f"adjacent separators: {chars[i]}{chars[i+1]}"
            if (i + 1) not in bad_positions:
                bad_positions[i + 1] = f"adjacent separators: {chars[i]}{chars[i+1]}"

    # --- vytvor SuspiciousGlyph pro kazdy bad index ---
    suspicious: list[SuspiciousGlyph] = []
    H = crop.shape[0]
    for i, reason in sorted(bad_positions.items()):
        ch, hm, x0, x1, y_top, y_bot = scan[i]
        if ch not in SEPARATOR_CHARS:
            continue
        y0c = max(0, y_top)
        y1c = min(H, y_bot + 1)
        glyph_rgba = tx.region_to_rgba_array(crop[y0c:y1c, x0:x1 + 1])
        sub_mask = mask[x0:x1 + 1, y0c:y1c].T
        marked = list(text)
        marked[i] = f'[{marked[i]}]'
        suspicious.append(SuspiciousGlyph(
            region=region.name,
            font_group=group,
            hexmash=hm,
            matched_char=ch,
            scraped_text=''.join(marked),
            char_index=i,
            reason=reason,
            pixels=glyph_rgba,
            mask_preview=sub_mask,
        ))
    return suspicious


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
