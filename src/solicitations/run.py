"""Send notices to Claude Haiku 5.5 as one Message Batch and save the answers.

    uv run python -m solicitations.run work/week.csv --ids work/pilot_ids.txt --out work/answers.jsonl --max-usd 1 [--dry-run]

Refuses to send if the estimated cost (prompt length, and output like the J&A runs) is over --max-usd.
Notices already in --out with this prompt version are skipped. The batch id is printed when created;
if the run dies after that, --resume-batch ID fetches the answers without sending anything again.
"""
import argparse
import json
import sys
from pathlib import Path

from federal_ja.analysis import llm

from . import fields, prepare

MODEL = "claude-haiku-5-5"
EFFORT = "high"
MAX_TOKENS = 32_000
EST_OUTPUT_TOKENS = 9_000


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("week_csv")
    ap.add_argument("--ids", required=True)
    ap.add_argument("--profile", required=True, choices=list(fields.PROFILES))
    ap.add_argument("--text", default="work/text")
    ap.add_argument("--out", default="work/answers.jsonl")
    ap.add_argument("--max-usd", type=float, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume-batch", default=None)
    a = ap.parse_args(argv)
    version = fields.PROFILES[a.profile]["version"]
    want = Path(a.ids).read_text().split()
    out = Path(a.out)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if r.get("prompt_version") == version and r.get("answer") is not None:
                done.add(r["notice_id"])
    rows = {r["notice_id"]: r for r in prepare.rows(a.week_csv)}
    text = Path(a.text)
    instr = fields.instructions(a.profile)
    prompts, facts, missing = {}, {}, []
    for nid in want:
        if nid in done:
            continue
        if not (text / f"{nid}.json.gz").exists():
            missing.append(nid)
            continue
        doc = prepare.load(text, rows[nid])
        p, f = prepare.prompt(doc, instr)
        prompts[nid], facts[nid] = p, f
    est = sum(llm.cost_usd(MODEL, len(p[0] + p[1]) // 4, EST_OUTPUT_TOKENS, price_mult=llm.BATCH_PRICE_MULT)
              for p in prompts.values())
    big = sum(1 for p in prompts.values() if len(p[0] + p[1]) // 4 > llm.LONG_PROMPT_TOKENS)
    print(json.dumps({"to_send": len(prompts), "already_done": len(done & set(want)), "not_fetched": len(missing),
                      "estimated_usd": round(est, 3), "prompts_over_100k_tokens": big,
                      "cut_packages": sum(f["cut"] for f in facts.values())}))
    if a.dry_run or not prompts:
        return 0
    if est > a.max_usd:
        print(f"refusing: estimate ${est:.2f} is over --max-usd {a.max_usd}")
        return 1
    res = llm.batch_call(prompts, schema=fields.schema(a.profile), tool_name="record_notice", model=MODEL,
                         max_tokens=MAX_TOKENS, effort=EFFORT, resume_id=a.resume_batch)
    spent, failed = 0.0, 0
    with out.open("a") as fh:
        for nid, r in res.items():
            if isinstance(r, Exception):
                failed += 1
                fh.write(json.dumps({"notice_id": nid, "prompt_version": version, "profile": a.profile, "model": MODEL,
                                     "answer": None, "error": str(r)[:300]}) + "\n")
                continue
            answer, usage = r
            spent += usage.get("cost_usd") or 0
            fh.write(json.dumps({"notice_id": nid, "prompt_version": version, "profile": a.profile, "model": MODEL,
                                 "effort": EFFORT, "answer": answer, "usage": usage, "input": facts[nid]}) + "\n")
    print(json.dumps({"answered": len(res) - failed, "failed": failed, "spent_usd": round(spent, 4)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
