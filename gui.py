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
                 save_tm_cb=None):
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
        self.after(300, self._warn_card_t_regions)
        self.after(100, self._pump_messages)
        self.bind_all("<Control-s>", lambda _e: self._save())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _warn_card_t_regions(self):
        """Pop a warning if any card region (community c*card* or hole
        p*card*) uses a T-transform. OpenScrape recommends I-transform for
        card faces — T (font scraping) is fragile for variable-color suits
        and anti-aliased pips."""
        import re as _re
        bad: list[str] = []
        for name, r in self.table.regions.items():
            low = name.lower()
            if not (_re.match(r"^c\d+card", low) or _re.match(r"^p\d+card", low)):
                continue
            if r.transform and r.transform[0] == "T":
                bad.append(f"{name}  ({r.transform})")
        if not bad:
            return
        preview = "\n".join(bad[:15])
        more = f"\n... and {len(bad) - 15} more" if len(bad) > 15 else ""
        messagebox.showwarning(
            "Card regions using T-transform",
            "OpenScrape does not recommend T-transform (font scraping) for "
            "card regions — use I-transform (image matching) instead.\n\n"
            "T-scrape depends on stable glyph colors/shapes, but card suits "
            "and rank antialiasing vary between skins and frames, producing "
            "unreliable matches.\n\n"
            "Affected regions:\n" + preview + more +
            "\n\nChange their transform to I in the TM file."
        )

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
                       if r.transform and r.transform[0] == "T"})
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

    def log(self, s: str) -> None:
        self.log_txt.insert("end", s + "\n")
        self.log_txt.see("end")
        try:
            with open("ohlearn.log", "a", encoding="utf-8") as f:
                f.write(s + "\n")
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
        kind = (r.transform or "")[:1]
        # I-transform regiony jsou docasne deaktivovane — uci se jen text (T)
        if kind == "I":
            return (0, 0)
        is_t = kind == "T"
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
        dlg = LabelDialog(self, "New glyph", g.pixels, ctx, save_tm_cb=self._save)
        if dlg.result == "__DISCARD__":
            self.discarded_glyphs.add((g.font_group, g.hexmash))
            self.log(f"[-] discarded glyph t{g.font_group}$ hexmash={g.hexmash}")
            return
        if dlg.result is None:
            # Skip — don't ask about this glyph again in this session
            self.discarded_glyphs.add((g.font_group, g.hexmash))
            return
        label = dlg.result[0]  # OH fonts store a single char
        learn.add_glyph(self.table, g, label)
        self.log(f"[+] t{g.font_group}${label}  hexmash={g.hexmash}")
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
        dlg = LabelDialog(self, "New image", im.pixels, ctx, default=proposed, scale=2,
                          save_tm_cb=self._save)
        if dlg.result == "__DISCARD__":
            self.discarded_images.add((im.width, im.height, im.pixels.tobytes()))
            self.log(f"[-] discarded image for {im.region}")
            return
        if dlg.result is None:
            self.discarded_images.add((im.width, im.height, im.pixels.tobytes()))
            return
        learn.add_image(self.table, im, dlg.result)
        self.log(f"[+] i${dlg.result}  {im.width}x{im.height}")
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
