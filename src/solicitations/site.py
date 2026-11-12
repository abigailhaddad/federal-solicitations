"""Build the static site (site/dist) from data/posts/*.json.

    index.html         every weekly post, newest first
    posts/<week>.html  one page per week
    about.html         what this is, how values are checked, the caveats
    feed.xml           RSS 2.0, the last FEED_ITEMS posts in full
    style.css, 404.html

No JavaScript, no cookies, no external assets. Every value from SAM.gov is HTML-escaped; links are kept
only if they are plain http(s) URLs.

    uv run python -m solicitations.site --out site/dist
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import shutil
import sys
from email.utils import format_datetime
from pathlib import Path

from .digest import LABELS, MAX_PICKS, POSTS

TITLE = "RFP Watch"
TAGLINE = ("Federal services and construction solicitations and sources-sought notices on SAM.gov, each week: how offers will be "
           "judged, contract types, stated values, named incumbents and planned sole-source awards, every value "
           "with the sentence it came from")
SITE_URL = "https://rfp-watch.pages.dev"
GITHUB_REPO = "abigailhaddad/federal-solicitations"
HF_REPO_ID = "abigailhaddad/federal-solicitations"
DATASET_URL = f"https://huggingface.co/datasets/{HF_REPO_ID}"
PROJECT_URL = f"https://github.com/{GITHUB_REPO}"
ISSUE_URL = f"{PROJECT_URL}/issues/new"
FEED_ITEMS = 20


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def safe_url(u) -> str | None:
    u = str(u or "").strip()
    return u if re.match(r"^https?://[^\s<>\"']+$", u) else None


def link(u, text) -> str:
    u = safe_url(u)
    return f'<a href="{e(u)}">{e(text)}</a>' if u else e(text)


def date_label(d: str) -> str:
    x = dt.date.fromisoformat(d[:10])
    return f"{x:%B} {x.day}, {x.year}"


def week_label(p: dict) -> str:
    a, b = dt.date.fromisoformat(p["week_start"]), dt.date.fromisoformat(p["week_end"])
    return f"{a:%B} {a.day}–{b.day}, {b.year}" if a.month == b.month else f"{a:%B} {a.day} – {b:%B} {b.day}, {b.year}"


def page(title: str, body: str, *, root: str = "", home: bool = False) -> str:
    past = "" if home else f'<a href="{root}index.html">Past weeks</a> '
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(TAGLINE)}">
<meta name="referrer" content="no-referrer">
<link rel="stylesheet" href="{root}style.css">
<link rel="alternate" type="application/rss+xml" title="{e(TITLE)}" href="{root}feed.xml">
</head>
<body>
<header class="site"><a class="brand" href="{root}index.html">{e(TITLE)}</a>
<nav>{past}<a href="{root}about.html">About</a> <a href="{root}feed.xml">RSS</a></nav>
</header>
<main>
{body}
</main>
<footer class="site"><p>Picks chosen and written up by Claude. Every value, count and list comes from the notices'
documents, each value with the sentence it came from; check the notice on SAM.gov before relying on anything.
<a href="{root}about.html">About</a> · <a href="{DATASET_URL}">All the data (Hugging Face)</a> ·
<a href="{ISSUE_URL}">Report a problem (GitHub issue)</a></p></footer>
</body>
</html>
"""


def who(n: dict) -> str:
    return " · ".join(e(x) for x in (n.get("agency"), n.get("sub_tier") if n.get("sub_tier") != n.get("agency") else None,
                                     n.get("office")) if x)


def kind(n: dict) -> str:
    return "Sources sought" if n["track"] == "sources_sought" else "Solicitation"


def facts(n: dict) -> str:
    bits = [kind(n), f"responses due {date_label(n['response_deadline'])}" if n.get("response_deadline") else None,
            f"set-aside on SAM.gov: {n['set_aside_sam']}" if n.get("set_aside_sam") and n["set_aside_sam"] != "No Set aside used" else None,
            f"evaluation: {LABELS[n['evaluation_basis']]}" if n.get("evaluation_basis") else None,
            ("contract type: " + ", ".join(n["contract_type"])) if n.get("contract_type") else None,
            f"stated value: {n['stated_value']}" + (f" ({n['stated_value_what']})" if n.get("stated_value_what") else "") if n.get("stated_value") else None]
    return " · ".join(e(b) for b in bits if b)


def ref(n: dict) -> str:
    return f'{link(n.get("sam_link"), "Notice on SAM.gov")} · {e(n.get("solicitation_number"))}'


