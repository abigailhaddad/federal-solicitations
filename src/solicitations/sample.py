"""Draw the check samples for every field from checked answers.

    uv run python -m solicitations.sample work/week.csv work/checked.jsonl --out work/checks [--n 30 --blanks 10 --seed 1]

Per field, two files:
  <field>.json         a random --n of the kept values: value, quote, the document text around the quote
                       (from the whole package), the SAM.gov link and the field's written definition.
  <field>_blanks.json  a random --blanks of notices where the field is empty but the package has words
                       on that topic, with the text around those words: to measure what the field misses.
Checkers write verdicts_<field>.json and verdicts_<field>_blanks.json next to them.
"""
import argparse
import json
import random
import re
from pathlib import Path

from federal_ja.analysis.text import normalize

from . import fields, prepare

TOPIC_WORDS = {
    "evaluation_basis": r"lowest price technically acceptable|LPTA|trade-?off|best value|highest technically rated|basis for award",
    "evaluation_factors": r"evaluation factors?|evaluation criteria|factors? for award",
    "relative_importance": r"more important than|relative importance|equal(?:ly)? (?:in )?important|descending order of importance",
    "contract_type": r"firm[- ]fixed[- ]price|time[- ]and[- ]materials|labor[- ]hour|cost[- ]plus|indefinite[- ]delivery|\bIDIQ\b|blanket purchase",
    "award_count": r"multiple awards?|single award|one award|up to \w+ awards",
    "period_of_performance": r"period of performance|base (?:period|year)|option (?:period|year)",
    "stated_value": r"estimated (?:total )?value|estimated amount|ceiling|not[- ]to[- ]exceed \$|magnitude",
    "incumbent": r"incumbent|currently (?:being )?performed by|follow[- ]on to|current contract(?:or)?",
    "clearance": r"security clearance|top secret|\bsecret\b|public trust|\bSCI\b",
    "cmmc_level": r"\bCMMC\b|Cybersecurity Maturity Model",
    "brand_name": r"brand[- ]name",
    "oral_presentation": r"oral presentation|orals|interviews?",
    "page_limit": r"page limit|pages? maximum|not (?:to )?exceed \d+ pages|limited to \d+ pages",
    "set_aside_stated": r"set[- ]aside|8\(a\)|SDVOSB|HUBZone|WOSB|full and open",
    "planned_strategy": r"set[- ]aside|sole source|full and open|intends? to|anticipates?",
    "sole_source_intent": r"sole[- ]source|only one (?:responsible )?source|intends? to (?:award|negotiate|issue)|limited competiti",
    "set_aside_considered": r"set[- ]aside|8\(a\)|SDVOSB|HUBZone|WOSB",
}


def window(text: str, start: int, end: int, half: int = 2500) -> str:
    return text[max(0, start - half): end + half]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("week_csv")
    ap.add_argument("checked")
    ap.add_argument("--text", default="work/text")
    ap.add_argument("--out", default="work/checks")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--blanks", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--only", default=None, help="comma-separated <track>__<field> names to sample")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = {r["notice_id"]: r for r in prepare.rows(a.week_csv)}
    recs = [json.loads(l) for l in Path(a.checked).read_text().splitlines()]
    docs = {}

    def text_of(nid):
        if nid not in docs:
            docs[nid] = normalize(prepare.load(Path(a.text), rows[nid]).verification_text())
        return docs[nid]

    rng = random.Random(a.seed)
    summary = {}
    for profile in fields.PROFILES:
      for name, (kind, definition) in fields.profile_fields(profile).items():
        if kind in ("enum", "text"):
            continue
        name_out = f"{profile}__{name}"
        if a.only and name_out not in a.only.split(","):
            continue
        precs = [r for r in recs if r.get("profile") == profile]
        items = []
        for r in precs:
            v = r["kept"].get(name)
            for it in (v if isinstance(v, list) else ([v] if v else [])):
                items.append((r["notice_id"], it))
        pick = rng.sample(items, min(a.n, len(items)))
        sample = []
        for nid, it in pick:
            t = text_of(nid)
            q = normalize(it.get("quote") or "")
            k = t.find(q) if q in t else t.lower().find(q.lower())
            sample.append({"notice_id": nid, "sam_link": f"https://sam.gov/opp/{nid}/view", "title": rows[nid]["title"],
                           "notice_type": rows[nid]["type"], "value": {x: y for x, y in it.items() if x not in ("pages", "match")},
                           "quote_pages": it.get("pages"), "context": window(t, k, k + len(q)) if k >= 0 else t[:5000]})
        empty = [r["notice_id"] for r in precs if not r["kept"].get(name)]
        rx = re.compile(TOPIC_WORDS[name], re.I)
        cand = [nid for nid in empty if rx.search(text_of(nid))]
        blanks = []
        for nid in rng.sample(cand, min(a.blanks, len(cand))):
            t = text_of(nid)
            hits = [m for m in rx.finditer(t)][:3]
            blanks.append({"notice_id": nid, "sam_link": f"https://sam.gov/opp/{nid}/view", "title": rows[nid]["title"],
                           "notice_type": rows[nid]["type"],
                           "context": "\n[...]\n".join(window(t, m.start(), m.end(), 1500) for m in hits)})
        for suffix, data in (("", sample), ("_blanks", blanks)):
            json.dump({"field": name, "track": profile, "definition": definition, "items": data},
                      open(out / f"{name_out}{suffix}.json", "w"), indent=1)
        summary[name_out] = {"kept_values": len(items), "notices_with_value": len({n for n, _ in items}),
                         "sampled": len(sample), "empty_with_topic_words": len(cand), "blanks_sampled": len(blanks)}
    json.dump(summary, open(out / "summary.json", "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
