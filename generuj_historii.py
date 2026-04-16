#!/usr/bin/env python3
"""
generuj_historii.py  --  Vygeneruje git_historie.html z git logu.
Spoustej z adresare CashGame (nebo predej cestu pres --repo).
"""

import subprocess, re, html, argparse, os
from datetime import datetime

# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------

def git(args, cwd):
    r = subprocess.run(
        ["git"] + args,
        cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    return r.stdout.strip()


def get_branch_map(cwd):
    """Vraci dict {commit_hash: set(branch_names)} pro vsechny local branches."""
    branches = git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd).splitlines()
    cmap = {}
    for br in branches:
        for h in git(["rev-list", br], cwd).splitlines():
            cmap.setdefault(h, set()).add(br)
    return cmap, branches


def get_commits(cwd):
    """Vraci list dictu: hash, short, date, subject, body, ins, del, net, files, branches."""
    raw = git(
        ["log", "--all", "--format=__COMMIT__%H|%h|%ai|%s|%b__END__", "--numstat"],
        cwd,
    )
    commits = []
    for block in raw.split("__COMMIT__"):
        block = block.strip()
        if not block:
            continue
        head, *rest = block.split("__END__", 1)
        parts = head.split("|", 3)
        if len(parts) < 4:
            continue
        full_hash, short, datestr, subject_body = parts
        # split subject from body
        sb = subject_body.strip()
        subject = sb.split("\n", 1)[0]
        body = sb.split("\n", 1)[1].strip() if "\n" in sb else ""

        # numstat
        numstat = rest[0].strip() if rest else ""
        ins = dels = 0
        files_changed = set()
        for line in numstat.splitlines():
            m = re.match(r"(\d+|-)\t(\d+|-)\t(.+)", line)
            if m:
                a = int(m.group(1)) if m.group(1) != "-" else 0
                d = int(m.group(2)) if m.group(2) != "-" else 0
                ins += a
                dels += d
                files_changed.add(m.group(3).strip())

        # parse version from subject
        ver_m = re.search(r"v(\d+\.\d+\.\d+\w*)", subject)
        version = ver_m.group(1) if ver_m else ""

        # parse tags (H324-T01, GREF18-M01, etc.)
        tags = sorted(set(re.findall(r"[HG]\w*-[A-Z0-9]+", subject)))

        # parse date
        dt = datetime.fromisoformat(datestr)

        # categorize
        cat = categorize(subject)

        commits.append({
            "hash": full_hash,
            "short": short,
            "date": dt,
            "version": version,
            "subject": subject,
            "body": body,
            "tags": tags,
            "ins": ins,
            "dels": dels,
            "net": ins - dels,
            "files": sorted(files_changed),
            "category": cat,
            "branches": [],
        })
    # dedupe by hash (--all can list same commit once per ref)
    seen = {}
    for c in commits:
        if c["hash"] not in seen:
            seen[c["hash"]] = c
    return list(seen.values())


def categorize(subject):
    s = subject.lower()
    if "fix" in s and ("bug" in s or "shadow" in s or "dead code" in s or "precedence" in s or "ascii" in s):
        return "bugfix"
    if "audit" in s or "generic" in s or "refactor" in s:
        return "audit"
    if "slowplay" in s:
        return "slowplay"
    if "exploit" in s:
        return "exploit"
    if "rewrite" in s or "core" in s:
        return "rewrite"
    if "rename" in s or "initial" in s or "strategy" in s:
        return "infra"
    if "sizing" in s or "split" in s:
        return "sizing"
    return "other"


