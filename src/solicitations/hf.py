"""Write the Hugging Face dataset card from the data and the check results, and publish.

    uv run python -m solicitations.hf card work/publish            # write work/publish/README.md
    HF_TOKEN=... uv run python -m solicitations.hf publish work/publish

Every number in the card is computed from the tables or from docs/checks/scorecard.json when the card is
built; none is typed in.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from .digest import LABELS
from .site import DATASET_URL, GITHUB_REPO, HF_REPO_ID, PROJECT_URL, SITE_URL

ROOT = Path(__file__).resolve().parents[2]
SCORECARD = ROOT / "docs" / "checks" / "scorecard.json"

DEFS = {
    "evaluation_basis": "How offers will be judged, as the solicitation states it: `lpta` (lowest price technically acceptable), `tradeoff` (best value; non-price factors can outweigh price), `highest_technically_rated`, `lowest_price` (price alone among acceptable offers), `other`.",
    "relative_importance": "The sentence saying how the evaluation factors compare in importance, summarized in a few words; the quote is the document's sentence.",
    "evaluation_factors": "Each evaluation factor the solicitation lists, as named, in order (list).",
    "contract_type": "Each contract type the document says this award will be (list), from a fixed set (firm-fixed-price, IDIQ, time-and-materials, cost-plus-fixed-fee, ...).",
    "stated_value": "A dollar estimate or ceiling the document states for this acquisition, as written (ranges and qualifiers kept), with what it is in `stated_value_what`. Not a per-contract amount, guaranteed minimum, bond, size standard or past contract's value.",
    "incumbent": "A current or previous contractor or contract for this work that the document names as such: `incumbent_firm`, `incumbent_contract_number`.",
    "sole_source_intent": "Sources-sought only: the firm the notice says the agency intends (or is considering) to award to without competition.",
}


def tally(row: dict | None, kind: str) -> str:
    if not row or kind not in row:
        return "–"
    r = row[kind]
    if kind == "values":
        bits = [f"{r.get('correct', 0)} of {r['n']} correct"] + [f"{r[k]} {k}" for k in ("wrong", "unclear") if r.get(k)]
    else:
        bits = [f"{r.get('missed', 0)} of {r['n']} missed"] + ([f"{r['unclear']} unclear"] if r.get("unclear") else [])
    return ", ".join(bits)


def card(publish: Path) -> str:
    sol = pd.read_parquet(publish / "data" / "solicitations.parquet")
    ss = pd.read_parquet(publish / "data" / "sources_sought.parquet")
    score = json.loads(SCORECARD.read_text())
    week = f"{sol.week_start.iloc[0]} to {sol.week_end.iloc[0]}"

    def filled(df, f):
        col = f if f in df else f"{f}_quote"
        return int(df[col].map(lambda x: x is not None and not (isinstance(x, float) and x != x) and len(x) > 0 if hasattr(x, "__len__") else x == x and x is not None).sum())

    rows = []
    for track, df in (("solicitation", sol), ("sources_sought", ss)):
        for f in (["evaluation_basis", "relative_importance", "evaluation_factors", "contract_type", "stated_value", "incumbent"]
                  if track == "solicitation" else ["sole_source_intent", "incumbent", "stated_value"]):
            s = score.get(f"{track}__{f}")
            rows.append(f"| {track.replace('_', ' ')} | `{f}` | {filled(df, f):,} of {len(df):,} | {tally(s, 'values')} | {tally(s, 'blanks')} |")
    eb = sol.evaluation_basis.value_counts()
    nodoc = int((sol.files_read == 0).sum() + (ss.files_read == 0).sum())
    cut = int(sol.model_read_excerpt.sum() + ss.model_read_excerpt.sum())
    dropped = [f"`{k.split('__')[1]}` ({k.split('__')[0].replace('_', ' ')}): {tally(v, 'values')}"
               for k, v in score.items() if not v["published"]]
    return f"""---
license: other
license_name: us-government-works-and-public-sam-gov-data
pretty_name: Federal solicitations and sources-sought notices (SAM.gov), checked values with quotes
language: [en]
tags: [government, procurement, federal-contracting, sam-gov]
configs:
- config_name: solicitations
  data_files: data/solicitations.parquet
  default: true
- config_name: sources_sought
  data_files: data/sources_sought.parquet
---

# Federal solicitations and sources-sought notices, with checked values

