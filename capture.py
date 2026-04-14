"""Window finding + client-area capture using PrintWindow (works even when
the poker window is covered). Returns numpy uint8 BGRA arrays — same layout as
OpenScrape uses internally (pBits from GetDIBits)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np
import win32con
import win32gui
import win32ui

user32 = ctypes.windll.user32
PW_RENDERFULLCONTENT = 0x00000002


def enum_windows() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                out.append((hwnd, title))
        return True

    win32gui.EnumWindows(cb, None)
    return out


def find_windows(title_substrings: list[str]) -> list[tuple[int, str]]:
    """Find visible windows whose title contains ALL given substrings (case-insensitive)."""
    wins = enum_windows()
    needles = [s.lower() for s in title_substrings if s]
    return [(h, t) for h, t in wins if all(n in t.lower() for n in needles)]


def client_rect(hwnd: int) -> tuple[int, int]:
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def capture_client(hwnd: int) -> np.ndarray:
    """Return BGRA ndarray (H, W, 4) of the client area. Uses PrintWindow so the
    window does not need to be on top."""
    w, h = client_rect(hwnd)
    if w <= 0 or h <= 0:
        raise RuntimeError(f"bad client size {w}x{h}")

    hwnd_dc = win32gui.GetDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)

    # PrintWindow with PW_CLIENTONLY | PW_RENDERFULLCONTENT
    result = user32.PrintWindow(hwnd, save_dc.GetSafeHdc(),
                                win32con.PW_CLIENTONLY | PW_RENDERFULLCONTENT)
    if not result:
        # fallback to BitBlt
        save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)

    bits = bmp.GetBitmapBits(True)       # bytes, BGRA top-down
    arr = np.frombuffer(bits, dtype=np.uint8).reshape((h, w, 4)).copy()

    # cleanup
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return arr


def crop_region(frame: np.ndarray, left: int, top: int, right: int, bottom: int) -> np.ndarray:
    """Crop inclusive region (OH stores right/bottom inclusive)."""
    H, W = frame.shape[:2]
    l = max(0, min(left, W - 1))
    t = max(0, min(top, H - 1))
    r = max(l, min(right, W - 1))
    b = max(t, min(bottom, H - 1))
    return frame[t:b + 1, l:r + 1].copy()
