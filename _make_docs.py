"""Generate ODT documentation files (EN + CZ) for ohlearn."""
from __future__ import annotations

import zipfile
from xml.sax.saxutils import escape

MIMETYPE = "application/vnd.oasis.opendocument.text"

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:version="1.2" manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""

META_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
 xmlns:dc="http://purl.org/dc/elements/1.1/" office:version="1.2">
 <office:meta>
  <dc:title>{title}</dc:title>
  <meta:generator>ohlearn docs</meta:generator>
 </office:meta>
</office:document-meta>
"""

STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
 office:version="1.2">
 <office:styles>
  <style:style style:name="Heading1" style:family="paragraph">
   <style:text-properties fo:font-size="18pt" fo:font-weight="bold"/>
   <style:paragraph-properties fo:margin-top="0.3in" fo:margin-bottom="0.1in"/>
  </style:style>
  <style:style style:name="Heading2" style:family="paragraph">
   <style:text-properties fo:font-size="14pt" fo:font-weight="bold"/>
   <style:paragraph-properties fo:margin-top="0.2in" fo:margin-bottom="0.08in"/>
  </style:style>
  <style:style style:name="Code" style:family="paragraph">
   <style:text-properties style:font-name="Consolas" fo:font-size="10pt"/>
   <style:paragraph-properties fo:margin-left="0.25in" fo:background-color="#f4f4f4"/>
  </style:style>
 </office:styles>
</office:document-styles>
"""

CONTENT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
 office:version="1.2">
 <office:body>
  <office:text>
{body}
  </office:text>
 </office:body>