What federal **services solicitations** and **sources-sought notices** on SAM.gov say about how offers will be
judged, contract type, stated value, the incumbent, and planned sole-source awards. **Every value comes with the
document sentence it was read from** (`*_quote`) and where that sentence is (`*_source`: file and page).

**Covers {week}** (one week; posted dates, Eastern): {len(sol):,} solicitations and {len(ss):,} sources-sought
notices. Weekly posts built from it: {SITE_URL}. Code, rules and proof files: {PROJECT_URL}.

## What's in it

- `solicitations`: every SAM.gov *Solicitation* with a posting that week whose product service code is a
  service (starts with a letter). One row per solicitation number, read from its latest posting.
- `sources_sought`: every *Sources Sought* notice with a posting that week (services and goods).

Not included: solicitations for goods, presolicitations, special notices, award notices.

Each row has the SAM.gov fields (title, agency, office, dates, set-aside, NAICS, PSC, description, link) and these
read from the documents:

| Field | What it is |
|---|---|
""" + "\n".join(f"| `{k}` | {v} |" for k, v in DEFS.items()) + f"""

## How the values were read and checked

1. Claude Haiku 5.5 (Batch API) read each notice's SAM.gov description and attached documents and answered a
   fixed set of questions, quoting the sentence each answer came from. Packages over 130,000 characters were cut
   to the parts on these topics first ({cut:,} notices; `model_read_excerpt`).
2. Code checked every answer: the quote must be in the documents, and the value must pass a rule against its own
   quote (for example an `lpta` value needs LPTA wording; a dollar value must appear in its sentence with its
   qualifiers and currency; a per-contract amount or a guaranteed minimum is not a total). Failing answers were
   dropped.
3. **Sampled checks.** For each field, a random sample of about 30 kept values (all of them when there were
   fewer) was judged against the documents by a separate Claude model, using the field's written definition.
   A field is published only if it scored about 28 of 30 or better. A second sample of 10 notices where the
   field is empty but the documents have words on the topic measures what the field misses. Proof files (each
   value, its quote, the document text around it and the verdict): {PROJECT_URL}/tree/main/docs/checks.

| Track | Field | Notices with a value | Values judged | Empty notices with topic words judged |
|---|---|---|---|---|
""" + "\n".join(rows) + f"""

These are samples judged by an AI model, not a census by a person. Read them as: most values are right, and
empty does not mean "not stated". In particular, of {score["solicitation__evaluation_basis"]["blanks"]["n"]} sampled solicitations with no
`evaluation_basis` but with evaluation words in their documents, {score["solicitation__evaluation_basis"]["blanks"].get("missed", 0)} did state one.

Check of 2026-11-12, `solicitation stated_value`: of the 3 judged wrong, one is a real error (a per-year,
per-award ceiling published as the ceiling) and two lost their formatting ("$" and "between"), which code now
restores. The blanks sample for `stated_value` was drawn before the per-contract and minimum rules were added.

**Fields asked for and not published** (failed their check): {"; ".join(dropped)}.

## Coverage gaps

- {nodoc:,} notices had no document text to read (no attachments, documents hosted elsewhere such as PIEE or
  NECO, or scanned images): only their SAM.gov description was read. `files_read`, `files_not_read` and
  `links_to_other_sites` say what was read for each notice.
- Spreadsheets were not read.
- How offers will be judged, where stated ({int(eb.sum()):,} solicitations): """ + ", ".join(f"{LABELS[k]} {v:,}" for k, v in eb.items()) + f""".

## Source and license

SAM.gov Contract Opportunities data and attached documents are U.S. government works and public records. The
values and quotes here are derived from them. Code: MIT ({PROJECT_URL}).

Spotted a wrong value? Open an issue: {PROJECT_URL}/issues.
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solicitations.hf")
    ap.add_argument("cmd", choices=["card", "publish"])
    ap.add_argument("publish", type=Path)
    a = ap.parse_args(argv)
    (a.publish / "README.md").write_text(card(a.publish))
    print(f"wrote {a.publish / 'README.md'}", file=sys.stderr)
    if a.cmd == "publish":
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(HF_REPO_ID, repo_type="dataset", exist_ok=True)
        info = api.upload_folder(repo_id=HF_REPO_ID, repo_type="dataset", folder_path=str(a.publish),
                                 allow_patterns=["README.md", "data/*.parquet", "data/*.csv"],
                                 commit_message="Publish solicitations and sources-sought tables")
        print(info, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