def pick_html(i: int, p: dict) -> str:
    n = p["notice"]
    return "\n".join([f'<article class="pick"><h2><span class="rank">{i}.</span> {e(p["headline"])}</h2>',
                      f'<p class="facts">{e(n["title"])}</p><p class="meta">{who(n)}</p>',
                      f'<p class="meta">{facts(n)}</p>', f'<p>{e(p["why"])}</p>',
                      f'<blockquote><p>“{e(p["quote"])}”</p><footer>— from the notice</footer></blockquote>',
                      f'<p class="meta">{ref(n)}</p></article>'])


def item(n: dict, value: str, quote: str | None) -> str:
    q = f'<br><span class="gist">“{e(quote)}”</span>' if quote else ""
    return (f'<li><strong>{e(value)}</strong> — {link(n.get("sam_link"), n["title"])}<br>'
            f'<span class="meta">{kind(n)} · {who(n)}'
            + (f' · due {e(date_label(n["response_deadline"]))}' if n.get("response_deadline") else "") + f"</span>{q}</li>")


def amount(s: str) -> float:
    m = re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(billion|million|[BM])?", s or "", re.I)
    if not m:
        return 0.0
    return float(m.group(1).replace(",", "")) * {"b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6}.get((m.group(2) or "").lower(), 1)


def post_content(p: dict) -> str:
    s = p["summary"]
    eb = s["evaluation_basis"]
    parts = [f'<p class="lede">Every services and construction solicitation and sources-sought notice with a posting on SAM.gov from '
             f'{e(week_label(p))}: {s["solicitations"]:,} solicitations and {s["sources_sought"]:,} sources-sought notices. '
             f'Values are read from each notice\'s documents and checked against them; an empty value means it wasn\'t found.</p>',
             f'<p>How offers will be judged, where the solicitation says ({s["evaluation_basis_found"]:,} of {s["solicitations"]:,}): '
             + ", ".join(f"{e(LABELS[k])} {v:,}" for k, v in eb.items()) + ". "
             f'{s["sole_source_intent"]:,} sources-sought notices say the agency plans to award to one named firm. '
             f'{s["solicitations_with_incumbent"] + s["sources_sought_with_incumbent"]:,} notices name a current or previous '
             f'contractor; {s["solicitations_with_value"] + s["sources_sought_with_value"]:,} state a dollar value.</p>']
    if not p["picks"]:
        parts.append("<p>Nothing picked out this week.</p>")
    parts += [pick_html(i, x) for i, x in enumerate(p["picks"], 1)]
    L = p["lists"]
    as_of = p.get("as_of") or p["built_at"][:10]
    if L.get("largest_values"):
        parts.append('<section class="all"><h2>Largest stated values</h2><p class="note">The ten largest dollar values '
                     'stated this week, as written (sorted by the first dollar figure).</p><ol>'
                     + "".join(f'<li><strong>{e(n["stated_value"])}</strong>' + (f' ({e(n["stated_value_what"])})' if n.get("stated_value_what") else "")
                               + f' — {link(n.get("sam_link"), n["title"])} <span class="meta">· {e(n.get("sub_tier") or n.get("agency"))}</span></li>'
                               for n in L["largest_values"]) + "</ol></section>")
    def status(n):
        d = (n.get("response_deadline") or "")[:10]
        return "" if not d else (" · <strong>responses still open</strong>" if d >= as_of else " · responses closed")
    parts.append(f'<section class="all"><h2>Planned sole-source awards ({len(L["sole_source_intent"])})</h2>'
                 f'<p class="note">Sources-sought notices that say the agency intends to award, or is considering awarding, '
                 f'to one named firm. Open or closed as of {e(date_label(as_of))}.</p><ul>'
                 + "".join(item(n, n["sole_source_intent"], n.get("sole_source_intent_quote")).replace("</span>", status(n) + "</span>", 1)
                           for n in sorted(L["sole_source_intent"], key=lambda n: (n.get("response_deadline") or "")[:10] < as_of))
                 + "</ul></section>")
    parts.append(f'<section class="all"><h2>Named incumbents ({len(L["incumbent"])})</h2>'
                 '<p class="note">Notices that name a current or previous contractor, or contract, for this work.</p><ul>'
                 + "".join(item(n, " / ".join(x for x in (n.get("incumbent_firm"), n.get("incumbent_contract_number")) if x),
                                n.get("incumbent_quote")) for n in L["incumbent"]) + "</ul></section>")
    vals = sorted(L["stated_value"], key=lambda n: -amount(n["stated_value"]))
    parts.append(f'<section class="all"><details><summary><h2>Stated values ({len(vals)})</h2></summary>'
                 '<p class="note">As written in the notice, largest first (sorted by the first dollar figure; read the sentence for what it covers).</p><ul>'
                 + "".join(item(n, n["stated_value"] + (f' ({n["stated_value_what"]})' if n.get("stated_value_what") else ""),
                                n.get("stated_value_quote")) for n in vals) + "</ul></details></section>")
    parts.append(f'<p class="note">{s["no_document_text"]:,} of these notices had no attached document text to read '
                 f'(no attachments, documents hosted on another site such as PIEE, or scanned images); only their SAM.gov '
                 f'description was read. Every notice, with every value and its quote, is in the '
                 f'<a href="{DATASET_URL}">dataset</a>.</p>')
    return "\n".join(parts)


def post_page(p: dict, prev: str | None, nxt: str | None) -> str:
    nav = ([f'<a href="{e(prev)}.html">← Older</a>'] if prev else []) + ([f'<a href="{e(nxt)}.html">Newer →</a>'] if nxt else [])
    body = (f'<p class="dateline">Week of {e(week_label(p))}</p><h1>{e(TITLE)}: week of {e(week_label(p))}</h1>\n'
            + post_content(p) + (f'\n<p class="pager">{" · ".join(nav)}</p>' if nav else ""))
    return page(f"{TITLE}: week of {week_label(p)}", body, root="../")


def index_page(posts: list[dict]) -> str:
    rows = []
    for p in reversed(posts):
        top = p["picks"][0]["headline"] if p["picks"] else "nothing picked out"
        s = p["summary"]
        rows.append(f'<li><span class="date">Week of {e(week_label(p))}</span> <a href="posts/{e(p["week_start"])}.html">{e(top)}</a> '
                    f'<span class="count">{s["solicitations"] + s["sources_sought"]:,} notices, {len(p["picks"])} picks</span></li>')
    lst = '<ul class="weeks">' + "".join(rows) + "</ul>" if rows else "<p>No posts yet.</p>"
    return page(TITLE, f'<h1>{e(TITLE)}</h1>\n<p class="lede">{e(TAGLINE)}. <a href="about.html">About</a> · '
                       f'<a href="feed.xml">RSS feed</a></p>\n{lst}', home=True)


def about_page() -> str:
    body = f"""<h1>About {e(TITLE)}</h1>
<p>{e(TAGLINE)}.</p>
<p><strong>What's covered.</strong> Each week, every <em>solicitation</em> on SAM.gov for services, construction
included (product service codes that start with a letter) and every <em>sources-sought</em> notice (market research before a solicitation)
with a posting that week. A solicitation number is one notice, read from its latest posting. Not covered:
solicitations for goods, presolicitations, special notices, and award notices.</p>
<p><strong>How values are read.</strong> Claude (Haiku) reads each notice's SAM.gov description and attached
documents and answers a fixed set of questions, quoting the sentence each answer comes from. Code then checks every
answer: the quote must be in the documents, and the value must pass a rule against its own quote (an "LPTA" value
needs LPTA wording, a dollar value must be in its sentence with its qualifiers, a per-contract amount or guaranteed
minimum is not a total, and so on). Answers that fail are dropped, not shown.</p>
<p><strong>What's published.</strong> Only fields that passed a sampled check: for each one, about 30 values (or all
of them, when fewer) were judged against the documents by a separate Claude model against the field's written
definition. A field that fell short was dropped (for example whether a sources-sought notice says a set-aside is
being considered: 6 of 30 right). The results are in the <a href="{e(PROJECT_URL)}/tree/main/docs/checks">proof
files</a>. Those are samples, not a census: expect occasional errors, and check the notice.</p>
<p><strong>Empty means not found.</strong> Large packages are cut to the parts on these topics before Claude reads
them, documents hosted elsewhere (PIEE, NECO) and spreadsheets aren't read, and scanned images have no text.</p>
<p><strong>The picks</strong> are chosen and written up by Claude (Opus) from the checked values. Code drops a pick
whose quote isn't in the notice, whose dollar figure isn't the notice's stated value, or that says "sole source",
"incumbent" or "LPTA" about a notice without that value.</p>
<p><strong>All the data:</strong> <a href="{e(DATASET_URL)}">huggingface.co/datasets/{e(HF_REPO_ID)}</a>, as Parquet
and CSV, every value with its quote and where in the documents it is. Code: <a href="{e(PROJECT_URL)}">github.com/{e(GITHUB_REPO)}</a>.
Spotted a wrong value? <a href="{ISSUE_URL}">Open an issue</a>. No JavaScript, no cookies, no trackers.</p>"""
    return page(f"About · {TITLE}", body)


def feed_xml(posts: list[dict]) -> str:
    x = lambda s: html.escape("" if s is None else str(s), quote=False)  # noqa: E731
    newest = list(reversed(posts))[:FEED_ITEMS]
    built = max((dt.datetime.fromisoformat(p["built_at"]) for p in newest), default=dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc))
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', "<channel>",
           f"<title>{x(TITLE)}</title>", f"<link>{SITE_URL}/</link>", f"<description>{x(TAGLINE)}.</description>",
           "<language>en-us</language>", f'<atom:link href="{SITE_URL}/feed.xml" rel="self" type="application/rss+xml"/>',
           f"<lastBuildDate>{format_datetime(built.astimezone(dt.timezone.utc), usegmt=True)}</lastBuildDate>"]
    for p in newest:
        url = f"{SITE_URL}/posts/{p['week_start']}"
        t = dt.datetime.fromisoformat(p["built_at"]).astimezone(dt.timezone.utc)
        out += ["<item>", f"<title>{x(TITLE)}: week of {x(week_label(p))}</title>", f"<link>{url}</link>",
                f'<guid isPermaLink="true">{url}</guid>', f"<pubDate>{format_datetime(t, usegmt=True)}</pubDate>",
                f"<description>{x(post_content(p))}</description>", "</item>"]
    return "\n".join(out + ["</channel>", "</rss>"]) + "\n"


