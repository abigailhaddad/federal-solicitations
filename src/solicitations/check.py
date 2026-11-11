"""Check every answer: each quote must be in the package, and each value must pass its field's rule.

    uv run python -m solicitations.check work/week.csv work/answers.jsonl --out work/checked.jsonl

Writes one record per notice: the kept values (each with its quote and the pages it is on) and every
dropped value with the reason. Quotes are checked against the whole package, not the excerpt sent.
"""
import argparse
import json
import sys
from pathlib import Path

from federal_ja.analysis.text import locate_quote

from . import fields, prepare


def check_answer(doc, answer: dict) -> tuple[dict, list]:
    nd = doc.normalized()
    kept, dropped = {}, []
    for name, (kind, _) in fields.FIELDS.items():
        v = answer.get(name)
        if kind in ("enum", "text"):
            kept[name] = v or None
            continue
        items = v if kind == "list" else ([v] if v and v.get("value") not in (None, "", "not_stated") else [])
        good = []
        for it in items:
            if it.get("value") in (None, "", "not_stated"):
                continue
            qm = locate_quote(it.get("quote"), doc, nd, ignore_case=True)
            if not qm.verified:
                dropped.append({"field": name, "value": it.get("value"), "quote": it.get("quote"), "why": "quote not found"})
                continue
            why = fields.rule(name, it)
            if why:
                dropped.append({"field": name, "value": it.get("value"), "quote": it.get("quote"), "why": why})
                continue
            good.append({**it, "pages": qm.pages, "match": qm.match})
        kept[name] = good if kind == "list" else (good[0] if good else None)
    return kept, dropped


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("week_csv")
    ap.add_argument("answers")
    ap.add_argument("--text", default="work/text")
    ap.add_argument("--out", default="work/checked.jsonl")
    a = ap.parse_args(argv)
    rows = {r["notice_id"]: r for r in prepare.rows(a.week_csv)}
    latest = {}
    for line in Path(a.answers).read_text().splitlines():
        r = json.loads(line)
        if r.get("answer") is not None and r.get("prompt_version") == fields.PROMPT_VERSION:
            latest[r["notice_id"]] = r
    n_drop = {}
    with open(a.out, "w") as fh:
        for nid, r in latest.items():
            doc = prepare.load(Path(a.text), rows[nid])
            kept, dropped = check_answer(doc, r["answer"])
            for d in dropped:
                n_drop[(d["field"], d["why"])] = n_drop.get((d["field"], d["why"]), 0) + 1
            fh.write(json.dumps({"notice_id": nid, "meta": doc.meta, "input": r.get("input"), "usage": r.get("usage"),
                                 "kept": kept, "dropped": dropped}, default=str) + "\n")
    print(json.dumps({"notices": len(latest), "dropped": {f"{k[0]}: {k[1]}": v for k, v in sorted(n_drop.items())}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
