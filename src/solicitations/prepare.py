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

WHOLE_CHARS = 240_000       # ~60k tokens: send everything
MAX_CHARS = 240_000         # cap for excerpts
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


def excerpt(doc: Document) -> tuple[str, dict]:
    """The text the model reads, and facts about the cut."""
    full = doc.model_text()
    if len(full) <= WHOLE_CHARS:
        return full, {"cut": False, "chars_total": len(full), "chars_sent": len(full)}
    parts = []
    for fn, pages in doc.attachments():
        text = "\n\n".join(p.text for p in pages)
        spans = [(0, min(len(text), FILE_HEAD))]
        for m in TOPICS.finditer(text):
            spans.append((max(0, m.start() - WINDOW), min(len(text), m.end() + WINDOW)))
        spans.sort()
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(b, merged[-1][1])
            else:
                merged.append([a, b])
        body = "\n[...]\n".join(text[a:b] for a, b in merged)
        if merged and merged[-1][1] < len(text):
            body += "\n[...]"
        parts.append(f"=== ATTACHMENT: {fn} ===\n{body}")
    sent = "\n\n".join(parts)
    if len(sent) > MAX_CHARS:
        sent = sent[:MAX_CHARS] + "\n[... the rest of the package was cut for length ...]"
    return sent, {"cut": True, "chars_total": len(full), "chars_sent": len(sent)}


def prompt(doc: Document, instructions: str) -> tuple[tuple[str, str], dict]:
    body, facts = excerpt(doc)
    return (instructions, header(doc) + "\n\nDOCUMENTS:\n\n" + (body or "(no document text)")), facts


def rows(week_csv: str) -> list[dict]:
    return list(csv.DictReader(open(week_csv)))