CSS = """:root{--bg:#fbfbf8;--fg:#1d1d1f;--muted:#5f6368;--line:#e2e1da;--accent:#0f6b5c;--quote:#f1f1ec}
@media (prefers-color-scheme: dark){:root{--bg:#141414;--fg:#e8e6e1;--muted:#a3a19c;--line:#2e2d2a;--accent:#5fc7b0;--quote:#1f1e1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
header.site,main,footer.site{max-width:46rem;margin:0 auto;padding:0 16px}
header.site{display:flex;flex-wrap:wrap;gap:.5rem 1.5rem;align-items:baseline;justify-content:space-between;padding-top:1rem;padding-bottom:.75rem;border-bottom:1px solid var(--line)}
.brand{font-weight:800;font-size:1.2rem;text-decoration:none;color:var(--fg)}
nav a{margin-right:1rem;color:var(--muted)}
a{color:var(--accent);overflow-wrap:anywhere}
h1{font-size:1.6rem;line-height:1.25;margin:1.5rem 0 .75rem}
h2{font-size:1.2rem;line-height:1.3;margin:2rem 0 .5rem}
summary h2{display:inline}
summary{cursor:pointer;margin:2rem 0 .5rem}
.rank,.meta,.dateline,.note,.count,.date,.gist{color:var(--muted)}
.facts{font-weight:600;margin:.25rem 0}
.meta{font-size:.95rem;margin:.2rem 0}
.dateline{margin:1.5rem 0 0;font-size:.95rem}
blockquote{margin:.75rem 0;padding:.5rem 1rem;background:var(--quote);border-left:3px solid var(--accent)}
blockquote p{margin:0}
blockquote footer{color:var(--muted);font-size:.9rem;margin-top:.25rem}
.pick{border-bottom:1px solid var(--line);padding-bottom:1rem}
.all ul,.all ol,.weeks{padding-left:1.3rem}
.all li{margin:.7rem 0;font-size:.95rem}
.weeks li{margin:.6rem 0}
.date{display:block;font-size:.9rem}
.count{font-size:.9rem;white-space:nowrap}
.note,.gist{font-size:.9rem}
.pager{margin:2rem 0}
footer.site{margin-top:3rem;padding-top:1rem;padding-bottom:2rem;border-top:1px solid var(--line);color:var(--muted);font-size:.9rem}
"""


