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

from .digest import scrub_post
from .opening import kept_text
from .site import DATASET_URL, SITE_URL, TITLE, date_label, week_label

TOPIC = "RFP Watch"


def md(s) -> str:
    return str(s or "").replace("*", "\\*").replace("_", "\\_").replace("[", "\\[").replace("]", "\\]")


EMAIL_LARGEST = 3   # the email lists the three largest stated values; the web page lists ten
TITLE_CHARS = 90    # list items in the email show the notice title up to this length; the link has the rest


def short(t) -> str:
    t = " ".join(str(t or "").split())
    return t if len(t) <= TITLE_CHARS else t[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def email_sole_source(p: dict) -> tuple[list[dict], int, int]:
    """The planned sole-source awards the email lists: those whose response deadline has not passed as of the
    post's date (or has none) and that are not already a pick. Returns (listed, closed, picked)."""
    as_of = p.get("as_of") or p["built_at"][:10]
    picked = {x["notice"].get("notice_id") for x in p.get("picks") or []}
    listed, closed, dup = [], 0, 0
    for n in (p.get("lists") or {}).get("sole_source_intent") or []:
        d = (n.get("response_deadline") or "")[:10]
        if n.get("notice_id") in picked:
            dup += 1
        elif d and d < as_of:
            closed += 1
        else:
            listed.append(n)
    return listed, closed, dup


def body(p: dict) -> str:
    L = p["lists"]
    as_of = p.get("as_of") or p["built_at"][:10]
    url = f"{SITE_URL}/posts/{p['week_start']}"
    lead = kept_text(p)   # the checked opening on what the week's notices are buying (opening.py), if it passed
    out = ([md(lead), ""] if lead else []) + [
        f"Federal services and construction solicitations and sources-sought notices posted on SAM.gov, {week_label(p)}. "
        f"[Read it on the web]({url}).", ""]
    for i, x in enumerate(p["picks"], 1):
        n = x["notice"]
        out += [f"## {i}. {md(x['headline'])}", "",
                f"**{md(n['title'])}** · {md(n.get('sub_tier') or n.get('agency'))} · {md(n.get('office'))}", "",
                md(x["why"]), "", f"> “{md(x['quote'])}”", "",
                f"[Notice on SAM.gov]({n['sam_link']})" + (f" · responses due {date_label(n['response_deadline'])}" if n.get("response_deadline") else ""), ""]
    # What the email lists (the web page lists everything; see email_lists):
    ss, n_closed, n_picked = email_sole_source(p)
    if ss:
        out += ["## Planned sole-source awards, responses still open", ""]
        for n in ss:
            due = f", responses due {date_label(n['response_deadline'])}" if n.get("response_deadline") else ""
            out.append(f"- **{md(n['sole_source_intent'])}**: [{md(short(n['title']))}]({n['sam_link']}), {md(n.get('sub_tier') or n.get('agency'))}{due}")
        out.append("")
    left = [f"{n_closed} whose response period has closed" if n_closed else "",
            f"{n_picked} picked out above" if n_picked else ""]
    left = [x for x in left if x]
    if left:
        out += [f"Not listed here: {' and '.join(left)} (planned sole-source awards; all are on [the web page]({url})).", ""]
    lv = (L.get("largest_values") or [])[:EMAIL_LARGEST]
    if lv:
        out += [f"## The {len(lv)} largest stated values", ""]
        out += [f"- **{md(n['stated_value'])}**: [{md(short(n['title']))}]({n['sam_link']}), {md(n.get('sub_tier') or n.get('agency'))}" for n in lv]
        out.append("")
    # The counts (how offers will be judged, notices per field) are on the web page, not in the email.
    out += [f"On [the web page]({url}): counts, how offers are judged, named incumbents, the ten largest and every "
            f"stated value, each with its source sentence. All of it is in [the dataset]({DATASET_URL}). The opening "
            f"and picks are written by Claude and checked in code against the notices."]
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
