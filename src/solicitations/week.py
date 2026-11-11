"""Pick the notices for one week from SAM.gov's Contract Opportunities files.

    uv run python -m solicitations.week --from 2026-11-02 --to 2026-11-08 CSV [CSV ...] --out work/week.csv

A record is a solicitation number (with its sub-tier) that had any posting of the kept types in the
week. It is read from its latest posting in the files, so a presolicitation followed by the
solicitation is one record. `first_posted` is the earliest posting of that number in any file, so
`new_in_week` tells a new requirement from an amendment of an older one. Files: the active-notices
CSV plus the yearly archives that can hold the week (a closed notice moves to the archive).
"""
import argparse
import csv
import sys
from datetime import date

csv.field_size_limit(sys.maxsize)
TYPES = ("Solicitation", "Sources Sought", "Presolicitation", "Special Notice")
COLS = ("NoticeId", "Title", "Sol#", "Department/Ind.Agency", "Sub-Tier", "Office", "PostedDate", "Type",
        "SetASide", "ResponseDeadLine", "NaicsCode", "ClassificationCode", "PopState", "Link", "Description")


def key(r):
    return ((r.get("Sol#") or "").strip().upper() or r["NoticeId"], (r.get("Sub-Tier") or "").strip())


def posted(r):
    return (r.get("PostedDate") or "")[:10]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    rows = {}
    for f in a.files:
        with open(f, encoding="latin-1", newline="") as fh:
            for r in csv.DictReader(fh):
                rows[r["NoticeId"]] = {c: r.get(c) for c in COLS}   # a notice in two files: the later file wins
    groups = {}
    for r in rows.values():
        groups.setdefault(key(r), []).append(r)
    out = []
    for k, rs in groups.items():
        in_week = [r for r in rs if r["Type"] in TYPES and posted(r)
                   and start <= date.fromisoformat(posted(r)) <= end]
        if not in_week:
            continue
        kept = [r for r in rs if r["Type"] in TYPES]
        latest = max(kept, key=lambda r: (r["PostedDate"] or "", r["NoticeId"]))
        first = min(posted(r) for r in rs if posted(r))
        out.append({"notice_id": latest["NoticeId"], "sol": k[0], "type": latest["Type"],
                    "title": latest["Title"], "agency": latest["Department/Ind.Agency"], "sub_tier": latest["Sub-Tier"],
                    "office": latest["Office"], "posted": latest["PostedDate"], "first_posted": first,
                    "new_in_week": start <= date.fromisoformat(first) <= end, "versions": len(rs),
                    "deadline": latest["ResponseDeadLine"], "set_aside": latest["SetASide"],
                    "naics": latest["NaicsCode"], "psc": latest["ClassificationCode"], "pop_state": latest["PopState"],
                    "link": latest["Link"], "description": latest["Description"] or ""})
    out.sort(key=lambda r: r["notice_id"])
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    from collections import Counter
    print({"records": len(out), "new_in_week": sum(r["new_in_week"] for r in out),
           "by_type": dict(Counter(r["type"] for r in out))})


if __name__ == "__main__":
    main()
