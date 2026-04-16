"""Detect missing runtime dependencies and offer to install them.

Runs before the GUI starts so we can pip-install missing Python packages
(numpy, Pillow, pywin32, pytesseract). Also checks the Tesseract binary
and English language data; the binary opens an installer page (UAC + EULA),
the language file (~4 MB) downloads and copies in via UAC.

PyInstaller frozen builds skip pip checks since deps are bundled.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from importlib import import_module


_MB_OK = 0x00
_MB_YESNO = 0x04
_MB_ICONINFO = 0x40
_MB_ICONQUESTION = 0x20
_MB_ICONERROR = 0x10
_MB_TOPMOST = 0x40000
_IDYES = 6


def _msgbox_yesno(title: str, text: str) -> bool:
    flags = _MB_YESNO | _MB_ICONQUESTION | _MB_TOPMOST
    return ctypes.windll.user32.MessageBoxW(0, text, title, flags) == _IDYES


def _msgbox_info(title: str, text: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, text, title, _MB_OK | _MB_ICONINFO | _MB_TOPMOST)


def _msgbox_error(title: str, text: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, text, title, _MB_OK | _MB_ICONERROR | _MB_TOPMOST)


PY_DEPS: list[tuple[str, str]] = [
    ("numpy", "numpy>=1.24"),
    ("PIL", "Pillow>=10.0"),
    ("win32gui", "pywin32>=306"),
    ("pytesseract", "pytesseract>=0.3.10"),
]

TESSERACT_INSTALLER_PAGE = "https://github.com/UB-Mannheim/tesseract/wiki"
ENG_TRAINEDDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata"

_WIN_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _missing_python_pkgs() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for mod, spec in PY_DEPS:
        try:
            import_module(mod)
        except ImportError:
            out.append((mod, spec))
    return out


def _pip_install(specs: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", *specs]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0:
            return True, proc.stdout[-800:]
        return False, (proc.stderr or proc.stdout)[-1500:]
    except Exception as e:
        return False, str(e)


def _find_tesseract_exe() -> str | None:
    exe = shutil.which("tesseract")
    if exe:
        return exe
    for p in _WIN_TESSERACT_PATHS:
        if os.path.exists(p):
            return p
    return None


def _download(url: str, name: str) -> str | None:
    import urllib.request
    dest = os.path.join(tempfile.gettempdir(), name)
    try:
        urllib.request.urlretrieve(url, dest)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        return None
    except Exception as e:
        _msgbox_error("Download failed", f"{url}\n\n{e}")
        return None


def _admin_copy(src: str, dst: str) -> bool:
    args = f'/c copy /Y "{src}" "{dst}"'
    try:
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", args, None, 0)
        if int(ret) <= 32:
            return False
    except Exception:
        return False
    for _ in range(20):
        time.sleep(0.5)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return True
    return False


def _check_python_deps() -> bool:
    """Returns True if app should continue, False if it should exit."""
    if getattr(sys, "frozen", False):
        return True
    missing = _missing_python_pkgs()
    if not missing:
        return True
    mods = "\n  ".join(m for m, _ in missing)
    if not _msgbox_yesno(
        "OHLearn - chybi zavislosti",
        f"Chybi tyto Python balicky:\n\n  {mods}\n\nNainstalovat ted pres pip?",
    ):
        _msgbox_info(
            "OHLearn",
            "Bez techto balicku aplikace nepobezi. Nainstaluj rucne:\n\n"
            f"  pip install {' '.join(s for _, s in missing)}",
        )
        return False
    ok, log = _pip_install([s for _, s in missing])
    if ok:
        _msgbox_info("OHLearn", "Balicky nainstalovany. Spust aplikaci znovu.")
        return False
    _msgbox_error("OHLearn - pip selhal", log)
    return False


def _check_tesseract() -> None:
    """Tesseract is optional (OCR feature). Never blocks startup."""
    try:
        import_module("pytesseract")
    except ImportError:
        return
    exe = _find_tesseract_exe()
    if not exe:
        if _msgbox_yesno(
            "OHLearn - Tesseract chybi",
            "Tesseract OCR neni nainstalovany.\n\n"
            "Bez nej budou navrhy znaku pouze z naucenych glyphu.\n\n"
            "Otevrit stranku s instalatorem v prohlizeci?",
        ):
            webbrowser.open(TESSERACT_INSTALLER_PAGE)
        return
    eng = os.path.join(os.path.dirname(exe), "tessdata", "eng.traineddata")
    if os.path.exists(eng):
        return
    if not _msgbox_yesno(
        "OHLearn - eng.traineddata chybi",
        f"Pro Tesseract OCR navrhy je potreba 'eng.traineddata' v:\n  "
        f"{os.path.dirname(eng)}\n\n"
        "Stahnout (~4 MB) a zkopirovat tam? (vyzada UAC)",
    ):
        return
    src = _download(ENG_TRAINEDDATA_URL, "eng.traineddata")
    if not src:
        return
    if _admin_copy(src, eng):
        _msgbox_info("OHLearn", "eng.traineddata nainstalovan. OCR navrhy budou aktivni.")
    else:
        _msgbox_error(
            "OHLearn - kopirovani selhalo",
            f"Zkopiruj rucne (UAC):\n\n  z: {src}\n  do: {eng}",
        )


def check_and_install() -> bool:
    """Returns True if app should continue startup, False to exit."""
    if not _check_python_deps():
        return False
    _check_tesseract()
    return True