def load_posts(d: Path = POSTS) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


def build(posts: list[dict], out: Path) -> list[str]:
    out = Path(out)
    if out.name != "dist":
        raise SystemExit(f"refusing to wipe {out}: the output directory must be named dist")
    posts = sorted(posts, key=lambda p: p["week_start"])
    if out.exists():
        shutil.rmtree(out)
    (out / "posts").mkdir(parents=True)
    files = {"index.html": index_page(posts), "about.html": about_page(), "feed.xml": feed_xml(posts), "style.css": CSS,
             "404.html": page(f"Not found · {TITLE}", '<h1>Not found</h1><p><a href="/index.html">Past weeks</a></p>', root="/")}
    for k, p in enumerate(posts):
        prev = posts[k - 1]["week_start"] if k > 0 else None
        nxt = posts[k + 1]["week_start"] if k + 1 < len(posts) else None
        files[f"posts/{p['week_start']}.html"] = post_page(p, prev, nxt)
    for rel, text in files.items():
        (out / rel).write_text(text, encoding="utf-8")
    return sorted(files)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solicitations.site")
    ap.add_argument("--out", type=Path, required=True, help="output directory, wiped first (must be named dist)")
    a = ap.parse_args(argv)
    files = build(load_posts(), a.out)
    print(f"site: {len(files)} files -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