</office:document-content>
"""


def render_body(blocks: list[tuple[str, str]]) -> str:
    out = []
    for kind, txt in blocks:
        t = escape(txt)
        if kind == "h1":
            out.append(f'<text:h text:style-name="Heading1" text:outline-level="1">{t}</text:h>')
        elif kind == "h2":
            out.append(f'<text:h text:style-name="Heading2" text:outline-level="2">{t}</text:h>')
        elif kind == "p":
            out.append(f'<text:p>{t}</text:p>')
        elif kind == "code":
            for line in txt.splitlines() or [""]:
                out.append(f'<text:p text:style-name="Code">{escape(line)}</text:p>')
        elif kind == "li":
            out.append(f'<text:p>• {t}</text:p>')
    return "\n".join(out)


def write_odt(path: str, title: str, blocks: list[tuple[str, str]]) -> None:
    body = render_body(blocks)
    content = CONTENT_TEMPLATE.format(body=body)
    meta = META_TEMPLATE.format(title=escape(title))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype MUST be first and stored (not deflated) per ODT spec
        z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", MANIFEST)
        z.writestr("meta.xml", meta)
        z.writestr("styles.xml", STYLES)
        z.writestr("content.xml", content)


# ============ ENGLISH ============

EN = [
    ("h1", "ohlearn — OpenHoldem Tablemap Learner"),
    ("p", "Python application that inspects a live poker-client window and updates bitmaps (fonts + images) in an OpenHoldem .tm / .tmn / .osdb2 tablemap. It uses the OH CTransform algorithms, so the generated glyphs are byte-compatible with OpenScrape."),

    ("h2", "1. How it works"),
    ("p", "Every second the app captures the client area of the selected window (via Win32 PrintWindow — works even when the window is covered) and iterates over every region in the TM:"),
    ("li", "T regions: build a color-cube mask, segment into per-character bitmaps, compute hexmash (OH CalcHexmash). If the hexmash is new (and not covered by existing fonts within TM-configured fuzzy tolerance), a modal asks for the label."),
    ("li", "I regions: compare the crop against existing i$ images within region.radius-based pixel tolerance (65% pixel match threshold, same as OH ITypeTransform). New crops are offered for naming."),

    ("h2", "2. Installation & launch"),
    ("code", "run.bat            # double-click launcher (creates .deps_ok on first run)\npip install -r requirements.txt   # manual install if run.bat is not used"),
    ("p", "Run from the directory that contains the target .tm file. The app auto-detects the first .tm/.tmn/.osdb2 in CWD."),

    ("h2", "3. Main window"),
    ("li", "Poker window combo — visible top-level windows; pre-filtered by s$titletext if present."),
    ("li", "Start Learning / Stop — toggles the capture/observe worker thread."),
    ("li", "Save TM — writes the tablemap atomically (creates .bak backup). Also bound to Ctrl+S, also works inside label dialogs."),
    ("li", "Prune duplicates — finds pixel-identical images and offers to remove the second of each pair."),
    ("li", "Auto-tune cube (RGB/radius) — when on, automatically detects text color via Otsu and expands the region's color cube to cover new text-color variants (e.g. active vs inactive player). Off by default — the TM stays untouched."),
    ("li", "Learn-tol cap — entry box (default 0.20) that caps the learner's effective fuzzy tolerance independent of s$tNtype. See section 5. Set to 0 to disable the cap and use the raw TM value."),
    ("li", "Region list — shows every region with status markers (● trained, ○ empty, · non-learnable). Filters: vše (all) / nic (none) / jen neuč. (only untrained) / per-transform (T0, T2, I …). Only checked regions are scanned. The list starts with nothing selected — pick regions explicitly via the filter buttons or click-to-select."),

    ("h2", "4. Label dialog"),
    ("p", "For each new glyph or image a modal appears with the scaled preview, the region/font-group context, and the nearest existing matches. Buttons:"),
    ("li", "Save (Enter) — adds to TM under the typed label (one char for fonts)."),
    ("li", "Skip (Esc) — ignores for the rest of the session (won't pop again)."),
    ("li", "Discard (forever) — same as skip (session-only; not persisted)."),
    ("li", "💾 Save TM / Ctrl+S — write the current TM without closing the dialog."),
    ("p", "The dialog is non-modal (main window close still works), always-on-top, and auto-focuses the entry field so you can type immediately."),

    ("h2", "5. Matching policy — parity with OpenScrape + learn-tolerance cap"),
    ("p", "The fuzzy font matcher is byte-identical to OH GetBestHammingDistance: font.x_count must be ≤ segment length, compared as a prefix, weighted_hd = sum(hamming) / sum(lit_pixels), and a match requires whd < s$tNtype tolerance. If the learner accepts a glyph as already-known, OH's scraper will accept it too. When the learner offers a new glyph, OH cannot currently scrape it — learn it to remove the misscrape."),
    ("p", "Learn-tolerance cap: there are two tolerances — the one OpenHoldem uses at scrape time (the raw s$tNtype from the TM, e.g. 0.35) and the one ohlearn uses to decide 'this glyph is already covered, don't prompt for it' (capped at 0.20 by default). When the TM is lenient, the cap keeps the learner stricter so additional bitmap variants are captured. Scrapers configured to run stricter than the TM — for example OpenScrape at 0.20 with a 0.35 TM — then match reliably because every visual variation within 0.20 of some stored glyph has been saved."),
    ("p", "Effective learning threshold = min(s$tNtype, LEARN_FUZZY_CAP). Cap = 0 disables the cap (falls back to raw TM behaviour). The GUI log prefixes the effective value in brackets when it diverges, e.g. tol=0.20(TM=0.35)."),

    ("h2", "6. Tablemap format notes"),
    ("li", "Byte-faithful writer: sort order, spacing and line endings reproduce CTablemap::SaveTablemap exactly."),
    ("li", "Image pixels are stored in text as BBGGRRAA per pixel; in-memory as (R,G,B,A) tuples."),
    ("li", "N_FONT_GROUPS = 10 (supports T6+ regions like mtt_average_stack even though OH officially defines T0–T5)."),

    ("h2", "7. Troubleshooting"),
    ("li", "All glyphs look like a black box → the region's color cube covers almost the entire region (mask density > 90%). The observer skips such cubes entirely; enable Auto-tune to let the app reset and retune them."),
    ("li", "Digits are proposed as a single giant glyph → characters are touching (no background column between them). The segmenter automatically splits at the column with minimum foreground density inside the next MAX_SINGLE_CHAR_WIDTH window."),
    ("li", "Crop mismatch (\"skip: client WxH ≠ target TxT\") → CoinPoker and similar custom skins draw a title bar inside the client area. The app auto-crops to s$targetsize (center horizontally, top offset = H − target_h)."),

    ("h2", "8. Files"),
    ("li", "capture.py — PrintWindow-based window listing + BGRA capture."),
    ("li", "transform.py — OH algorithms: color-cube, char mask, shift-left-down, hexmash, segmentation, image diff."),
    ("li", "learn.py — observation pipeline, fuzzy match, image tolerance, auto-tune, prune helpers."),
    ("li", "tm.py — .tm / .tmn / .osdb2 parser and byte-faithful writer."),
    ("li", "gui.py — Tkinter UI: region list, learning loop, label dialogs, logging."),

    ("h2", "9. Log"),
    ("p", "Every run appends to ohlearn.log in the working directory. Per region you will see mask pixel count, segment count, existing glyphs in group, skip counters (exact / fuzzy / blob) and the current cube. Useful to diagnose why a region does not produce dialogs."),
]

# ============ CZECH ============

CZ = [
    ("h1", "ohlearn — Učitel OpenHoldem tablemapy"),
    ("p", "Python aplikace, která sleduje živé okno poker klienta a aktualizuje bitmapy (fonty + obrázky) v OpenHoldem tablemapě (.tm / .tmn / .osdb2). Používá OH CTransform algoritmy, takže vygenerované glyphy jsou bajt-kompatibilní s OpenScrape."),

    ("h2", "1. Jak to funguje"),
    ("p", "Každou vteřinu aplikace sejme client-area vybraného okna (přes Win32 PrintWindow — funguje i když je okno překryté) a projde všechny regiony v TM:"),
    ("li", "T regiony: vytvoří masku z color-cube, rozsegmentuje na jednotlivé znaky, spočítá hexmash (OH CalcHexmash). Pokud je hexmash nový (a žádný existující font ho nepokrývá v rámci TM fuzzy tolerance), popne se dialog pro zadání znaku."),
    ("li", "I regiony: porovná crop s existujícími i$ obrázky v toleranci daným region.radius (práh 65% shodných pixelů, stejně jako OH ITypeTransform). Nový obrázek se nabídne k pojmenování."),

    ("h2", "2. Instalace a spuštění"),
    ("code", "run.bat            # dvojklik (prvním spuštěním vytvoří .deps_ok)\npip install -r requirements.txt   # manuální instalace bez run.bat"),
    ("p", "Spouštět z adresáře, kde je cílový .tm soubor. Aplikace si sama najde první .tm/.tmn/.osdb2 v CWD."),

    ("h2", "3. Hlavní okno"),
    ("li", "Combobox Poker window — viditelná okna, filtrovaná podle s$titletext."),
    ("li", "Start Learning / Stop — spouští/zastavuje worker thread se snímáním."),
    ("li", "Save TM — atomický zápis tablemapy (vytvoří .bak zálohu). Klávesa Ctrl+S funguje i uvnitř learning dialogu."),
    ("li", "Prune duplicates — najde identické obrázky a nabídne smazání duplicitního."),
    ("li", "Auto-tune cube (RGB/radius) — když zapnuto, automaticky detekuje barvu textu přes Otsu a rozšíří cube regionu aby pokryl novou variantu barvy (např. aktivní vs neaktivní hráč). Defaultně vypnuto, TM zůstane nedotčené."),
    ("li", "Learn-tol cap — pole (default 0.20), které capne efektivní fuzzy toleranci learneru nezávisle na s$tNtype. Viz sekce 5. Nastav na 0 pro vypnutí capu (učí se na raw TM hodnotě)."),
    ("li", "Seznam regionů — zobrazuje každý region se statusem (● naučeno, ○ prázdné, · nelze učit). Filtry: vše / nic / jen neuč. / podle typu transformu (T0, T2, I …). Učí se pouze zaškrtnuté. Seznam startuje s ničím vybraným — vyber regiony explicitně přes filtrační tlačítka nebo klikáním."),

    ("h2", "4. Dialog pro pojmenování"),
    ("p", "Pro každý nový glyph / obrázek se objeví okno s náhledem, kontextem (region, font-group) a seznamem nejbližších existujících match. Tlačítka:"),
    ("li", "Save (Enter) — přidá do TM pod zadaným labelem (jeden znak pro fonty)."),
    ("li", "Skip (Esc) — ignoruje po zbytek session (znovu se neptá)."),
    ("li", "Discard (forever) — totéž co skip (jen session, nepersistuje)."),
    ("li", "💾 Save TM / Ctrl+S — uloží TM bez zavření dialogu."),
    ("p", "Dialog je nemodální (hlavní X funguje), vždy na popředí a automaticky zaměří Entry — můžeš rovnou psát."),

    ("h2", "5. Párování — kompatibilita s OpenScrape + learn-tolerance cap"),
    ("p", "Fuzzy font matcher je bajt-identický s OH GetBestHammingDistance: font.x_count ≤ délka segmentu, porovnán jako prefix, weighted_hd = sum(hamming) / sum(lit_pixels), match vyžaduje whd < s$tNtype tolerance. Když tady matcher uzná glyph za již naučený, OH ho při scrapování taky uzná. Když tady nabídne nový — OH ho zatím nedokáže nascrapovat (= misscrape). Nauč ho a misscrape zmizí."),
    ("p", "Learn-tolerance cap: existují dvě tolerance — jedna co OpenHoldem používá při scrapování (raw s$tNtype z TM, např. 0.35) a druhá co ohlearn používá pro rozhodnutí 'tento glyph už je pokrytý, neptej se' (cap 0.20 defaultně). Když je TM benevolentní, cap drží learner striktnější a nasbírá další bitmap varianty. Scrapery konfigurované striktněji než TM — například OpenScrape na 0.20 s TM 0.35 — pak matchují spolehlivě, protože každá vizuální variace ve vzdálenosti 0.20 od nějakého uloženého glyphu už byla uložena."),
    ("p", "Efektivní learning práh = min(s$tNtype, LEARN_FUZZY_CAP). Cap = 0 ho vypne (chová se jako raw TM). Log v GUI ukazuje efektivní hodnotu v závorce, když se liší od TM, např. tol=0.20(TM=0.35)."),

    ("h2", "6. Formát tablemapy"),
    ("li", "Bajt-přesný writer: řazení, mezery a konce řádků odpovídají CTablemap::SaveTablemap."),
    ("li", "Pixely obrázků v textu jsou BBGGRRAA po pixelu, v paměti jako (R,G,B,A) tuple."),
    ("li", "N_FONT_GROUPS = 10 (podporuje T6+ regiony jako mtt_average_stack, i když OH oficiálně má jen T0–T5)."),

    ("h2", "7. Řešení problémů"),
    ("li", "Všechny glyphy vypadají jako černé obdélníky → cube regionu pokrývá skoro celý region (mask density > 90%). Observer takové cuby ignoruje; zapni Auto-tune, aby je aplikace resetovala a přeladila."),
    ("li", "Cifry se nabízejí jako jeden obří glyph → znaky se dotýkají (žádný background sloupec mezi nimi). Segmenter sám rozřízne ve sloupci s nejmenší hustotou foregroundu v okně MAX_SINGLE_CHAR_WIDTH."),
    ("li", "Rozměr nesedí (\"skip: client WxH ≠ target TxT\") → CoinPoker a podobné skiny kreslí titulek dovnitř client-area. Aplikace auto-cropne na s$targetsize (horizontálně vycentrovat, top offset = H − target_h)."),

    ("h2", "8. Soubory"),
    ("li", "capture.py — výpis oken + BGRA snímání přes PrintWindow."),
    ("li", "transform.py — OH algoritmy: color-cube, char mask, shift-left-down, hexmash, segmentace, image diff."),
    ("li", "learn.py — observation pipeline, fuzzy match, image tolerance, auto-tune, prune."),
    ("li", "tm.py — parser a bajt-přesný writer .tm / .tmn / .osdb2."),
    ("li", "gui.py — Tkinter UI: region list, learning loop, dialog, logging."),

    ("h2", "9. Log"),
    ("p", "Každý běh appenduje do ohlearn.log v pracovním adresáři. Pro každý region uvidíš počet pixelů v masce, počet segmentů, existující glyphy ve skupině, počítadla skip (exact / fuzzy / blob) a aktuální cube. Hodí se pro diagnostiku, proč region nenabízí dialog."),
]


if __name__ == "__main__":
    write_odt("README_en.odt", "ohlearn — English Documentation", EN)
    write_odt("README_cz.odt", "ohlearn — Česká dokumentace", CZ)
    print("ok")
