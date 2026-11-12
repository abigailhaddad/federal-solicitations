"""Build the published tables from checked answers: one row per notice, one file per track.

    uv run python -m solicitations.table work/week.csv work/checked.jsonl --week 2026-11-02 --out work/publish

Only fields in PUBLISHED are written, each value with the sentence it came from (`*_quote`) and where
that sentence is (`*_source`: file name and page). A field is listed there only after a sampled check
against the documents passed (docs/checks/README.md). Values that failed their quote or rule check were
dropped in check.py and are never here; an empty field means "not found", not "not stated".
"""
import argparse
import csv
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

import re

import pandas as pd

TRACKS = {"solicitation": "solicitations", "sources_sought": "sources_sought"}

# field -> extra keys kept from the answer besides value and quote
PUBLISHED = {
    "solicitation": {"evaluation_basis": [], "relative_importance": [], "evaluation_factors": [],
                     "contract_type": [], "stated_value": ["what"], "incumbent": ["contract_number"]},
    "sources_sought": {"sole_source_intent": [], "incumbent": ["contract_number"], "stated_value": ["what"]},
}
LISTS = {"evaluation_factors", "contract_type"}


def cp1252(s: str | None) -> str | None:
    """SAM.gov's CSVs are Windows-1252 but read as Latin-1 (week.py), which turns curly quotes and dashes
    into control characters (596 of 2,079 records in the first week). Re-decode; leave the text as is if
    it doesn't round-trip."""
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("cp1252")
    except UnicodeError:
        return s


def incumbent_parts(value: str | None, number: str | None) -> tuple[str | None, str | None]:
    """(firm, contract number). The model's value is sometimes a firm, sometimes a contract number, and once
    a contract number with its own note; the firm column gets only a name, the number column only a number."""
    v, n = (value or "").strip(), (number or "").strip() or None
    if not v:
        return None, n
    looks_like_number = lambda t: bool(re.fullmatch(r"[A-Z0-9][A-Z0-9-]{6,}", t)) and bool(re.search(r"\d", t))
    if n and n in v:
        rest = re.sub(r"\([^)]*\)", "", v.replace(n, "")).strip(" ,;/-")
        rest = re.sub(r"^(?:contract\s+no\.?|follow-on\s+to\s+contract|task\s+order|and)\s*", "", rest, flags=re.I).strip(" ,;/-")
        return (rest if re.search(r"[A-Za-z]{3}", rest) and not looks_like_number(rest) else None), n
    if not n and looks_like_number(v):
        return None, v
    return v, n


def sources(item: dict, names: dict) -> str:
    out = []
    for p in item.get("pages") or []:
        rid = p.get("resource_id")
        name = "SAM.gov notice description" if rid == "description" else names.get(rid, rid)
        out.append(f"{name}, p. {p['page_number']}" if p.get("page_number") else name)
    return "; ".join(dict.fromkeys(out))


def build(week_csv: str, checked: str, text_dir: str, week_start: str) -> dict[str, pd.DataFrame]:
    rows = {r["notice_id"]: r for r in csv.DictReader(open(week_csv))}
    end = (date.fromisoformat(week_start) + timedelta(days=6)).isoformat()
    out = {t: [] for t in TRACKS}
    for line in open(checked):
        rec = json.loads(line)
        nid, track = rec["notice_id"], rec["profile"]
        r = rows[nid]
        txt = json.load(gzip.open(Path(text_dir) / f"{nid}.json.gz"))
        names = {f["resource_id"]: f["name"] for f in txt["files"]}
        files = txt["files"]
        row = {
            "notice_id": nid, "sam_link": f"https://sam.gov/opp/{nid}/view",
            "solicitation_number": r["sol"], "title": cp1252(r["title"]), "notice_type": r["type"],
            "agency": cp1252(r["agency"]), "sub_tier": cp1252(r["sub_tier"]), "office": cp1252(r["office"]),
            "posted": r["posted"], "first_posted": r["first_posted"], "new_in_week": r["new_in_week"] == "True",
            "versions_in_files": int(r["versions"]), "response_deadline": r["deadline"] or None,
            "set_aside_sam": r["set_aside"] or None, "naics": r["naics"] or None, "psc": r["psc"] or None,
            "place_of_performance_state": r["pop_state"] or None, "week_start": week_start, "week_end": end,
        }
        for name, extras in PUBLISHED[track].items():
            v = rec["kept"].get(name)
            items = v if isinstance(v, list) else ([v] if v else [])
            if name in LISTS:
                row[name] = [i["value"] for i in items] or None
                row[f"{name}_quotes"] = [i["quote"] for i in items] or None
                row[f"{name}_sources"] = [sources(i, names) for i in items] or None
            else:
                i = items[0] if items else {}
                row[name] = i.get("value") or None
                for x in extras:
                    row[f"{name}_{x}"] = (i.get(x) or "").strip() or None
                if name == "incumbent":
                    row.pop("incumbent")
                    row["incumbent_firm"], row["incumbent_contract_number"] = incumbent_parts(i.get("value"), i.get("contract_number"))
                row[f"{name}_quote"] = i.get("quote") or None
                row[f"{name}_source"] = sources(i, names) if i else None
        row.update({
            # read = some text came out of it; everything else is listed with why
            "files_read": sum(1 for f in files if any((t or "").strip() for t in f.get("pages") or [])),
            "files_not_read": [f"{f['name']} ({f.get('kind')})" for f in files
                               if not any((t or "").strip() for t in f.get("pages") or [])] or None,
            "links_to_other_sites": [l["name"] for l in txt["links"]] or None,
            "pages_without_text": rec["meta"]["pages_without_text"],
            "package_chars": rec["input"]["chars_total"], "model_read_excerpt": bool(rec["input"]["cut"]),
            "model": rec["usage"]["model"], "description": cp1252(r["description"]) or None,
        })
        # every text cell, quotes included (a quote from the notice description has the same problem)
        row = {k: ([cp1252(x) if isinstance(x, str) else x for x in v] if isinstance(v, list)
                   else cp1252(v) if isinstance(v, str) else v) for k, v in row.items()}
        out[track].append(row)
    return {t: pd.DataFrame(sorted(v, key=lambda x: (x["posted"], x["notice_id"]))) for t, v in out.items()}


def write(tables: dict[str, pd.DataFrame], out: Path) -> None:
    (out / "data").mkdir(parents=True, exist_ok=True)
    for track, df in tables.items():
        stem = out / "data" / TRACKS[track]
        df.to_parquet(f"{stem}.parquet", index=False)
        flat = df.copy()
        for c in flat.columns:
            if flat[c].map(lambda x: isinstance(x, list)).any():
                flat[c] = flat[c].map(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, list) else None)
        flat.to_csv(f"{stem}.csv", index=False)   # every column written as text by default; IDs keep their zeros


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("week_csv")
    ap.add_argument("checked")
    ap.add_argument("--week", required=True, help="week start (Monday), YYYY-MM-DD")
    ap.add_argument("--text", default="work/text")
    ap.add_argument("--out", default="work/publish")
    a = ap.parse_args(argv)
    tables = build(a.week_csv, a.checked, a.text, a.week)
    write(tables, Path(a.out))
    for t, df in tables.items():
        print(t, len(df), {f: int(df[f if f in df else f"{f}_quote"].notna().sum()) for f in PUBLISHED[t]})


if __name__ == "__main__":
    main()
