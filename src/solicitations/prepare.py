"""Turn a fetched notice into (a) the Document its quotes are checked against and (b) what the model reads.

Small packages are sent whole. Bigger ones are cut to: the start of each file, and the text around every
passage on the topics the fields ask about, merged, up to MAX_CHARS (well under the 100,000-token
point where Haiku's price rises). What was left out is marked [...] so the model knows it is partial.
Quotes are always checked against the whole package, never the excerpt.
"""
import csv
import gzip
import json
import re
from pathlib import Path

from federal_ja.analysis.text import Document, Page

# 130,000 characters stays under Haiku's 100,000-token price point even at the densest text seen
# (about 1.4 characters a token, in table-heavy packages). At 240,000, 67 of 1,544 requests on 2026-11-11
# passed 100,000 tokens and were billed at five times the rate.
WHOLE_CHARS = 130_000       # send everything up to this
MAX_CHARS = 130_000         # cap for excerpts
FILE_HEAD = 6_000           # start of each file
WINDOW = 2_500              # either side of a topic hit
TOPICS = re.compile(
    r"lowest[\s-]+price[\s-]+technically|\bLPTA\b|trade[\s-]?off|best[\s-]+value|highest[\s-]+technically|"
    r"basis\s+for\s+award|evaluation\s+factors?|evaluation\s+criteria|relative\s+importance|more\s+important\s+than|"
    r"contract\s+type|firm[\s-]+fixed[\s-]+price|time[\s-]+and[\s-]+materials|labor[\s-]+hour|cost[\s-]+plus|"
    r"indefinite[\s-]+(?:delivery|quantity)|\bIDIQ\b|multiple\s+awards?|single\s+award|"
    r"period\s+of\s+performance|option\s+(?:period|year)s?|estimated\s+(?:total\s+)?value|ceiling|not[\s-]+to[\s-]+exceed|"
    r"incumbent|currently\s+(?:being\s+)?performed|follow[\s-]+on|predecessor|current\s+contract|"
    r"clearance|top\s+secret|\bsecret\b|public\s+trust|\bCMMC\b|brand[\s-]+name|oral\s+presentation|interview|"
    r"page\s+limit|pages?\s+(?:maximum|max|limit)|not\s+(?:to\s+)?exceed\s+\d+\s+pages|set[\s-]+aside|8\(a\)|SDVOSB|HUBZone|"
    r"sole\s+source|small\s+business", re.I)


def load(text_dir: Path, row: dict) -> Document:
    rec = json.load(gzip.open(text_dir / f"{row['notice_id']}.json.gz"))
    pages = []
    desc = (row.get("description") or "").strip()
    if desc:
        pages.append(Page("description", "SAM.gov notice description", 1, "native", desc))
    for f in rec["files"]:
        for i, t in enumerate(f.get("pages") or [], 1):
            pages.append(Page(f["resource_id"], f["name"], i if f.get("kind") == "pdf" else None, "native", t or ""))
    meta = {k: row.get(k) for k in ("type", "title", "agency", "sub_tier", "office", "posted", "first_posted",
                                     "deadline", "set_aside", "naics", "psc", "pop_state", "sol")}
    meta["links"] = rec["links"]
    meta["files_not_read"] = [f["name"] for f in rec["files"] if f.get("kind") in ("spreadsheet_not_read", "too_large_not_read", "error")]
    meta["pages_without_text"] = sum(1 for f in rec["files"] for t in (f.get("pages") or []) if not (t or "").strip())
    return Document(row["notice_id"], meta, pages)


