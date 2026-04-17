"""python -m ohlearn [tm_path]

Bez argumentu vezme první *.tm/*.tmn/*.osdb2 v CWD (a v adresari kde lezi exe).
Pri frozen exe kazda vyjimka jde do messageboxu — jinak bychom u --windowed
buildu nevideli, proc se aplikace nespustila.
"""
import os
import sys
import traceback


def _error_box(title: str, msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x10)
    except Exception:
        print(msg, file=sys.stderr)


def main() -> int:
    import bootstrap
    if not bootstrap.check_and_install():
        return 0
    import tm as tmmod
    import gui

    # pri frozen exe (PyInstaller) je CWD tam odkud byl exe spusteny.
    # Zkusime CWD a pak adresar exe.
    search_dirs = [os.getcwd()]
    if getattr(sys, "frozen", False):
        search_dirs.append(os.path.dirname(sys.executable))

    if len(sys.argv) >= 2:
        path = sys.argv[1]
    else:
        path = None
        for d in search_dirs:
            p = tmmod.find_tm_in_cwd(d)
            if p:
                path = p
                break
    if not path:
        _error_box(
            "OHLearn",
            "No .tm/.tmn/.osdb2 file found in directory:\n  "
            + "\n  ".join(search_dirs)
            + "\n\nRun the exe from a directory with a TM, or pass the path as an argument."
        )
        return 2
    if not os.path.isfile(path):
        _error_box("OHLearn", f"File does not exist:\n{path}")
        return 2
    print(f"loading {path} ...")
    table = tmmod.load(path)
    print(f"  regions={len(table.regions)}  images={len(table.images)}  "
          f"fonts={sum(len(g) for g in table.fonts)}")
    app = gui.App(table)
    app.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        _error_box("OHLearn — unhandled error", traceback.format_exc())
        sys.exit(1)