CAT_COLORS = {
    "bugfix":   ("#fce4ec", "#c62828", "Bug Fix"),
    "audit":    ("#e3f2fd", "#1565c0", "Audit / Refactor"),
    "slowplay": ("#e8f5e9", "#2e7d32", "Slowplay"),
    "exploit":  ("#fff3e0", "#e65100", "Exploit"),
    "rewrite":  ("#ede7f6", "#4527a0", "Core Rewrite"),
    "sizing":   ("#fff8e1", "#f9a825", "Sizing"),
    "infra":    ("#eceff1", "#546e7a", "Infra"),
    "other":    ("#f5f5f5", "#424242", "Other"),
}

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CashGame Bot -- Git Historie</title>
<style>
  :root {{
    --bg: #0d1117; --fg: #c9d1d9; --border: #30363d;
    --header-bg: #161b22; --row-hover: #1c2128;
    --accent: #58a6ff; --green: #3fb950; --red: #f85149;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg); color: var(--fg);
    padding: 24px; line-height: 1.5;
  }}
  h1 {{
    font-size: 22px; font-weight: 600; margin-bottom: 6px;
    color: #fff; letter-spacing: -0.3px;
  }}
  .meta {{
    font-size: 12px; color: #8b949e; margin-bottom: 18px;
  }}
  /* Stats bar */
  .stats {{
    display: flex; gap: 16px; flex-wrap: wrap;
    margin-bottom: 18px;
  }}
  .stat-card {{
    background: var(--header-bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 18px; min-width: 120px;
  }}
  .stat-card .label {{ font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat-card .value {{ font-size: 22px; font-weight: 700; color: #fff; }}
  /* Filter */
  .filter-bar {{
    display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px;
  }}
  .filter-btn {{
    border: 1px solid var(--border); background: var(--header-bg);
    color: var(--fg); padding: 4px 12px; border-radius: 16px;
    font-size: 12px; cursor: pointer; transition: all 0.15s;
  }}
  .filter-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
  .filter-btn.active {{ background: var(--accent); color: #0d1117; border-color: var(--accent); font-weight: 600; }}
  /* Search */
  .search {{
    width: 100%; max-width: 400px; padding: 6px 12px;
    background: var(--header-bg); border: 1px solid var(--border);
    border-radius: 6px; color: var(--fg); font-size: 13px;
    margin-bottom: 14px; outline: none;
  }}
  .search:focus {{ border-color: var(--accent); }}
  /* Table */
  .table-wrap {{
    overflow-x: auto; border: 1px solid var(--border); border-radius: 8px;
  }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 13px;
  }}
  thead {{ position: sticky; top: 0; z-index: 2; }}
  th {{
    background: var(--header-bg); color: #8b949e;
    padding: 10px 12px; text-align: left; font-weight: 600;
    border-bottom: 1px solid var(--border); white-space: nowrap;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px;
    cursor: pointer; user-select: none;
  }}
  th:hover {{ color: var(--accent); }}
  th .arrow {{ font-size: 10px; margin-left: 4px; opacity: 0.4; }}
  th.sorted .arrow {{ opacity: 1; color: var(--accent); }}
  td {{
    padding: 8px 12px; border-bottom: 1px solid var(--border);
    vertical-align: top;
  }}
  tr:hover td {{ background: var(--row-hover); }}
  tr.hidden {{ display: none; }}
  /* Cells */
  .hash {{
    font-family: 'Cascadia Code', 'Fira Code', monospace;
    font-size: 12px; color: var(--accent);
  }}
  .date {{ white-space: nowrap; color: #8b949e; font-size: 12px; }}
  .version {{
    font-family: monospace; font-weight: 700; color: #fff;
    white-space: nowrap;
  }}
  .subject {{ max-width: 520px; }}
  .subject summary {{ cursor: pointer; }}
  .subject .body {{
    margin-top: 6px; font-size: 12px; color: #8b949e;
    white-space: pre-wrap; max-height: 200px; overflow-y: auto;
  }}
  .badge {{
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600; white-space: nowrap;
    margin: 1px 2px;
  }}
  .tag {{
    display: inline-block; padding: 1px 6px; border-radius: 4px;
    font-size: 10px; font-family: monospace; margin: 1px 1px;
    background: #21262d; color: #8b949e; border: 1px solid var(--border);
  }}
  .branch {{
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    font-size: 10px; font-family: monospace; font-weight: 600;
    margin: 1px 2px; color: #fff; white-space: nowrap;
  }}
  .ins {{ color: var(--green); font-family: monospace; font-size: 12px; }}
  .del {{ color: var(--red); font-family: monospace; font-size: 12px; }}
  .net {{ font-family: monospace; font-size: 12px; font-weight: 600; }}
  .net.pos {{ color: var(--green); }}
  .net.neg {{ color: var(--red); }}
  .net.zero {{ color: #8b949e; }}
  .bar {{
    display: inline-block; height: 8px; border-radius: 2px;
    vertical-align: middle;
  }}
  .bar-ins {{ background: var(--green); }}
  .bar-del {{ background: var(--red); }}
  .files {{
    font-size: 11px; color: #8b949e; font-family: monospace;
  }}
  /* Footer */
  footer {{
    margin-top: 18px; font-size: 11px; color: #484f58;
    text-align: center;
  }}
</style>
</head>
<body>

<h1>CashGame Bot -- Git Historie</h1>
<p class="meta">Vygenerovano: {generated} &nbsp;|&nbsp; Repo: CashGame &nbsp;|&nbsp; Vetve: {branches_list}</p>

<div class="stats">
  <div class="stat-card"><div class="label">Commitu</div><div class="value">{total_commits}</div></div>
  <div class="stat-card"><div class="label">Radku OHF</div><div class="value">{ohf_lines}</div></div>
  <div class="stat-card"><div class="label">Celkem +</div><div class="value ins">+{total_ins}</div></div>
  <div class="stat-card"><div class="label">Celkem -</div><div class="value del">-{total_dels}</div></div>
  <div class="stat-card"><div class="label">Poslednich 7 dni</div><div class="value">{last7}</div></div>
</div>

<input class="search" type="text" id="search" placeholder="Hledat (verze, tag, popis...)">
<div class="filter-bar" id="filters">
  <button class="filter-btn active" data-cat="all">Vse</button>
  {filter_buttons}
</div>
<div class="filter-bar" id="branch_filters">
  <button class="filter-btn active" data-branch="all">Vsechny vetve</button>
  {branch_filter_buttons}
</div>

<div class="table-wrap">
<table id="hist">
<thead>
<tr>
  <th data-col="0">Hash <span class="arrow">&#9650;</span></th>
  <th data-col="1">Datum <span class="arrow">&#9660;</span></th>
  <th data-col="2">Verze <span class="arrow">&#9650;</span></th>
  <th data-col="3">Vetev</th>
  <th data-col="4">Popis</th>
  <th data-col="5">Kategorie <span class="arrow">&#9650;</span></th>
  <th data-col="6">Tagy</th>
  <th data-col="7">+/- <span class="arrow">&#9650;</span></th>
  <th data-col="8">Net <span class="arrow">&#9650;</span></th>
  <th data-col="9">Soubory</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</div>

<footer>generuj_historii.py &mdash; spust <code>python generuj_historii.py</code> pro aktualizaci</footer>

<script>
// Search
const search = document.getElementById('search');
const rows = document.querySelectorAll('#hist tbody tr');
function applyFilters() {{
  const q = search.value.toLowerCase();
  const cat = document.querySelector('#filters .filter-btn.active').dataset.cat;
  const br = document.querySelector('#branch_filters .filter-btn.active').dataset.branch;
  rows.forEach(r => {{
    const text = r.textContent.toLowerCase();
    const rc = r.dataset.cat;
    const rb = (r.dataset.branches || '').split(',');
    const matchQ = !q || text.includes(q);
    const matchC = cat === 'all' || rc === cat;
    const matchB = br === 'all' || rb.indexOf(br) !== -1;
    r.classList.toggle('hidden', !(matchQ && matchC && matchB));
  }});
}}
search.addEventListener('input', applyFilters);

// Category filter
document.getElementById('filters').addEventListener('click', e => {{
  if (!e.target.classList.contains('filter-btn')) return;
  document.querySelectorAll('#filters .filter-btn').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  applyFilters();
}});
// Branch filter
document.getElementById('branch_filters').addEventListener('click', e => {{
  if (!e.target.classList.contains('filter-btn')) return;
  document.querySelectorAll('#branch_filters .filter-btn').forEach(b => b.classList.remove('active'));
  e.target.classList.add('active');
  applyFilters();
}});

// Sort
document.querySelectorAll('#hist thead th').forEach(th => {{
  th.addEventListener('click', () => {{
    const col = +th.dataset.col;
    const tbody = document.querySelector('#hist tbody');
    const arr = Array.from(tbody.rows);
    const dir = th.classList.contains('asc') ? -1 : 1;
    document.querySelectorAll('#hist thead th').forEach(t => t.classList.remove('asc','desc','sorted'));
    th.classList.add(dir === 1 ? 'asc' : 'desc', 'sorted');
    arr.sort((a, b) => {{
      let va = a.cells[col].dataset.sort || a.cells[col].textContent;
      let vb = b.cells[col].dataset.sort || b.cells[col].textContent;
      if (!isNaN(va) && !isNaN(vb)) return (va - vb) * dir;
      return va.localeCompare(vb, 'cs') * dir;
    }});
    arr.forEach(r => tbody.appendChild(r));
  }});
}});
</script>
</body>
</html>
"""


def bar_html(ins, dels):
    mx = max(ins, dels, 1)
    scale = 80 / mx
    iw = max(1, int(ins * scale))
    dw = max(1, int(dels * scale)) if dels else 0
    parts = [f'<span class="bar bar-ins" style="width:{iw}px"></span>']
    if dw:
        parts.append(f'<span class="bar bar-del" style="width:{dw}px"></span>')
    return " ".join(parts)


BRANCH_COLORS = [
    "#1f6feb", "#bc4c00", "#8250df", "#1a7f37", "#cf222e",
    "#9a6700", "#116329", "#a40e26", "#0969da", "#6639ba",
]


def branch_color(br, cache={}):
    if br not in cache:
        cache[br] = BRANCH_COLORS[len(cache) % len(BRANCH_COLORS)]
    return cache[br]


def build_row(c):
    bg, fg, label = CAT_COLORS.get(c["category"], CAT_COLORS["other"])
    badge = f'<span class="badge" style="background:{bg};color:{fg}">{label}</span>'
    tags_html = " ".join(f'<span class="tag">{html.escape(t)}</span>' for t in c["tags"]) if c["tags"] else ""
    branches_html = " ".join(
        f'<span class="branch" style="background:{branch_color(b)}">{html.escape(b)}</span>'
        for b in c["branches"]
    ) if c["branches"] else ""

    net_cls = "pos" if c["net"] > 0 else ("neg" if c["net"] < 0 else "zero")
    net_sign = "+" if c["net"] > 0 else ""

    subj = html.escape(c["subject"])
    body_html = ""
    if c["body"]:
        body_html = f'<div class="body">{html.escape(c["body"])}</div>'
        subj = f"<details><summary>{subj}</summary>{body_html}</details>"

    files = "<br>".join(html.escape(f) for f in c["files"])
    branches_attr = ",".join(c["branches"])

    return (
        f'<tr data-cat="{c["category"]}" data-branches="{html.escape(branches_attr)}">'
        f'<td class="hash">{c["short"]}</td>'
        f'<td class="date" data-sort="{c["date"].isoformat()}">{c["date"].strftime("%Y-%m-%d %H:%M")}</td>'
        f'<td class="version">{html.escape(c["version"])}</td>'
        f'<td>{branches_html}</td>'
        f'<td class="subject">{subj}</td>'
        f'<td>{badge}</td>'
        f'<td>{tags_html}</td>'
        f'<td data-sort="{c["ins"] + c["dels"]}">'
        f'<span class="ins">+{c["ins"]}</span> <span class="del">-{c["dels"]}</span><br>{bar_html(c["ins"], c["dels"])}</td>'
        f'<td class="net {net_cls}" data-sort="{c["net"]}">{net_sign}{c["net"]}</td>'
        f'<td class="files">{files}</td>'
        f'</tr>\n'
    )


def build_html(commits, ohf_lines, branches):
    rows = "".join(build_row(c) for c in commits)
    total_ins = sum(c["ins"] for c in commits)
    total_dels = sum(c["dels"] for c in commits)
    now = datetime.now()
    week_ago = datetime(now.year, now.month, now.day).timestamp() - 7 * 86400
    last7 = sum(1 for c in commits if c["date"].timestamp() > week_ago)

    cats_used = sorted(set(c["category"] for c in commits))
    btns = []
    for cat in cats_used:
        _, _, label = CAT_COLORS.get(cat, CAT_COLORS["other"])
        btns.append(f'<button class="filter-btn" data-cat="{cat}">{label}</button>')

    br_btns = [
        f'<button class="filter-btn" data-branch="{html.escape(b)}" '
        f'style="border-color:{branch_color(b)}">{html.escape(b)}</button>'
        for b in branches
    ]
    branches_list_html = " ".join(
        f'<span class="branch" style="background:{branch_color(b)}">{html.escape(b)}</span>'
        for b in branches
    )

    return HTML_TEMPLATE.format(
        generated=now.strftime("%Y-%m-%d %H:%M:%S"),
        total_commits=len(commits),
        ohf_lines=f"{ohf_lines:,}".replace(",", " "),
        total_ins=f"{total_ins:,}".replace(",", " "),
        total_dels=f"{total_dels:,}".replace(",", " "),
        last7=last7,
        filter_buttons="\n  ".join(btns),
        branch_filter_buttons="\n  ".join(br_btns),
        branches_list=branches_list_html,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generuje git_historie.html z git logu.")
    parser.add_argument("--repo", default=".", help="Cesta k git repu (default: .)")
    parser.add_argument("--output", default=None, help="Vystupni soubor (default: git_historie.html v repu)")
    args = parser.parse_args()

    repo = os.path.abspath(args.repo)
    out = args.output or os.path.join(repo, "git_historie.html")

    print(f"Repo:   {repo}")
    print(f"Output: {out}")

    commits = get_commits(repo)
    branch_map, branches = get_branch_map(repo)
    for c in commits:
        c["branches"] = sorted(branch_map.get(c["hash"], []))
    # sort commits by date desc for display
    commits.sort(key=lambda c: c["date"], reverse=True)
    print(f"Nacten {len(commits)} commitu z {len(branches)} vetvi: {', '.join(branches)}")

    # OHF line count - podpora i variant-B souboru
    ohf_lines = 0
    for name in ("Cash_game.ohf", "Cash_game_B.ohf"):
        ohf = os.path.join(repo, name)
        if os.path.isfile(ohf):
            with open(ohf, "r", encoding="utf-8") as f:
                ohf_lines = max(ohf_lines, sum(1 for _ in f))

    page = build_html(commits, ohf_lines, branches)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"Hotovo -- {out} ({len(page):,} bajtu, {len(commits)} radku tabulky)")


if __name__ == "__main__":
    main()
