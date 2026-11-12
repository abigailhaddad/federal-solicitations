"""Build one weekly post from the week's published tables: a few notices picked out by Claude, and lists
and counts computed from the data.

    uv run python -m solicitations.digest work/publish --max-usd 1 [--dry-run]

What Claude does: reads one line per candidate notice (its title, agency, SAM.gov description opening and
the checked values with their quotes) and picks the few a reporter, Hill staffer or contracts lawyer would
most want to know about. It writes a headline and a short explanation per pick and quotes the notice.

What code does, and checks (a pick that fails is dropped and kept in the post file under `dropped`):
* the quote must be in the notice: in one of its checked quotes or its SAM.gov description, ignoring case
  and whitespace;
* a dollar figure in the headline or explanation must be in the notice's stated value;
* "sole source", "incumbent" and "lowest price technically acceptable" may be said of a pick only if a
  pick's notice has that value in the data;
* every count, list and fact line on the page is computed from the tables, never written by the model.

One post per week (the week in the tables); it refuses to overwrite one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
POSTS = ROOT / "data" / "posts"
MODEL = os.environ.get("DIGEST_MODEL", "claude-opus-5-5")
MAX_PICKS = 5
PRICE_PER_MTOK = {"claude-opus-5-5": (5.0, 25.0)}   # input, output (USD per million tokens)

PROMPT = """You help write a weekly page about federal contracting notices posted on SAM.gov. This week has
{n_sol} solicitations for services and {n_ss} sources-sought notices (market research before a solicitation).
Below are the {n} that have something a reader might care about, one per line, each starting with its [index].
Each line has the notice's title, agency, deadline, the SAM.gov set-aside field, the opening of its SAM.gov
description, and values read from its documents, each followed by the document sentence it came from.

Pick {lo} to {hi} notices that a reporter covering federal contracting, a congressional staffer or a government
contracts lawyer would most want to know about this week: for example a plan to sole-source to a named firm,
a large stated value, a named incumbent losing or keeping work, a contract structure or evaluation method
that stands out for what is being bought, or an unusual requirement. One notice per pick. If little stands
out, return fewer picks.

Rules:
- Use ONLY what the line says. No outside knowledge about companies, programs, laws or people.
- Name agencies, offices and firms exactly as written; do not expand abbreviations.
- Don't characterize beyond the line (no "routine", "first-ever", "controversial") unless it says so.
- "quote" must be copied exactly from that notice's line: from one of its quoted sentences or its description,
  10 to 250 characters.
- State a dollar amount only if it is the notice's stated value.
- headline: plain, specific, no hype, at most 120 characters. why: 1 to 3 sentences.

