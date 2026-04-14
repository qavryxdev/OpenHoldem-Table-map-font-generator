"""OpenHoldem tablemap (.tm/.tmn/.osdb2) parser + writer.

Byte-exact format produced by CTablemap::SaveTablemap — any deviation makes
OH/OpenScrape reject or misload the map.

Pixel text format (i$): per pixel 8 hex chars "BBGGRRAA"; in memory we keep
them as (R,G,B,A) tuples.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

VERSION_HEADER = ".osdb2"
N_FONT_GROUPS = 10
N_HASH_GROUPS = 4


@dataclass
class Size:
    name: str
    width: int
    height: int


@dataclass
class Symbol:
    name: str
    text: str


@dataclass
class Region:
    name: str
    left: int
    top: int
    right: int
    bottom: int
    color: int          # 0xAARRGGBB
    radius: int
    transform: str


@dataclass
class Font:
    ch: str             # single char
    x: list[int] = field(default_factory=list)  # per-column hex bitmap

    @property
    def hexmash(self) -> str:
        return "".join(f"{v:x}" for v in self.x)

    @property
    def x_count(self) -> int:
        return len(self.x)


@dataclass
class HashPoint:
    x: int
    y: int


@dataclass
class HashValue:
    name: str
    hash: int


@dataclass
class Image:
    name: str
    width: int
    height: int
    pixels: list[tuple[int, int, int, int]]   # row-major RGBA tuples


@dataclass
class Tablemap:
    path: str = ""
    sizes: dict[str, Size] = field(default_factory=dict)
    symbols: dict[str, Symbol] = field(default_factory=dict)
    regions: dict[str, Region] = field(default_factory=dict)
    fonts: list[dict[str, Font]] = field(
        default_factory=lambda: [dict() for _ in range(N_FONT_GROUPS)]
    )  # fonts[i] keyed by hexmash
    hash_points: list[list[HashPoint]] = field(
        default_factory=lambda: [[] for _ in range(N_HASH_GROUPS)]
    )
    hashes: list[dict[int, HashValue]] = field(
        default_factory=lambda: [dict() for _ in range(N_HASH_GROUPS)]
    )
    images: dict[str, Image] = field(default_factory=dict)
    version_line: str = VERSION_HEADER
    header_comment: str = "// OpenScrape 13.0.2"


_RE_REGION = re.compile(
    r"^r\$(\S+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+([0-9a-fA-F]+)\s+(-?\d+)\s+(\S+)\s*$"
)


def _parse_hex(s: str) -> int:
    return int(s, 16)


def load(path: str) -> Tablemap:
    tm = Tablemap(path=path)
    with open(path, "rb") as f:
        raw = f.read()
    # strip binary tail if present (some .tmn files have garbage after images)
    text = raw.decode("latin-1", errors="replace")
    lines = text.splitlines()

    # collect header comment
    header_comments = []
    for line in lines[:5]:
        if line.startswith("//") and "OpenScrape" in line:
            header_comments.append(line)
    if header_comments:
        tm.header_comment = header_comments[0]
    if lines and lines[0].strip().startswith(".osdb"):
        tm.version_line = lines[0].strip()

    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r\n")
        stripped = line.strip()
        i += 1

        if not stripped or stripped.startswith("//"):
            continue

        prefix = stripped[:2]

        if prefix == "z$":
            parts = stripped[2:].split()
            if len(parts) >= 3:
                tm.sizes[parts[0]] = Size(parts[0], int(parts[1]), int(parts[2]))

        elif prefix == "s$":
            body = stripped[2:]
            m = re.match(r"(\S+)\s*(.*)$", body)
            if m:
                tm.symbols[m.group(1)] = Symbol(m.group(1), m.group(2).rstrip())

        elif prefix == "r$":
            m = _RE_REGION.match(stripped)
            if m:
                tm.regions[m.group(1)] = Region(
                    name=m.group(1),
                    left=int(m.group(2)),
                    top=int(m.group(3)),
                    right=int(m.group(4)),
                    bottom=int(m.group(5)),
                    color=_parse_hex(m.group(6)),
                    radius=int(m.group(7)),
                    transform=m.group(8),
                )

        elif re.match(r"^t\d\$", stripped):
            group = int(stripped[1])
            rest = stripped[3:]
            if not rest:
                continue
            ch = rest[0]
            tail = rest[1:].split()
            xs = [int(t, 16) for t in tail]
            f_ = Font(ch=ch, x=xs)
            tm.fonts[group][f_.hexmash] = f_

        elif re.match(r"^p\d\$", stripped):
            group = int(stripped[1])
            parts = stripped[3:].split()
            if len(parts) >= 2:
                tm.hash_points[group].append(HashPoint(int(parts[0]), int(parts[1])))

        elif re.match(r"^h\d\$", stripped):
            group = int(stripped[1])
            rest = stripped[3:]
            m = re.match(r"(\S+)\s+([0-9a-fA-F]+)$", rest)
            if m:
                h = _parse_hex(m.group(2))
                tm.hashes[group][h] = HashValue(m.group(1), h)

        elif prefix == "i$":
            rest = stripped[2:]
            parts = rest.split()
            if len(parts) < 3:
                continue
            name = parts[0]
            w = int(parts[1])
            h = int(parts[2])
            pixels: list[tuple[int, int, int, int]] = []
            for _ in range(h):
                if i >= len(lines):
                    break
                row = lines[i].rstrip("\r\n")
                i += 1
                if len(row) < w * 8:
                    # not enough chars — corrupt row
                    pixels.extend([(0, 0, 0, 0)] * (w - (len(row) // 8)))
                for x in range(w):
                    hx = row[x * 8:(x + 1) * 8]
                    if len(hx) < 8:
                        pixels.append((0, 0, 0, 0))
                        continue
                    # BBGGRRAA
                    b = int(hx[0:2], 16)
                    g = int(hx[2:4], 16)
                    r = int(hx[4:6], 16)
                    a = int(hx[6:8], 16)
                    pixels.append((r, g, b, a))
            tm.images[name] = Image(name=name, width=w, height=h, pixels=pixels)

    return tm


def save(tm: Tablemap, path: str | None = None) -> None:
    if path is None:
        path = tm.path
    out: list[str] = []
    out.append(f"{tm.version_line}\r\n")
    out.append("\r\n")
    out.append(f"{tm.header_comment}\r\n")
    out.append("\r\n")
    out.append("// 32 bits per pixel\r\n")
    out.append("\r\n")

    def hdr(h: str) -> None:
        out.append("//\r\n")
        out.append(f"// {h}\r\n")
        out.append("//\r\n")
        out.append("\r\n")

    hdr("sizes")
    for z in sorted(tm.sizes.values(), key=lambda z: z.name):
        out.append(f"z${z.name:<16} {z.width}  {z.height}\r\n")
    out.append("\r\n")

    hdr("strings")
    for s in sorted(tm.symbols.values(), key=lambda s: s.name):
        out.append(f"s${s.name:<25} {s.text}\r\n")
    out.append("\r\n")

    hdr("regions")
    for r in sorted(tm.regions.values(), key=lambda r: r.name):
        out.append(
            f"r${r.name:<18} {r.left:3d} {r.top:3d} {r.right:3d} {r.bottom:3d} "
            f"{r.color:8x} {r.radius:4d} {r.transform}\r\n"
        )
    out.append("\r\n")

    hdr("fonts")
    for g in range(N_FONT_GROUPS):
        # sort by (char, hexmash) to stabilize output
        for f_ in sorted(tm.fonts[g].values(), key=lambda f: (f.ch, f.hexmash)):
            line = f"t{g}${f_.ch}"
            for v in f_.x:
                line += f" {v:x}"
            out.append(line + "\r\n")
    out.append("\r\n")

    hdr("points")
    for g in range(N_HASH_GROUPS):
        for p in tm.hash_points[g]:
            out.append(f"p{g}${p.x:4d} {p.y:4d}\r\n")
    out.append("\r\n")

    hdr("hash")
    for g in range(N_HASH_GROUPS):
        for hv in sorted(tm.hashes[g].values(), key=lambda h: h.name):
            out.append(f"h{g}${hv.name:<18} {hv.hash:08x}\r\n")
    out.append("\r\n")

    hdr("images")
    for img in sorted(tm.images.values(), key=lambda i: i.name):
        out.append(f"i${img.name:<16} {img.width:<3d} {img.height:<3d}\r\n")
        for y in range(img.height):
            row = []
            for x in range(img.width):
                r, g, b, a = img.pixels[y * img.width + x]
                row.append(f"{b:02x}{g:02x}{r:02x}{a:02x}")
            out.append("".join(row) + "\r\n")
    out.append("\r\n")

    data = "".join(out).encode("latin-1", errors="replace")
    # atomic write with backup
    if os.path.exists(path):
        bak = path + ".bak"
        try:
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(path, bak)
        except OSError:
            pass
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def find_tm_in_cwd(cwd: str = ".") -> str | None:
    cands = []
    for fn in os.listdir(cwd):
        low = fn.lower()
        if low.endswith((".tm", ".tmn", ".osdb2")):
            cands.append(os.path.join(cwd, fn))
    return cands[0] if cands else None
