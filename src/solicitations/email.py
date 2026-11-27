"""Send the week's post as the RFP Watch topic of the Federal Data Stories newsletter (Buttondown).

    BUTTONDOWN_API_KEY=... uv run python -m solicitations.email data/posts/2026-11-09.json [--send] [--preview-to me@x]

The list carries several topics as subscriber-editable tags; topic_mail sends only to subscribers tagged
"RFP Watch". It always saves a draft first and checks it, and it never sends the same week twice
(metadata topic + edition), so a rerun is safe. Without --send nothing goes out; the weekly workflow
passes --send only when the repository variable RFP_EMAIL_SEND is "true".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from topic_mail import send

from . import fields
from .digest import LABELS, scrub_post
from .site import DATASET_URL, EVAL_EMPTY, SITE_URL, TITLE, date_label, week_label

TOPIC = "RFP Watch"


def md(s) -> str:
    return str(s or "").replace("*", "\\*").replace("_", "\\_").replace("[", "\\[").replace("]", "\\]")


def body(p: dict) -> str:
    s, L = p["summary"], p["lists"]
    as_of = p.get("as_of") or p["built_at"][:10]
    url = f"{SITE_URL}/posts/{p['week_start']}"
    out = [f"Every federal services and construction solicitation and sources-sought notice posted on SAM.gov, {week_label(p)}: "
           f"{s['solicitations']:,} solicitations and {s['sources_sought']:,} sources-sought notices. "
           f"[Read it on the web]({url}).", ""]
    for i, x in enumerate(p["picks"], 1):
        n = x["notice"]
        out += [f"## {i}. {md(x['headline'])}", "",
                f"**{md(n['title'])}** · {md(n.get('sub_tier') or n.get('agency'))} · {md(n.get('office'))}", "",
                md(x["why"]), "", f"> “{md(x['quote'])}”", "",
                f"[Notice on SAM.gov]({n['sam_link']})" + (f" · responses due {date_label(n['response_deadline'])}" if n.get("response_deadline") else ""), ""]
    if L.get("sole_source_intent"):
        out += ["## Planned sole-source awards", ""]
        for n in L["sole_source_intent"]:
            d = (n.get("response_deadline") or "")[:10]
            st = "" if not d else (" (responses open)" if d >= as_of else " (closed)")
            out.append(f"- **{md(n['sole_source_intent'])}**: [{md(n['title'])}]({n['sam_link']}), {md(n.get('sub_tier') or n.get('agency'))}{st}")
        out.append("")
    if L.get("largest_values"):
        out += ["## Largest stated values", ""]
        out += [f"- **{md(n['stated_value'])}**: [{md(n['title'])}]({n['sam_link']}), {md(n.get('sub_tier') or n.get('agency'))}" for n in L["largest_values"]]
        out.append("")
    eb = ", ".join(f"{LABELS[k]} {v:,}" for k, v in s["evaluation_basis"].items())
    inc = "Named incumbents" if fields.published("solicitation", "incumbent") else "Named incumbents (sources-sought notices)"
    out += [f"How offers will be judged, where found in the solicitation ({s['evaluation_basis_found']:,} of {s['solicitations']:,}): {eb}. "
            f"{EVAL_EMPTY}", "",
            f"{inc}, every stated value, and each value's source sentence: [the full post]({url}) and "
            f"[the dataset]({DATASET_URL}). Picks are chosen and written up by Claude; every value is read from the "
            f"notice's documents and checked against them. Check the notice before relying on anything."]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solicitations.email")
    ap.add_argument("post", type=Path)
    ap.add_argument("--send", action="store_true", help="send it (default: save a checked draft in Buttondown)")
    ap.add_argument("--dry-run", action="store_true", help="check the topic and earlier sends, write nothing")
    ap.add_argument("--preview-to", action="append", default=[], metavar="EMAIL",
                    help="also email a test copy of the draft to this address (repeatable)")
    ap.add_argument("--print", action="store_true", help="print the email and exit")
    a = ap.parse_args(argv)
    p = scrub_post(json.loads(a.post.read_text()))   # fields held from publishing (fields.HELD) never go out
    subject = f"{TITLE}: week of {week_label(p)}" + (f" — {p['picks'][0]['headline']}" if p["picks"] else "")
    text = body(p)
    if a.print:
        print(subject + "\n\n" + text)
        return 0
    mode = "dry-run" if a.dry_run else ("send" if a.send else "draft")
    r = send(TOPIC, subject[:250], text, edition=p["week_start"], mode=mode, preview_to=a.preview_to or None)
    print(json.dumps({k: r.get(k) for k in ("action", "id", "status", "url")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
