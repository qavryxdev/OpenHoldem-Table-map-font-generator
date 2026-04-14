"""Tkinter GUI: main control window. Lists poker windows to attach to,
runs the learning loop, and pops modal dialogs for labeling new glyphs /
images. Also has a 'Prune' tab for duplicates.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import numpy as np
from PIL import Image as PILImage
from PIL import ImageTk

import capture
import learn
import tm as tmmod
import transform as tx


SCALE = 4   # glyph preview zoom


def _pil_from_rgba(arr: np.ndarray, scale: int = 1) -> ImageTk.PhotoImage:
    if arr.ndim == 2:
        arr = np.stack([arr * 255] * 3 + [np.full_like(arr, 255)], axis=-1).astype(np.uint8)
    img = PILImage.fromarray(arr, mode="RGBA")
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), PILImage.NEAREST)
    return ImageTk.PhotoImage(img)


class LabelDialog(tk.Toplevel):
    """Modal dialog showing a glyph/image preview and asking for a label."""

    def __init__(self, parent: tk.Misc, title: str, preview_rgba: np.ndarray,
                 context_text: str, default: str = "", scale: int = SCALE,
                 save_tm_cb=None, suit_picker: bool = False,
                 rank_picker: bool = False):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: str | None = None
        self._save_tm_cb = save_tm_cb
        self._photo = _pil_from_rgba(preview_rgba, scale)
        tk.Label(self, image=self._photo, borderwidth=2, relief="groove").pack(padx=8, pady=8)
        tk.Label(self, text=context_text, justify="left",
                 font=("Consolas", 9)).pack(padx=8, pady=4)

        frm = tk.Frame(self)
        frm.pack(padx=8, pady=4, fill="x")
        tk.Label(frm, text="Label:").pack(side="left")
        self.var = tk.StringVar(value=default)
        self.ent = tk.Entry(frm, textvariable=self.var)
        self.ent.pack(side="left", fill="x", expand=True)
        self.ent.focus_set()
        self.ent.bind("<Return>", lambda _e: self._ok())
        self.ent.bind("<Escape>", lambda _e: self._skip())

        if suit_picker:
            suitfrm = tk.Frame(self)
            suitfrm.pack(padx=8, pady=4)
            tk.Label(suitfrm, text="Suit:",
                     font=("Segoe UI", 10, "bold")).pack(side="left", padx=4)
            for ch, label, fg in (
                ("h", "♥ Hearts (h)", "#c0282a"),
                ("d", "♦ Diamonds (d)", "#c0282a"),
                ("c", "♣ Clubs (c)", "#111"),
                ("s", "♠ Spades (s)", "#111"),
            ):
                tk.Button(suitfrm, text=label, fg=fg, width=13,
                          command=lambda c=ch: self._pick_suit(c)
                          ).pack(side="left", padx=2)
            # klavesove zkratky h/d/c/s
            for ch in "hdcs":
                self.bind(f"<Key-{ch}>", lambda _e, c=ch: self._pick_suit(c))

        if rank_picker:
            rankfrm = tk.Frame(self)
            rankfrm.pack(padx=8, pady=4)
            tk.Label(rankfrm, text="Rank:",
                     font=("Segoe UI", 10, "bold")).pack(side="left", padx=4)
            for ch in ("2", "3", "4", "5", "6", "7", "8", "9",
                       "T", "J", "Q", "K", "A"):
                tk.Button(rankfrm, text=ch, width=3,
                          font=("Segoe UI", 10, "bold"),
                          command=lambda c=ch: self._pick_rank(c)
                          ).pack(side="left", padx=1)
            for ch in "23456789tjqkaTJQKA":
                self.bind(f"<Key-{ch}>",
                          lambda _e, c=ch.upper(): self._pick_rank(c))

        btns = tk.Frame(self)
        btns.pack(padx=8, pady=8)
        tk.Button(btns, text="Save (Enter)", command=self._ok).pack(side="left", padx=4)
        tk.Button(btns, text="Skip (Esc)", command=self._skip).pack(side="left", padx=4)
        tk.Button(btns, text="Discard (forever)", command=self._discard).pack(side="left", padx=4)
        if save_tm_cb is not None:
            tk.Button(btns, text="💾 Save TM (Ctrl+S)",
                      command=self._save_tm).pack(side="left", padx=12)
        self.bind("<Control-s>", lambda _e: self._save_tm())

        self.transient(parent)
        # zamerne bez grab_set — umozni zavrit hlavni okno i pri aktivnim popupu
        # dostan okno do popredi a fokus na entry, at user muze rovnou psat
        self.attributes("-topmost", True)
        self.lift()
        self.after(10, lambda: (self.focus_force(), self.ent.focus_set()))
        # kdyz user zavre X, chovej se jako Skip a nech parent zavrit
        self.protocol("WM_DELETE_WINDOW", self._skip)
        self.wait_window(self)

    def _ok(self):
        v = self.var.get().strip()
        if not v:
            return
        self.result = v
        self.destroy()

    def _skip(self):
        self.result = None
        self.destroy()

    def _discard(self):
        self.result = "__DISCARD__"
        self.destroy()

    def _save_tm(self):
        if self._save_tm_cb is not None:
            self._save_tm_cb()

    def _pick_suit(self, ch: str):
        self.result = ch
        self.destroy()

    def _pick_rank(self, ch: str):
        self.result = ch
        self.destroy()


class App(tk.Tk):
    def __init__(self, table: tmmod.Tablemap):
        super().__init__()
        self.title(f"OHLearn - {table.path}")
        self.geometry("900x620")
        self.table = table
        self.hwnd: int | None = None
        self.running = False
        self.worker: threading.Thread | None = None
        self.msg_q: queue.Queue = queue.Queue()
        self.discarded_glyphs: set[tuple[int, str]] = set()   # (group, hexmash)
        self.discarded_images: set[tuple[int, int, bytes]] = set()  # (w,h,bytes)
        self.pending_glyphs: set[tuple[int, str]] = set()    # already in msg_q
        self.pending_images: set[tuple[int, int, bytes]] = set()
        self.image_name_counter = 0

        self._build_ui()
        self._refresh_windows()
        self._check_oversized_t_regions()
        self.after(100, self._pump_messages)
        self.bind_all("<Control-s>", lambda _e: self._save())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _check_oversized_t_regions(self) -> None:
        """Varuje na T regiony presahujici CTransform limit (height 24px).
        T-font hexmash u nich neuspesne (orez top radky) — patri na I-transform."""
        max_h = tx.MAX_SINGLE_CHAR_HEIGHT
        bad: list[tuple[str, int, int, str]] = []
        for name, r in self.table.regions.items():
            if not (r.transform and r.transform[0] == "T"):
                continue
            h = r.bottom - r.top + 1
            w = r.right - r.left + 1
            if h > max_h:
                bad.append((name, w, h, r.transform))
        if not bad:
            return
        banner = "=" * 70
        self.log(banner)
        self.log(f"!!! VAROVANI: {len(bad)} T regionu prilis VELKYCH pro "
                 f"OpenScrape (max height {max_h}px) !!!")
        self.log("T-font scrape u nich NEBUDE fungovat — zmen transform na I.")
        self.log(banner)
        for name, w, h, tr in bad:
            self.log(f"    {name:25} {w}x{h}  ({tr})")
        self.log(banner)
        # Modalni messagebox se seznamem, at to user nemuze prehlednout.
        lines = "\n".join(f"  {n}  {w}x{h}  ({tr})" for n, w, h, tr in bad)
        msg = (f"{len(bad)} T regionu presahuje limit OpenScrape "
               f"(MAX_SINGLE_CHAR_HEIGHT = {max_h} px).\n"
               f"T-font matching u nich nemuze fungovat — zmen transform na I "
               f"v OpenScrape.\n\n{lines}")
        # after/idle aby se dialog objevil az po postaveni hlavniho okna
        self.after(200, lambda: messagebox.showwarning(
            "Pozor: T regiony moc velke pro OpenScrape", msg))

    def _on_close(self):
        self.running = False
        # zavri pripadny otevreny dialog
        for w in self.winfo_children():
            if isinstance(w, tk.Toplevel):
                try:
                    w.destroy()
                except Exception:
                    pass
        self.destroy()

    def _build_ui(self):
        top = tk.Frame(self)
        top.pack(fill="x", padx=6, pady=6)

        tk.Label(top, text="Poker window:").pack(side="left")
        self.window_var = tk.StringVar()
        self.window_cb = ttk.Combobox(top, textvariable=self.window_var, width=70, state="readonly")
        self.window_cb.pack(side="left", padx=4)
        tk.Button(top, text="Refresh", command=self._refresh_windows).pack(side="left", padx=2)

        cmd = tk.Frame(self)
        cmd.pack(fill="x", padx=6, pady=6)
        self.start_btn = tk.Button(cmd, text="Start Learning", command=self._toggle)
        self.start_btn.pack(side="left")
        tk.Button(cmd, text="Save TM", command=self._save).pack(side="left", padx=4)
        tk.Button(cmd, text="Prune duplicates…", command=self._prune).pack(side="left", padx=4)
        self.autotune_var = tk.BooleanVar(value=False)
        tk.Checkbutton(cmd, text="Auto-tune cube (RGB/radius)",
                       variable=self.autotune_var).pack(side="left", padx=12)

        stats = tk.LabelFrame(self, text="Tablemap stats")
        stats.pack(fill="x", padx=6, pady=6)
        self.stats_lbl = tk.Label(stats, justify="left", font=("Consolas", 9))
        self.stats_lbl.pack(anchor="w")
        self._update_stats()

        # --- region selection + log side-by-side ---
        middle = tk.Frame(self)
        middle.pack(fill="both", expand=True, padx=6, pady=6)

        regfrm = tk.LabelFrame(middle, text="Regions (✓ = zaškrtnuto = učit)")
        regfrm.pack(side="left", fill="y")
        btnbar = tk.Frame(regfrm)
        btnbar.pack(fill="x")
        tk.Button(btnbar, text="vše", width=5,
                  command=lambda: self._set_all_regions(True)).pack(side="left")
        tk.Button(btnbar, text="nic", width=5,
                  command=lambda: self._set_all_regions(False)).pack(side="left")
        tk.Button(btnbar, text="jen neuč.", width=9,
                  command=self._select_untrained).pack(side="left")
        # dynamicke tlacitka per transform (T0..Tx, I)
        typebar = tk.Frame(regfrm)
        typebar.pack(fill="x")
        tk.Label(typebar, text="jen typ:").pack(side="left")
        used = sorted({r.transform for r in self.table.regions.values()
                       if r.transform and r.transform[0] in ("T", "I")})
        for tr in used:
            tk.Button(typebar, text=tr, width=4,
                      command=lambda t=tr: self._select_by_transform(t)
                      ).pack(side="left", padx=1)
        self.region_lb = tk.Listbox(regfrm, selectmode="multiple",
                                     width=36, height=20, font=("Consolas", 9),
                                     activestyle="none", exportselection=False)
        self.region_lb.pack(fill="y", expand=True)
        self._populate_regions()

        log = tk.LabelFrame(middle, text="Log")
        log.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.log_txt = tk.Text(log, height=20, font=("Consolas", 9))
        self.log_txt.pack(fill="both", expand=True)

    # ---------- region list ----------

    def _region_status(self, r: tmmod.Region) -> tuple[str, bool]:
        """Return (marker, is_trained). Marker shown in listbox."""
        t = r.transform or ""
        kind = t[0] if t else "?"
        trained = False
        if kind == "T":
            grp = int(t[1]) if len(t) > 1 and t[1].isdigit() else 0
            if 0 <= grp < tmmod.N_FONT_GROUPS and len(self.table.fonts[grp]) > 0:
                trained = True
        elif kind == "I":
            # any image region in TM implies at least one picture was saved
            trained = len(self.table.images) > 0
        else:
            # non-learnable — mark as "n/a"
            return ("·", True)
        return ("●" if trained else "○", trained)

    def _populate_regions(self):
        self.region_lb.delete(0, "end")
        self._region_names: list[str] = []
        for name in sorted(self.table.regions):
            r = self.table.regions[name]
            marker, _ = self._region_status(r)
            self.region_lb.insert("end", f"{marker} {r.transform:<3} {name}")
            self._region_names.append(name)
            self.region_lb.selection_set("end")  # default all selected

    def _refresh_region_markers(self):
        """Update ●/○ without losing selection."""
        sel = set(self.region_lb.curselection())
        self.region_lb.delete(0, "end")
        for i, name in enumerate(self._region_names):
            r = self.table.regions[name]
            marker, _ = self._region_status(r)
            self.region_lb.insert("end", f"{marker} {r.transform:<3} {name}")
            if i in sel:
                self.region_lb.selection_set(i)

    def _set_all_regions(self, on: bool):
        if on:
            self.region_lb.select_set(0, "end")
        else:
            self.region_lb.select_clear(0, "end")

    def _select_untrained(self):
        self.region_lb.select_clear(0, "end")
        for i, name in enumerate(self._region_names):
            r = self.table.regions[name]
            kind = (r.transform or "")[:1]
            if kind not in ("T", "I"):
                continue
            _, trained = self._region_status(r)
            if not trained:
                self.region_lb.select_set(i)

    def _select_by_transform(self, transform: str):
        self.region_lb.select_clear(0, "end")
        for i, name in enumerate(self._region_names):
            if self.table.regions[name].transform == transform:
                self.region_lb.select_set(i)

    def _selected_region_names(self) -> set[str]:
        return {self._region_names[i] for i in self.region_lb.curselection()}

    # ---------- bookkeeping ----------

    _LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB strop — starsi radky odhodime

    def log(self, s: str) -> None:
        self.log_txt.insert("end", s + "\n")
        self.log_txt.see("end")
        try:
            import os
            path = "ohlearn.log"
            line = s + "\n"
            # kdyz by soubor po zapisu presahl limit, rotuj: nech posledni ~1MB
            if os.path.exists(path):
                size = os.path.getsize(path)
                if size + len(line.encode("utf-8")) > self._LOG_MAX_BYTES:
                    keep = self._LOG_MAX_BYTES // 2
                    with open(path, "rb") as f:
                        f.seek(max(0, size - keep))
                        tail = f.read()
                    # zahoď moznou pulku radky na zacatku
                    nl = tail.find(b"\n")
                    if nl >= 0:
                        tail = tail[nl + 1:]
                    with open(path, "wb") as f:
                        f.write(tail)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def _update_stats(self):
        t = self.table
        n_fonts = sum(len(g) for g in t.fonts)
        s = (f"path     : {t.path}\n"
             f"regions  : {len(t.regions)}\n"
             f"symbols  : {len(t.symbols)}\n"
             f"fonts    : {n_fonts} ({', '.join(str(len(g)) for g in t.fonts)})\n"
             f"images   : {len(t.images)}\n")
        self.stats_lbl.configure(text=s)

    def _refresh_windows(self):
        wins = capture.enum_windows()
        # Filter by titletext if set
        filt = []
        needle = self.table.symbols.get("titletext")
        if needle:
            nl = needle.text.lower()
            for h, t in wins:
                if nl in t.lower():
                    filt.append((h, t))
        if not filt:
            filt = wins
        self._wins_list = filt
        self.window_cb["values"] = [f"0x{h:08x}  {t}" for h, t in filt]
        if filt:
            self.window_cb.current(0)

    # ---------- learning loop ----------

    def _toggle(self):
        if self.running:
            self.running = False
            self.start_btn.configure(text="Start Learning")
            return
        idx = self.window_cb.current()
        if idx < 0:
            messagebox.showwarning("ohlearn", "Vyber poker okno.")
            return
        self.hwnd = self._wins_list[idx][0]
        self.running = True
        self.start_btn.configure(text="Stop")
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    def _worker_loop(self):
        interval = 1.0
        while self.running:
            # pausni sbirani kdyz uz je ve fronte hodne cekajicich dialogu
            if self.msg_q.qsize() > 20:
                time.sleep(0.5)
                continue
            try:
                frame = capture.capture_client(self.hwnd)
            except Exception as e:
                self.msg_q.put(("log", f"capture error: {e}"))
                time.sleep(interval)
                continue

            # size gate + auto-crop: CoinPoker (a podobne custom skiny) maluji
            # vlastni titulkovou listu uvnitr Windows-client area. Kdyz je nas
            # snimek vetsi nez targetsize, oriznem ho — vystredime horizontalne
            # a top-offset = (H - target_h) (titlebar je nahore).
            z_target = self.table.sizes.get("targetsize")
            if z_target:
                H, W = frame.shape[:2]
                tw, th = z_target.width, z_target.height
                if (W, H) != (tw, th):
                    if W >= tw and H >= th:
                        x0 = (W - tw) // 2
                        y0 = H - th
                        frame = frame[y0:y0 + th, x0:x0 + tw].copy()
                        if not getattr(self, "_logged_crop", False):
                            self.msg_q.put(("log",
                                f"auto-crop: client {W}x{H} -> {tw}x{th} @ ({x0},{y0})"))
                            self._logged_crop = True
                    else:
                        self.msg_q.put(("log",
                            f"skip: client {W}x{H} mensi nez target {tw}x{th}"))
                        time.sleep(interval)
                        continue

            # iterate regions (filtered by selection)
            active = self._selected_region_names()
            n_t = 0
            n_t_dialogs = 0
            for r in list(self.table.regions.values()):
                if not self.running:
                    break
                if r.name not in active:
                    continue
                try:
                    nt, nd = self._process_region(frame, r)
                    n_t += nt
                    n_t_dialogs += nd
                except Exception as e:
                    import traceback
                    self.msg_q.put(("log",
                        f"ERROR in region {r.name}: {e}\n{traceback.format_exc()}"))
                    continue
            self.msg_q.put(("log",
                f"cycle: {n_t} T regionu, {n_t_dialogs} new glyphs to label"))

            time.sleep(interval)

    def _process_region(self, frame, r) -> tuple[int, int]:
        is_t = bool(r.transform and r.transform[0] == "T")
        n_t = 1 if is_t else 0
        n_new = 0
        if is_t and self.autotune_var.get():
            crop = frame[r.top:r.bottom + 1, r.left:r.right + 1]
            if crop.size > 0:
                old_c, old_r = r.color, r.radius
                if learn.autotune_region_inplace(r, crop):
                    self.msg_q.put(("log",
                        f"autotune {r.name}: 0x{old_c:08x}/{old_r} -> "
                        f"0x{r.color:08x}/{r.radius}"))
        glyphs, images = learn.observe_region(frame, r, self.table)
        if is_t:
            d = dict(learn._last_debug)
            n_new = len(glyphs)
            self.msg_q.put(("log",
                f"  T {r.name:20} mask={d.get('mask_px',0):5d}px "
                f"segs={d.get('n_segs',0):2d} "
                f"existing={d.get('n_existing',0):3d} "
                f"skip_exact={d.get('skipped_exact',0)} "
                f"skip_fuzzy={d.get('skipped_fuzzy',0)} "
                f"new={len(glyphs)} "
                f"cube=0x{r.color:08x}/{r.radius}"))
        for g in glyphs:
            key = (g.font_group, g.hexmash)
            if key in self.discarded_glyphs or key in self.pending_glyphs:
                continue
            if g.hexmash in self.table.fonts[g.font_group]:
                continue
            self.pending_glyphs.add(key)
            self.msg_q.put(("glyph", g))
        for im in images:
            if im.exact_name is not None:
                continue
            key = (im.width, im.height, im.pixels.tobytes())
            if key in self.discarded_images or key in self.pending_images:
                continue
            self.pending_images.add(key)
            self.msg_q.put(("image", im))
        return n_t, n_new

    # ---------- message pump (main thread) ----------

    def _pump_messages(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "glyph":
                    self._handle_glyph(payload)
                elif kind == "image":
                    self._handle_image(payload)
        except queue.Empty:
            pass
        self.after(100, self._pump_messages)

    def _handle_glyph(self, g: learn.GlyphObservation):
        key = (g.font_group, g.hexmash)
        self.pending_glyphs.discard(key)
        # re-check (may have been added/discarded while queued)
        if g.hexmash in self.table.fonts[g.font_group]:
            return
        if key in self.discarded_glyphs:
            return
        ctx = (f"region: {g.region}    font group: t{g.font_group}\n"
               f"hexmash: {g.hexmash}\n"
               f"width  : {len(g.xvals)} cols")
        rlow = g.region.lower()
        is_suit = "suit" in rlow
        is_rank = "rank" in rlow
        dlg = LabelDialog(self, "New glyph", g.pixels, ctx,
                          save_tm_cb=self._save,
                          suit_picker=is_suit,
                          rank_picker=is_rank)
        if dlg.result == "__DISCARD__":
            self.discarded_glyphs.add((g.font_group, g.hexmash))
            self.log(f"[-] discarded glyph t{g.font_group}$ hexmash={g.hexmash}")
            return
        if dlg.result is None:
            # Skip — don't ask about this glyph again in this session
            self.discarded_glyphs.add((g.font_group, g.hexmash))
            return
        label = dlg.result[0]  # OH fonts store a single char
        overwrite = (is_suit and label in ("h", "d", "c", "s")) or \
                    (is_rank and label in ("2","3","4","5","6","7","8","9","T","J","Q","K","A"))
        # pro card regiony (cardface + suit/rank) uloz bitmapu CELEHO regionu
        # jako image — ne orezany font-segment, protoze user vidi v nahledu
        # cely region a ocekava stejnou bitmapu v TM
        # Ulozit font hexmash v kazdem pripade — jinak observer ten samy
        # glyph v dalsim cyklu znovu nabidne k labelovani. Pro cardface regiony
        # navic uloz CELOU bitmapu jako image (odpovida tomu, co vidi user).
        ok = learn.add_glyph(self.table, g, label, overwrite=overwrite)
        self.log(f"[+] t{g.font_group}${label}  hexmash={g.hexmash}  (stored={ok})")
        if (is_suit or is_rank) and "cardface" in rlow:
            h_px, w_px = g.pixels.shape[:2]
            im_obs = learn.ImageObservation(
                region=g.region, width=w_px, height=h_px,
                pixels=g.pixels, exact_name=None, near_matches=[],
            )
            saved = learn.add_image(self.table, im_obs, label, overwrite=overwrite)
            self.log(f"[+] i${saved or label}  {w_px}x{h_px}  (from cardface region)")
        self._update_stats()
        self._refresh_region_markers()

    def _handle_image(self, im: learn.ImageObservation):
        key = (im.width, im.height, im.pixels.tobytes())
        self.pending_images.discard(key)
        if key in self.discarded_images:
            return
        # propose name from region (e.g. "p0cardback" → similar)
        near_str = ", ".join(f"{n} (diff={d})" for n, d in im.near_matches) or "(none)"
        self.image_name_counter += 1
        proposed = f"{im.region}_{self.image_name_counter:03d}"
        ctx = (f"region: {im.region}    size: {im.width}x{im.height}\n"
               f"nearest existing: {near_str}")
        rlow = im.region.lower()
        is_suit = "suit" in rlow
        is_rank = "rank" in rlow
        dlg = LabelDialog(self, "New image", im.pixels, ctx, default="", scale=2,
                          save_tm_cb=self._save,
                          suit_picker=is_suit,
                          rank_picker=is_rank)
        if dlg.result == "__DISCARD__":
            self.discarded_images.add((im.width, im.height, im.pixels.tobytes()))
            self.log(f"[-] discarded image for {im.region}")
            return
        if dlg.result is None:
            self.discarded_images.add((im.width, im.height, im.pixels.tobytes()))
            return
        overwrite = (is_suit and dlg.result in ("h", "d", "c", "s")) or \
                    (is_rank and dlg.result in ("2","3","4","5","6","7","8","9","T","J","Q","K","A"))
        saved = learn.add_image(self.table, im, dlg.result, overwrite=overwrite)
        self.log(f"[+] i${saved or dlg.result}  {im.width}x{im.height}")
        self._update_stats()
        self._refresh_region_markers()

    # ---------- save / prune ----------

    def _save(self):
        try:
            tmmod.save(self.table)
            self.log(f"[S] saved {self.table.path} (backup: .bak)")
        except Exception as e:
            messagebox.showerror("ohlearn", f"save failed: {e}")

    def _prune(self):
        dups = learn.find_duplicate_images(self.table, tol_px=0)
        if not dups:
            messagebox.showinfo("ohlearn", "žádné duplikátní obrázky.")
            return
        msg = "\n".join(f"{a}  ≡  {b}  (diff={d})" for a, b, d in dups[:20])
        if messagebox.askyesno("ohlearn",
                               f"Nalezeno {len(dups)} duplicit:\n\n{msg}\n\n"
                               "Smazat druhý z každé dvojice?"):
            seen = set()
            for a, b, _ in dups:
                if b in seen or a in seen:
                    continue
                learn.remove_image(self.table, b)
                seen.add(b)
            self._update_stats()
            self.log(f"[P] odstraněno {len(seen)} obrázků")
