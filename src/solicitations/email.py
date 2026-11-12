"""Send the week's post as an email through Buttondown.

    RFP_BUTTONDOWN_API_KEY=... uv run python -m solicitations.email data/posts/2026-11-09.json [--draft]

Uses RFP_BUTTONDOWN_API_KEY, never the J&A digest's key, so it can only reach this newsletter's subscribers.
Before sending it lists the newsletter's recent emails and does nothing if this week's post was already
sent (metadata.week), so a rerun never sends twice. API details as in federal_ja.digest.buttondown
(checked there against Buttondown's OpenAPI schema).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

from .digest import LABELS
from .site import DATASET_URL, SITE_URL, TITLE, date_label, week_label

API = "https://api.buttondown.com/v1"
API_VERSION = "2026-04-01"
KIND = "rfp-watch-weekly"


def headers(send_now: bool) -> dict:
    key = os.environ.get("RFP_BUTTONDOWN_API_KEY")
    if not key:
        raise SystemExit("RFP_BUTTONDOWN_API_KEY is not set")
    h = {"Authorization": f"Token {key}", "X-API-Version": API_VERSION, "Content-Type": "application/json",
         "User-Agent": "federal-solicitations weekly"}
    if send_now:
        h["X-Buttondown-Live-Dangerously"] = "true"
    return h


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
    out += [f"How offers will be judged, where stated ({s['evaluation_basis_found']:,} of {s['solicitations']:,}): {eb}.", "",
            f"Named incumbents, every stated value, and each value's source sentence: [the full post]({url}) and "
            f"[the dataset]({DATASET_URL}). Picks are chosen and written up by Claude; every value is read from the "
            f"notice's documents and checked against them. Check the notice before relying on anything."]
    return "\n".join(out)


def already_sent(week: str) -> bool:
    r = requests.get(f"{API}/emails", headers=headers(False), params={"-status": "draft", "ordering": "-creation_date"}, timeout=60)
    r.raise_for_status()
    return any((e.get("metadata") or {}).get("kind") == KIND and (e.get("metadata") or {}).get("week") == week
               for e in r.json().get("results") or [])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solicitations.email")
    ap.add_argument("post", type=Path)
    ap.add_argument("--draft", action="store_true", help="save a draft in Buttondown instead of sending")
    ap.add_argument("--print", action="store_true", help="print the email and exit")
    a = ap.parse_args(argv)
    p = json.loads(a.post.read_text())
    subject = f"{TITLE}: week of {week_label(p)}" + (f" — {p['picks'][0]['headline']}" if p["picks"] else "")
    text = body(p)
    if a.print:
        print(subject + "\n\n" + text)
        return 0
    if not a.draft and already_sent(p["week_start"]):
        print(f"week {p['week_start']} was already sent; nothing done")
        return 0
    status = "draft" if a.draft else "about_to_send"
    r = requests.post(f"{API}/emails", headers=headers(status == "about_to_send"), timeout=60,
                      json={"subject": subject[:250], "body": text, "status": status,
                            "metadata": {"kind": KIND, "week": p["week_start"]}})
    if r.status_code != 201:
        raise SystemExit(f"Buttondown refused the email: HTTP {r.status_code} {r.text[:300]}")
    print(json.dumps({"id": r.json().get("id"), "status": r.json().get("status")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