Notices:
{lines}
"""

SCHEMA = {
    "type": "object",
    "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "headline": {"type": "string"},
                       "why": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["index", "headline", "why", "quote"], "additionalProperties": False}}},
    "required": ["picks"], "additionalProperties": False,
}

LABELS = {"lpta": "lowest price technically acceptable", "tradeoff": "best-value tradeoff",
          "highest_technically_rated": "highest technically rated", "lowest_price": "lowest price",
          "other": "other"}


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def val(x):
    """None for NaN/None/empty, else the value (lists from parquet come back as arrays)."""
    if x is None:
        return None
    if hasattr(x, "tolist") and not isinstance(x, str):
        x = x.tolist()
    if isinstance(x, float) and x != x:
        return None
    return x if x != "" and x != [] else None


def load(publish: Path) -> tuple[list[dict], list[dict]]:
    sol = pd.read_parquet(publish / "data" / "solicitations.parquet")
    ss = pd.read_parquet(publish / "data" / "sources_sought.parquet")
    conv = lambda df: [{k: val(v) for k, v in r.items()} for r in df.to_dict("records")]  # noqa: E731
    return conv(sol), conv(ss)


def quotes_of(r: dict) -> list[str]:
    out = []
    for k, v in r.items():
        if k.endswith("_quote") and v:
            out.append(v)
        if k.endswith("_quotes") and v:
            out += list(v)
    return out


def interesting(r: dict, track: str) -> bool:
    if track == "sources_sought":
        return bool(r.get("sole_source_intent") or r.get("incumbent_quote") or r.get("stated_value"))
    return bool(r.get("incumbent_quote") or r.get("stated_value") or r.get("evaluation_basis") in ("lpta", "highest_technically_rated"))


def line(i: int, r: dict, track: str) -> str:
    desc = re.sub(r"\s+", " ", r.get("description") or "")[:400]
    bits = [f"[{i}] {'SOURCES SOUGHT' if track == 'sources_sought' else 'SOLICITATION'}: {r['title']}",
            f"{r['agency']} / {r['sub_tier']} / {r['office']}", f"deadline {r.get('response_deadline') or '-'}",
            f"set-aside field: {r.get('set_aside_sam') or 'none'}"]
    if r.get("sole_source_intent"):
        bits.append(f'plans sole source to: {r["sole_source_intent"]} ("{r["sole_source_intent_quote"]}")')
    if r.get("incumbent_quote"):
        who = " / ".join(x for x in (r.get("incumbent_firm"), r.get("incumbent_contract_number")) if x)
        bits.append(f'incumbent: {who} ("{r["incumbent_quote"]}")')
    if r.get("stated_value"):
        bits.append(f'stated value: {r["stated_value"]} {r.get("stated_value_what") or ""} ("{r["stated_value_quote"]}")')
    if r.get("evaluation_basis"):
        bits.append(f'evaluation: {LABELS[r["evaluation_basis"]]} ("{r["evaluation_basis_quote"]}")')
    if r.get("contract_type"):
        bits.append("contract type: " + ", ".join(r["contract_type"]))
    bits.append(f"description: {desc}")
    return " | ".join(bits)


_MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(billion|million|thousand|[BMK])?\b", re.I)
_MULT = {"b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}


def dollars(text: str) -> list[tuple[float, float]]:
    out = []
    for num, unit in _MONEY.findall(text or ""):
        mult = _MULT.get((unit or "").lower(), 1.0)
        v = float(num.replace(",", "")) * mult
        dec = len(num.split(".")[1]) if "." in num else 0
        out.append((v, max(0.5 * mult / 10 ** dec, 0.01 * v)))
    return out


def check_pick(p: dict, cands: list[tuple[dict, str]]) -> str | None:
    i = p.get("index")
    if not isinstance(i, int) or not 0 <= i < len(cands):
        return f"bad index {i}"
    r, track = cands[i]
    q = norm(p.get("quote"))
    if len(q) < 10:
        return "quote too short"
    if not any(q in norm(t) for t in quotes_of(r) + [r.get("description")]):
        return "quote not found in the notice's checked quotes or description"
    allowed = [v for v, _ in dollars(r.get("stated_value_quote") or "")] + [v for v, _ in dollars(r.get("stated_value") or "")]
    for v, tol in dollars(f"{p.get('headline')} {p.get('why')}"):
        if not any(abs(v - a) <= tol for a in allowed):
            return f"dollar figure {v:,.0f} is not the notice's stated value"
    text = f"{p.get('headline')} {p.get('why')}"
    if re.search(r"sole[\s-]source", text, re.I) and not r.get("sole_source_intent"):
        return "says sole source but the notice has no sole-source value"
    if re.search(r"\bincumbent\b", text, re.I) and not r.get("incumbent_quote"):
        return "says incumbent but the notice has no incumbent value"
    if re.search(r"\bLPTA\b|lowest[\s-]price[\s,-]+technically", text, re.I) and r.get("evaluation_basis") != "lpta":
        return "says LPTA but the notice's evaluation basis is not lpta"
    return None


def fact(r: dict, track: str) -> dict:
    keep = ["notice_id", "sam_link", "solicitation_number", "title", "notice_type", "agency", "sub_tier", "office",
            "posted", "response_deadline", "set_aside_sam", "naics", "psc", "evaluation_basis", "evaluation_basis_quote",
            "relative_importance_quote", "evaluation_factors", "contract_type", "stated_value", "stated_value_what",
            "stated_value_quote", "incumbent_firm", "incumbent_contract_number", "incumbent_quote",
            "sole_source_intent", "sole_source_intent_quote"]
    return {"track": track} | {k: r.get(k) for k in keep if k in r}


def summarize(sol: list[dict], ss: list[dict]) -> dict:
    from collections import Counter
    return {
        "solicitations": len(sol), "sources_sought": len(ss),
        "solicitations_new": sum(1 for r in sol if r.get("new_in_week")),
        "sources_sought_new": sum(1 for r in ss if r.get("new_in_week")),
        "evaluation_basis": dict(Counter(r["evaluation_basis"] for r in sol if r.get("evaluation_basis")).most_common()),
        "evaluation_basis_found": sum(1 for r in sol if r.get("evaluation_basis")),
        "contract_type": dict(Counter(t for r in sol for t in (r.get("contract_type") or [])).most_common()),
        "solicitations_with_value": sum(1 for r in sol if r.get("stated_value")),
        "sources_sought_with_value": sum(1 for r in ss if r.get("stated_value")),
        "solicitations_with_incumbent": sum(1 for r in sol if r.get("incumbent_quote")),
        "sources_sought_with_incumbent": sum(1 for r in ss if r.get("incumbent_quote")),
        "sole_source_intent": sum(1 for r in ss if r.get("sole_source_intent")),
        "no_document_text": sum(1 for r in sol + ss if not r.get("files_read")),
    }


def call_model(prompt: str, client=None) -> tuple[list[dict], dict]:
    import anthropic
    c = client or anthropic.Anthropic(default_headers={"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]}
                                      if os.environ.get("ANTHROPIC_WORKSPACE_ID") else None)
    r = c.messages.create(model=MODEL, max_tokens=8000, output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                          messages=[{"role": "user", "content": prompt}])
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"model stop_reason={r.stop_reason}")
    picks = json.loads(next(b.text for b in r.content if b.type == "text"))["picks"]
    pin, pout = PRICE_PER_MTOK.get(MODEL, (5.0, 25.0))
    usage = {"model": MODEL, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens,
             "cost_usd": round((r.usage.input_tokens * pin + r.usage.output_tokens * pout) / 1e6, 4)}
    return picks, usage


def build(sol: list[dict], ss: list[dict], picks: list[dict], usage: dict, cands, week_start: str) -> dict:
    kept, dropped = [], []
    for p in picks:
        why_not = check_pick(p, cands)
        if why_not:
            dropped.append({**p, "dropped_because": why_not})
            continue
        r, track = cands[p["index"]]
        kept.append({"headline": p["headline"].strip(), "why": p["why"].strip(), "quote": p["quote"].strip(),
                     "notice": fact(r, track)})
        if len(kept) == MAX_PICKS:
            break
    week_end = (dt.date.fromisoformat(week_start) + dt.timedelta(days=6)).isoformat()
    lists = {
        "sole_source_intent": [fact(r, "sources_sought") for r in ss if r.get("sole_source_intent")],
        "incumbent": [fact(r, "solicitation") for r in sol if r.get("incumbent_quote")]
                     + [fact(r, "sources_sought") for r in ss if r.get("incumbent_quote")],
        "stated_value": [fact(r, "solicitation") for r in sol if r.get("stated_value")]
                        + [fact(r, "sources_sought") for r in ss if r.get("stated_value")],
    }
    return {"week_start": week_start, "week_end": week_end, "summary": summarize(sol, ss), "picks": kept,
            "dropped": dropped, "lists": lists, "usage": usage,
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(prog="solicitations.digest")
    ap.add_argument("publish", type=Path, help="folder with data/solicitations.parquet and data/sources_sought.parquet")
    ap.add_argument("--max-usd", type=float, required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    sol, ss = load(a.publish)
    week_start = sol[0]["week_start"]
    path = POSTS / f"{week_start}.json"
    if path.exists() and not a.dry_run:
        print(f"{path} exists; one post a week, nothing built")
        return 0
    cands = [(r, "sources_sought") for r in ss if interesting(r, "sources_sought")] + \
            [(r, "solicitation") for r in sol if interesting(r, "solicitation")]
    prompt = PROMPT.format(n_sol=len(sol), n_ss=len(ss), n=len(cands), lo=min(3, len(cands)), hi=MAX_PICKS,
                           lines="\n".join(line(i, r, t) for i, (r, t) in enumerate(cands)))
    est = len(prompt) / 3.5 * 5 / 1e6 + 8000 * 25 / 1e6
    print(json.dumps({"candidates": len(cands), "prompt_chars": len(prompt), "estimated_max_usd": round(est, 3)}), file=sys.stderr)
    if est > a.max_usd:
        print(f"refusing: estimate ${est:.2f} is over --max-usd {a.max_usd}")
        return 1
    picks, usage = call_model(prompt, client) if cands else ([], {})
    post = build(sol, ss, picks, usage, cands, week_start)
    print(json.dumps({"week": week_start, "summary": post["summary"], "picks": [p["headline"] for p in post["picks"]],
                      "dropped": [(d["headline"], d["dropped_because"]) for d in post["dropped"]], "usage": usage},
                     indent=1), file=sys.stderr)
    out = path if not a.dry_run else ROOT / "work" / f"post_dry_{week_start}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(post, indent=1, ensure_ascii=False, default=str) + "\n")
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
