"""Check every answer: each quote must be in the package, and each value must pass its field's rule.

    uv run python -m solicitations.check work/week.csv work/answers.jsonl --out work/checked.jsonl

Writes one record per notice: the kept values (each with its quote and the pages it is on) and every
dropped value with the reason. Quotes are checked against the whole package, not the excerpt sent.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from federal_ja.analysis.text import locate_quote

from . import fields, prepare


# The standard type-of-contract provision filled in (FAR 52.216-1): "The Government contemplates award of a
# Firm-Fixed-Price contract resulting from this solicitation." 3 of 10 sampled blanks were this sentence.
_CONTEMPLATES = re.compile(r"contemplates\s+(?:the\s+)?award\s+(?:of\s+)?(?:an?\s+)?([A-Za-z][A-Za-z ,/&()-]{2,80}?)\s+(?:type\s+)?contract", re.I)


def contract_type_by_rule(doc, nd) -> list[dict]:
    """Contract types read from the filled-in 52.216-1 sentence, each with that sentence as its quote."""
    out = []
    for m in _CONTEMPLATES.finditer(nd):
        start = nd.rfind(".", 0, m.start()) + 1
        end = nd.find(".", m.end())
        quote = nd[start: end + 1 if end > 0 else m.end()].strip()
        for rx in fields._CTYPES:
            if re.search(rx, m.group(1), re.I):
                item = {"value": m.group(1).strip(), "quote": quote}
                if not fields.rule("contract_type", item):
                    qm = locate_quote(quote, doc, nd, ignore_case=True)
                    if qm.verified:
                        out.append({**item, "pages": qm.pages, "match": qm.match, "source": "rule"})
                break
    return out[:3]


_AMOUNT = re.compile(r"(?:(?:US|BZ|BZD|AUD|CAD)\s*)?\$\s?[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|thousand|[MBK])\b)?", re.I)


def as_written(value: str | None, quote: str | None) -> str | None:
    """A value the model wrote as a bare number ("8000000") is replaced by the amount as the quote writes
    it ("$8,000,000.00"), when exactly one amount in the quote has that number. 10 of 60 sampled values
    on 2026-11-12 were bare numbers."""
    v = (value or "").strip().rstrip(",;")
    if not re.fullmatch(r"\d+(?:\.\d+)?", v):
        # bare numbers inside a longer value ("10000000 to 15000000", "Up to 49000000"): each gets the
        # quote's own spelling of that amount when the quote has exactly one
        def one(m):
            w = as_written(m.group(0), quote)
            return w if w != m.group(0) else m.group(0)
        return re.sub(r"(?<![\d$,.])\d{4,}(?:\.\d+)?(?![\d,])", one, v)
    hits = []
    for m in _AMOUNT.finditer(quote or ""):
        n = re.sub(r"[^\d.]", "", re.sub(r"\s*(?:million|billion|thousand|[MBK])\b", "", m.group(0), flags=re.I).split("$")[-1])
        unit = re.search(r"(million|billion|thousand|[MBK])\b", m.group(0), re.I)
        mult = {"m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9, "k": 1e3, "thousand": 1e3}.get(unit.group(1).lower(), 1) if unit else 1
        try:
            if abs(float(n.rstrip(".")) * mult - float(v)) < 0.5:
                hits.append(m.group(0).strip().rstrip(",."))  # "$10,000,000," at a clause break
        except ValueError:
            pass
    return hits[0] if len(set(hits)) == 1 else value


def check_answer(doc, answer: dict, profile: str | None = None) -> tuple[dict, list]:
    nd = doc.normalized()
    kept, dropped = {}, []
    for name, (kind, _) in (fields.profile_fields(profile) if profile else fields.FIELDS).items():
        v = answer.get(name)
        if kind in ("enum", "text"):
            kept[name] = v or None
            continue
        items = v if kind == "list" else ([v] if v and v.get("value") not in (None, "", "not_stated") else [])
        good = []
        for it in items:
            if it.get("value") in (None, "", "not_stated"):
                continue
            qm = locate_quote(it.get("quote"), doc, nd, ignore_case=True, min_words=fields.MIN_WORDS.get(name, 5))
            if not qm.verified:
                dropped.append({"field": name, "value": it.get("value"), "quote": it.get("quote"), "why": "quote not found"})
                continue
            why = fields.rule(name, it)
            if why:
                dropped.append({"field": name, "value": it.get("value"), "quote": it.get("quote"), "why": why})
                continue
            if name == "stated_value":
                # Architect-engineer services (PSC C...): the dollar figure is almost always the cost of the
                # construction being designed, not this contract's value (2 of 30 in the 2026-11-12 check).
                if (doc.meta.get("psc") or "").upper().startswith("C") and re.search(r"construction", it.get("quote") or "", re.I):
                    dropped.append({"field": name, "value": it.get("value"), "quote": it.get("quote"),
                                    "why": "construction cost on an A-E notice"})
                    continue
                it = {**it, "value": as_written(it.get("value"), it.get("quote"))}
            good.append({**it, "pages": qm.pages, "match": qm.match})
        kept[name] = good if kind == "list" else (good[0] if good else None)
    if profile == "solicitation" and not kept.get("contract_type"):
        kept["contract_type"] = contract_type_by_rule(doc, nd)
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
        prof = r.get("profile")
        if r.get("answer") is None or not prof:
            continue
        cur = fields.PROFILES[prof]["version"]
        old_ok = r.get("prompt_version") in fields.PROFILES[prof].get("accept_uncut", []) and not (r.get("input") or {}).get("cut")
        if r.get("prompt_version") == cur:
            latest[r["notice_id"]] = r          # the current version always wins
        elif old_ok and latest.get(r["notice_id"], {}).get("prompt_version") != cur:
            latest[r["notice_id"]] = r
    n_drop = {}
    with open(a.out, "w") as fh:
        for nid, r in latest.items():
            doc = prepare.load(Path(a.text), rows[nid])
            kept, dropped = check_answer(doc, r["answer"], r["profile"])
            for d in dropped:
                n_drop[(d["field"], d["why"])] = n_drop.get((d["field"], d["why"]), 0) + 1
            fh.write(json.dumps({"notice_id": nid, "profile": r["profile"], "meta": doc.meta, "input": r.get("input"), "usage": r.get("usage"),
                                 "kept": kept, "dropped": dropped}, default=str) + "\n")
    print(json.dumps({"notices": len(latest), "dropped": {f"{k[0]}: {k[1]}": v for k, v in sorted(n_drop.items())}}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
