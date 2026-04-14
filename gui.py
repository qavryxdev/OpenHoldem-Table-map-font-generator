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
                 context_text: str, default: str = "", scale: int = SCALE):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: str | None = None
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

        self.transient(parent)
        self.grab_set()
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
        self.image_name_counter = 0

        self._build_ui()
        self._refresh_windows()
        self.after(100, self._pump_messages)

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

        stats = tk.LabelFrame(self, text="Tablemap stats")
        stats.pack(fill="x", padx=6, pady=6)
        self.stats_lbl = tk.Label(stats, justify="left", font=("Consolas", 9))
        self.stats_lbl.pack(anchor="w")
        self._update_stats()

        log = tk.LabelFrame(self, text="Log")
        log.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_txt = tk.Text(log, height=20, font=("Consolas", 9))
        self.log_txt.pack(fill="both", expand=True)

    # ---------- bookkeeping ----------

    def log(self, s: str) -> None:
        self.log_txt.insert("end", s + "\n")
        self.log_txt.see("end")

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

            # iterate regions
            for r in list(self.table.regions.values()):
                if not self.running:
                    break
                glyphs, images = learn.observe_region(frame, r, self.table)
                for g in glyphs:
                    key = (g.font_group, g.hexmash)
                    if key in self.discarded_glyphs:
                        continue
                    self.msg_q.put(("glyph", g))
                for im in images:
                    if im.exact_name is not None:
                        continue
                    key = (im.width, im.height, im.pixels.tobytes())
                    if key in self.discarded_images:
                        continue
                    self.msg_q.put(("image", im))

            time.sleep(interval)

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
        # re-check (may have been added while queued)
        if g.hexmash in self.table.fonts[g.font_group]:
            return
        ctx = (f"region: {g.region}    font group: t{g.font_group}\n"
               f"hexmash: {g.hexmash}\n"
               f"width  : {len(g.xvals)} cols")
        dlg = LabelDialog(self, "New glyph", g.pixels, ctx)
        if dlg.result == "__DISCARD__":
            self.discarded_glyphs.add((g.font_group, g.hexmash))
            self.log(f"[-] discarded glyph t{g.font_group}$ hexmash={g.hexmash}")
            return
        if dlg.result is None:
            return
        label = dlg.result[0]  # OH fonts store a single char
        learn.add_glyph(self.table, g, label)
        self.log(f"[+] t{g.font_group}${label}  hexmash={g.hexmash}")
        self._update_stats()

    def _handle_image(self, im: learn.ImageObservation):
        # propose name from region (e.g. "p0cardback" → similar)
        near_str = ", ".join(f"{n} (diff={d})" for n, d in im.near_matches) or "(none)"
        self.image_name_counter += 1
        proposed = f"{im.region}_{self.image_name_counter:03d}"
        ctx = (f"region: {im.region}    size: {im.width}x{im.height}\n"
               f"nearest existing: {near_str}")
        dlg = LabelDialog(self, "New image", im.pixels, ctx, default=proposed, scale=2)
        if dlg.result == "__DISCARD__":
            self.discarded_images.add((im.width, im.height, im.pixels.tobytes()))
            self.log(f"[-] discarded image for {im.region}")
            return
        if dlg.result is None:
            return
        learn.add_image(self.table, im, dlg.result)
        self.log(f"[+] i${dlg.result}  {im.width}x{im.height}")
        self._update_stats()

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
