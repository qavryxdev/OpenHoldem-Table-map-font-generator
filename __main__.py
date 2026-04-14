"""python -m ohlearn [tm_path]

Bez argumentu vezme první *.tm/*.tmn/*.osdb2 v CWD.
"""
import os
import sys

import gui
import tm as tmmod


def main() -> int:
    if len(sys.argv) >= 2:
        path = sys.argv[1]
    else:
        path = tmmod.find_tm_in_cwd(os.getcwd())
    if not path:
        print("chyba: v pracovním adresáři není žádný .tm/.tmn/.osdb2 soubor", file=sys.stderr)
        return 2
    if not os.path.isfile(path):
        print(f"chyba: {path} neexistuje", file=sys.stderr)
        return 2
    print(f"loading {path} ...")
    table = tmmod.load(path)
    print(f"  regions={len(table.regions)}  images={len(table.images)}  "
          f"fonts={sum(len(g) for g in table.fonts)}")
    app = gui.App(table)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