def header(doc: Document) -> str:
    m = doc.meta
    lines = [f"SAM.gov notice type: {m['type']}", f"Title: {m['title']}", f"Agency: {m['agency']} / {m['sub_tier']} / {m['office']}",
             f"Solicitation number: {m['sol']}", f"Posted: {m['posted']} (first posted {m['first_posted']})",
             f"Response deadline: {m['deadline']}", f"Set-aside field on SAM.gov: {m['set_aside'] or 'none'}",
             f"NAICS {m['naics']}, PSC {m['psc']}"]
    if m["links"]:
        lines.append("Links to documents on other sites (not read): " + "; ".join(l["name"] for l in m["links"][:5]))
    if m["files_not_read"]:
        lines.append("Files not read: " + "; ".join(m["files_not_read"][:10]))
    return "\n".join(lines)


# Evaluation-method wording comes first in an excerpt: on 2026-11-11, 5 of 7 sampled packages whose
# stated method was missed had it past the cut, because earlier attachments' other topic hits used up
# the budget before Section M was reached.
PRIORITY = re.compile(
    r"lowest[\s,/-]+price[d]?[\s,/-]+(?:and\s+)?technically|\bLPTA\b|trade[\s\-‑]?off|best[\s-]+(?:overall[\s-]+)?value|"
    r"highest[\s-]+technically|basis\s+for\s+award|basis\s+of\s+award|evaluation\s+factors?|relative\s+importance|"
    r"more\s+important\s+than|comparative\s+(?:evaluation|analysis)|source\s+selection\s+process|"
    r"price\s+and\s+other\s+factors\s+considered|type\s+of\s+contract|contemplates\s+award", re.I)


def _windows(text: str, rx, half: int) -> list[list[int]]:
    return [[max(0, m.start() - half), min(len(text), m.end() + half)] for m in rx.finditer(text)]


def _merge(spans: list[list[int]]) -> list[list[int]]:
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(b, out[-1][1])
        else:
            out.append([a, b])
    return out


def excerpt(doc: Document, max_chars: int = MAX_CHARS) -> tuple[str, dict]:
    """The text the model reads, and facts about the cut. Packages over WHOLE_CHARS are cut to, in this
    order of priority until MAX_CHARS: text around evaluation-method wording in every attachment, then
    the start of each attachment, then text around the other topics. Kept spans are shown in document
    order, each attachment under its header, gaps marked [...]."""
    full = doc.model_text()
    if len(full) <= min(WHOLE_CHARS, max_chars):
        return full, {"cut": False, "chars_total": len(full), "chars_sent": len(full)}
    atts = [(fn, "\n\n".join(p.text for p in pages)) for fn, pages in doc.attachments()]
    keep = [[] for _ in atts]
    budget = max_chars
    tiers = [lambda t: _windows(t, PRIORITY, WINDOW), lambda t: [[0, min(len(t), FILE_HEAD)]],
             lambda t: _windows(t, TOPICS, WINDOW)]
    for tier in tiers:
        for i, (fn, text) in enumerate(atts):
            for a, b in _merge(tier(text)):
                if budget <= 0:
                    break
                cur = _merge(keep[i])
                new = b - a - sum(max(0, min(b, y) - max(a, x)) for x, y in cur)
                if new <= 0:
                    continue
                if new > budget:
                    b = a + budget
                    new = budget
                keep[i].append([a, b])
                budget -= new
    parts = []
    for (fn, text), spans in zip(atts, keep):
        spans = _merge(spans)
        if not spans:
            continue
        body = "\n[...]\n".join(text[a:b] for a, b in spans)
        if spans[0][0] > 0:
            body = "[...]\n" + body
        if spans[-1][1] < len(text):
            body += "\n[...]"
        parts.append(f"=== ATTACHMENT: {fn} ===\n{body}")
    sent = "\n\n".join(parts)
    return sent, {"cut": True, "chars_total": len(full), "chars_sent": len(sent)}


def prompt(doc: Document, instructions: str, max_chars: int = MAX_CHARS) -> tuple[tuple[str, str], dict]:
    body, facts = excerpt(doc, max_chars)
    return (instructions, header(doc) + "\n\nDOCUMENTS:\n\n" + (body or "(no document text)")), facts


def rows(week_csv: str) -> list[dict]:
    return list(csv.DictReader(open(week_csv)))
