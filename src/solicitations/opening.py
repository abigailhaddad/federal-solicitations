"""A short written opening for the week's post and email: what the week's notices are buying, checked in code.

Pattern copied from tracker-weekly's opening.py: code builds the facts, Claude writes the prose from them and
nothing else, and code checks every sentence before it is used. If any check fails the opening is dropped and the
post and email go without it.

The facts (all from the week's published tables, without fields held from publishing):
* the notices picked out below the opening, with the headline the post gives them;
* the kinds of work bought this week: the service groups of the product service code (PSC) with the most notices
  (product codes, on sources-sought notices only, are left out), each with up to
  EXAMPLES_PER_GROUP example notices (title, agency, office, deadline, checked values with their quotes, and the
  opening of the SAM.gov description). Group names are the PSC Manual's category names.

Claude returns the paragraph as a list of sentences, each citing the notices it rests on, each with a quote.
A sentence is dropped, and with it the whole opening, unless:
* it cites at least one notice, and each cited quote is found word for word in that notice's title, SAM.gov
  description or one of its checked quotes (ignoring case, whitespace and curly quotes);
* every number, and every capitalised word (names of agencies, offices, firms, places), appears in the facts of
  the notices it cites, the week's dates, or the group names, apart from a short list of ordinary words;
* every date ("December 8") is a date in a cited notice's facts or the week's dates;
* every dollar figure is a cited notice's stated value;
* "sole source", "incumbent" and "LPTA" are said only when a cited notice has that value;
* no counting words (most, many, several, majority, largest), no hype words, no advice to the reader, no prose
  about the input, no exclamation marks. Counts are on the page already, computed by code.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from . import fields

MODEL = os.environ.get("DIGEST_MODEL", "claude-opus-5-5")
PRICE_PER_MTOK = {"claude-opus-5-5": (5.0, 25.0)}
GROUPS_SHOWN = 8
EXAMPLES_PER_GROUP = 6
DESC_CHARS = 450

# First character of the product service code -> the PSC Manual's category name (services are letters;
# a digit is a product code, which only sources-sought notices carry here).
PSC_GROUPS = {
    "A": "Research and Development", "B": "Special Studies and Analyses", "C": "Architect and Engineering Services",
    "D": "Information Technology and Telecommunications", "E": "Purchase of Structures and Facilities",
    "F": "Natural Resources and Conservation Services", "G": "Social Services",
    "H": "Quality Control, Testing, and Inspection Services", "J": "Maintenance, Repair, and Rebuilding of Equipment",
    "K": "Modification of Equipment", "L": "Technical Representative Services",
    "M": "Operation of Government-Owned Facilities", "N": "Installation of Equipment", "P": "Salvage Services",
    "Q": "Medical Services", "R": "Professional, Administrative, and Management Support Services",
    "S": "Utilities and Housekeeping Services", "T": "Photographic, Mapping, Printing, and Publication Services",
    "U": "Education and Training Services", "V": "Transportation, Travel, and Relocation Services",
    "W": "Lease or Rental of Equipment", "X": "Lease or Rental of Facilities",
    "Y": "Construction of Structures and Facilities", "Z": "Maintenance, Repair, or Alteration of Real Property",
}
PRODUCTS = "Products and Equipment"
VALUE_FIELDS = ("sole_source_intent", "incumbent", "stated_value", "evaluation_basis")
EVAL_LABELS = {"lpta": "lowest price technically acceptable", "tradeoff": "best-value tradeoff",
               "highest_technically_rated": "highest technically rated", "lowest_price": "lowest price", "other": "other"}


# ── facts ─────────────────────────────────────────────────────────────────

def clean(s) -> str:
    return " ".join(str(s or "").split())


def group_of(r: dict) -> str | None:
    c = (r.get("psc") or "")[:1].upper()
    if not c:
        return None
    return PSC_GROUPS.get(c) or (PRODUCTS if c.isdigit() else None)


def notice_fact(nid: str, r: dict, track: str) -> dict:
    """One notice as the model sees it. Only published values: the tables reach here through digest.load,
    which drops held fields, and drop_held is applied again for safety."""
    r = {k: v for k, v in r.items() if k not in set(fields.held_columns(track, r))}
    f = {"id": nid, "kind": "sources-sought notice" if track == "sources_sought" else "solicitation",
         "title": clean(r.get("title")), "agency": clean(r.get("agency")),
         "sub_tier": clean(r.get("sub_tier")) if r.get("sub_tier") != r.get("agency") else None,
         "office": clean(r.get("office")), "kind_of_work": group_of(r),
         "responses_due": (r.get("response_deadline") or "")[:10] or None}
    if r.get("sole_source_intent"):
        f["plans_sole_source_award_to"] = clean(r["sole_source_intent"])
        f["sole_source_quote"] = clean(r.get("sole_source_intent_quote"))
    if r.get("incumbent_quote"):
        f["incumbent"] = " / ".join(x for x in (r.get("incumbent_firm"), r.get("incumbent_contract_number")) if x) or None
        f["incumbent_quote"] = clean(r["incumbent_quote"])
    if r.get("stated_value"):
        f["stated_value"] = clean(r["stated_value"])
        f["stated_value_covers"] = clean(r.get("stated_value_what")) or None
        f["stated_value_quote"] = clean(r.get("stated_value_quote"))
    if r.get("evaluation_basis"):
        f["evaluation"] = EVAL_LABELS.get(r["evaluation_basis"], r["evaluation_basis"])
        f["evaluation_quote"] = clean(r.get("evaluation_basis_quote"))
    desc = clean(r.get("description"))
    if desc:
        f["description_opening"] = desc if len(desc) <= DESC_CHARS else desc[:DESC_CHARS].rsplit(" ", 1)[0] + " ..."
    return {k: v for k, v in f.items() if v not in (None, "", [])}


def quotable(r: dict, track: str) -> dict[str, str]:
    """field -> text a quote about this notice may come from: title, full description, published quotes."""
    held = set(fields.held_columns(track, r))
    out = {"title": clean(r.get("title")), "description": clean(r.get("description"))}
    for k, v in r.items():
        if k in held or not v:
            continue
        if k.endswith("_quote"):
            out[k] = clean(v)
        elif k.endswith("_quotes"):
            for i, x in enumerate(list(v)):
                out[f"{k}[{i}]"] = clean(x)
    return {k: v for k, v in out.items() if v}


def _rank(week: str, nid: str) -> str:
    return hashlib.sha1(f"{week}:{nid}".encode()).hexdigest()


def facts(post: dict, sol: list[dict], ss: list[dict]) -> tuple[dict, dict]:
    """(facts for the model, id -> (row, track) for the checks). Deterministic for a given week."""
    week = post["week_start"]
    rows = [(r, "solicitation") for r in sol] + [(r, "sources_sought") for r in ss]
    by_id = {r["notice_id"]: (r, t) for r, t in rows}
    index: dict[str, tuple[dict, str]] = {}
    picked = []
    for i, p in enumerate(post.get("picks") or [], 1):
        nid = p["notice"]["notice_id"]
        if nid not in by_id:
            continue
        r, t = by_id[nid]
        index[f"P{i}"] = (r, t)
        picked.append({"headline_on_the_page": p["headline"], **notice_fact(f"P{i}", r, t)})
    counts: dict[str, int] = {}
    for r, _ in rows:
        g = group_of(r)
        if g and g != PRODUCTS:   # RFP Watch is about services; product codes appear only on sources-sought notices
            counts[g] = counts.get(g, 0) + 1
    top = sorted(counts, key=lambda g: (-counts[g], g))[:GROUPS_SHOWN]
    picked_ids = {p["notice"]["notice_id"] for p in post.get("picks") or []}
    groups = []
    n = 0
    for g in top:
        cands = [(r, t) for r, t in rows if group_of(r) == g and r["notice_id"] not in picked_ids
                 and len(clean(r.get("description"))) >= 150]
        cands.sort(key=lambda x: (not x[0].get("new_in_week"), _rank(week, x[0]["notice_id"])))
        ex = []
        for r, t in cands[:EXAMPLES_PER_GROUP]:
            n += 1
            index[f"N{n}"] = (r, t)
            ex.append(notice_fact(f"N{n}", r, t))
        groups.append({"kind_of_work": g, "example_notices": ex})
    f = {"week": {"from": week, "through": post["week_end"]},
         "picked_out_below": picked,
         "kinds_of_work_with_the_most_notices_this_week": groups}
    return f, index


def facts_text(f: dict) -> str:
    return json.dumps(f, indent=1, ensure_ascii=False)


# ── the call ──────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string"},
                       "sources": {"type": "array", "items": {
                           "type": "object",
                           "properties": {"notice": {"type": "string"}, "quote": {"type": "string"}},
                           "required": ["notice", "quote"], "additionalProperties": False}}},
        "required": ["text", "sources"], "additionalProperties": False}}},
    "required": ["sentences"], "additionalProperties": False,
}

PROMPT = """You write the opening paragraph of a weekly email about federal contracting notices posted on
SAM.gov (solicitations for services, and sources-sought notices, which are market research before a
solicitation). Readers: reporters covering federal contracting, congressional staff, government contracts
lawyers. Below the paragraph the email already has the picked-out notices, the planned sole-source awards and
the largest stated values, with counts computed by code.

Write three or four plain sentences that say what the government is buying this week: concrete specifics from
the notices, such as what the work is, where, and for which agency or office, drawn from the example notices
under kinds_of_work_with_the_most_notices_this_week. Good sentences sound like "The Army Corps of Engineers
is seeking firms to design the Phase II expansion of Puerto Quetzal in Guatemala" or "The U.S. Embassy in
Riga wants a contractor to cook and serve meals for the Marine Security Guards." At most one sentence may be
about a notice under picked_out_below. Pick the specifics a reader would find most informative, across
different kinds of work and agencies.

Rules:
- Use only what the input says about each notice. No outside knowledge about agencies, firms or programs.
- Every number, date, dollar figure and name you write must appear in the notices that sentence cites. Write
  dates as "December 8". Give a dollar figure only if it is that notice's stated_value.
- Do not count or compare quantities: no "most", "many", "several", "a number of", "majority", "largest",
  no totals. The page already gives the counts.
- Say "sole source", "incumbent" or "lowest price technically acceptable" only about a notice that has that
  value in the input.
- Don't mention the input, these instructions, or what a notice lacks. No adjectives of judgment
  (significant, major, notable, key, unusual), no advice to the reader, no exclamation marks, no Markdown.

For each sentence give its text and its sources: the id of every notice the sentence draws on (e.g. "N3",
"P1"), each with a quote of at least five words copied character for character from that notice's title,
description_opening or one of its *_quote values, that the sentence rests on.

Week:
{facts}
"""


def call_model(ftext: str, client=None) -> tuple[list[dict], dict]:
    import anthropic
    c = client or anthropic.Anthropic(default_headers={"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]}
                                      if os.environ.get("ANTHROPIC_WORKSPACE_ID") else None)
    r = c.messages.create(model=MODEL, max_tokens=4000,
                          output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                          messages=[{"role": "user", "content": PROMPT.format(facts=ftext)}])
    if r.stop_reason != "end_turn":
        raise RuntimeError(f"model stop_reason={r.stop_reason}")
    out = json.loads(next(b.text for b in r.content if b.type == "text"))["sentences"]
    pin, pout = PRICE_PER_MTOK.get(MODEL, (5.0, 25.0))
    usage = {"model": MODEL, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens,
             "cost_usd": round((r.usage.input_tokens * pin + r.usage.output_tokens * pout) / 1e6, 4)}
    return out, usage


# ── checks ────────────────────────────────────────────────────────────────

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ALLOWED_CAPS = {w.lower() for w in MONTHS + WEEKDAYS} | {
    "the", "a", "an", "this", "that", "these", "it", "its", "in", "on", "at", "for", "with", "and", "also",
    "separately", "elsewhere", "meanwhile", "another", "one", "two", "three", "both", "each", "among", "by", "from",
    "u", "s", "us", "federal", "government", "department", "agency", "office", "district", "service", "services",
    "administration", "solicitation", "solicitations", "sources", "sought", "notice", "notices", "rfi", "rfq", "rfp",
    "sam", "gov", "psc", "lpta", "contract", "contracts", "contractor", "contractors", "firm", "firms", "under", "as",
    "if", "while", "after", "before", "to", "of", "through", "via"}
QUANTITY = re.compile(r"\b(most|many|several|numerous|majority|largest|biggest|dozens?|handful|a number of|"
                      r"half|plurality|bulk|count(?:s|ed)?|total(?:s|ed|ing)?|percent|%)\b", re.I)
HYPE = re.compile(r"\b(significant(?:ly)?|major|notabl[ey]|key|landmark|sweeping|historic|dramatic(?:ally)?|"
                  r"crucial|critical|important(?:ly)?|striking(?:ly)?|unprecedented|controversial|unusual(?:ly)?|"
                  r"remarkabl[ey]|massive|huge|game-?changer|signals?|poised|interesting(?:ly)?)\b", re.I)
ADVICE = re.compile(r"\b(you|your|readers?|subscribers?|should|worth (?:watching|noting|following)|keep an eye|"
                    r"stay tuned|watch for|look out)\b", re.I)
META = re.compile(r"\b(input|facts?|provided|example notices?|the data|in the data|listed below|below|above|"
                  r"does not (?:say|state|specify|describe)|not (?:given|specified|stated)|unclear|unspecified)\b", re.I)
_MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(billion|million|thousand|[BMK])?\b", re.I)
_MULT = {"b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3}


def norm(s) -> str:
    s = str(s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().casefold()


def dollars(text: str) -> list[tuple[float, float]]:
    out = []
    for num, unit in _MONEY.findall(text or ""):
        mult = _MULT.get((unit or "").lower(), 1.0)
        v = float(num.replace(",", "")) * mult
        dec = len(num.split(".")[1]) if "." in num else 0
        out.append((v, max(0.5 * mult / 10 ** dec, 0.01 * v)))
    return out


def numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")}


def _iso_dates(text: str) -> set[tuple[int, int, int]]:
    return {(int(y), int(m), int(d)) for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", text)}


def sentence_problems(s: dict, f: dict, index: dict) -> list[str]:
    """Every reason one sentence can't be used."""
    text = " ".join(str(s.get("text") or "").split())
    label = f"sentence {text[:50]!r}"
    out = []
    if not text:
        return [f"{label}: empty"]
    cited = []
    for src in s.get("sources") or []:
        nid = str(src.get("notice") or "").strip()
        if nid not in index:
            out.append(f"{label}: cites an unknown notice {nid!r}")
            continue
        r, t = index[nid]
        q = norm(src.get("quote")).strip(' ."\'…')
        hits = [k for k, v in quotable(r, t).items() if q and q in norm(v)]
        if len(q) < 20:
            out.append(f"{label}: quote for {nid} too short: {src.get('quote')!r}")
        elif not hits:
            out.append(f"{label}: quote not in {nid}'s title, description or checked quotes: {src.get('quote')!r}")
        else:
            cited.append((nid, r, t))
    if not cited:
        out.append(f"{label}: no checked source")
        return out
    # what this sentence may draw names and numbers from: its cited notices, the week, the group names
    ctx = " \n ".join(facts_text(notice_fact(nid, r, t)) + " " + " ".join(quotable(r, t).values())
                      for nid, r, t in cited)
    ctx += " \n " + f["week"]["from"] + " " + f["week"]["through"] + " " + " ".join(PSC_GROUPS.values()) + " " + PRODUCTS
    if "!" in text:
        out.append(f"{label}: exclamation mark")
    for name, rx in (("counting word", QUANTITY), ("hype word", HYPE), ("advice or reader address", ADVICE),
                     ("prose about the input", META)):
        if m := rx.search(text):
            out.append(f"{label}: {name}: {m.group(0)!r}")
    no_money = _MONEY.sub(" ", text)
    if extra := numbers(no_money) - numbers(ctx):
        out.append(f"{label}: numbers not in its cited notices: {sorted(extra)}")
    allowed_d = []
    for _, r, _t in cited:
        allowed_d += [v for v, _ in dollars(r.get("stated_value_quote") or "")] + [v for v, _ in dollars(r.get("stated_value") or "")]
    for v, tol in dollars(text):
        if not any(abs(v - a) <= tol for a in allowed_d):
            out.append(f"{label}: dollar figure {v:,.0f} is not a cited notice's stated value")
    dates = _iso_dates(ctx)
    for m in re.finditer(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", text):
        mon, day, yr = MONTHS.index(m.group(1)) + 1, int(m.group(2)), m.group(3)
        if not any(mm == mon and dd == day and (not yr or yy == int(yr)) for yy, mm, dd in dates):
            out.append(f"{label}: date not in its cited notices: {m.group(0)!r}")
    words = {w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z'’]*", ctx)}
    for w in re.findall(r"[A-Za-z][A-Za-z'’]*", text):
        w2 = re.sub(r"['’]s$", "", w)
        if w2[0].isupper() and w2.casefold() not in words and w2.casefold() not in ALLOWED_CAPS:
            out.append(f"{label}: word not in its cited notices: {w2!r}")
    if re.search(r"sole[\s-]source", text, re.I) and not any(r.get("sole_source_intent") for _, r, _t in cited):
        out.append(f"{label}: says sole source but no cited notice has a sole-source value")
    if re.search(r"\bincumbent\b", text, re.I) and not any(
            r.get("incumbent_quote") and fields.published(t, "incumbent") for _, r, t in cited):
        out.append(f"{label}: says incumbent but no cited notice has a published incumbent value")
    if re.search(r"\bLPTA\b|lowest[\s-]price[\s,-]+technically", text, re.I) and not any(
            r.get("evaluation_basis") == "lpta" for _, r, _t in cited):
        out.append(f"{label}: says LPTA but no cited notice's evaluation basis is lpta")
    return out


def problems(sentences: list[dict], f: dict, index: dict) -> list[str]:
    out = []
    if not sentences:
        return ["no sentences"]
    if len(sentences) > 5:
        out.append(f"too long ({len(sentences)} sentences)")
    words = sum(len(str(s.get("text") or "").split()) for s in sentences)
    if words > 130:
        out.append(f"too long ({words} words)")
    for s in sentences:
        out += sentence_problems(s, f, index)
    return out


def write(post: dict, sol: list[dict], ss: list[dict], client=None) -> dict:
    """{"status": kept|dropped|error|skipped, "text", "sources", "problems", "usage"}. Never raises: any failure
    means the post and email go without an opening."""
    f, index = facts(post, sol, ss)
    if not index:
        return {"status": "skipped", "text": None, "sources": [], "problems": ["no notices"], "usage": {}}
    try:
        sentences, usage = call_model(facts_text(f), client)
    except Exception as exc:  # noqa: BLE001 - any failure means no opening
        return {"status": "error", "text": None, "sources": [], "problems": [f"{type(exc).__name__}: {exc}"], "usage": {}}
    p = problems(sentences, f, index)
    text = " ".join(" ".join(str(s.get("text") or "").split()) for s in sentences)
    sources = []
    for s in sentences:
        for src in s.get("sources") or []:
            if src.get("notice") in index:
                r, t = index[src["notice"]]
                q = norm(src.get("quote")).strip(' ."\'…')
                sources.append({"notice_id": r["notice_id"], "track": t, "title": r.get("title"),
                                "sam_link": r.get("sam_link"), "quote": src.get("quote"),
                                "found_in": [k for k, v in quotable(r, t).items() if q and q in norm(v)]})
    return {"status": "kept" if not p else "dropped", "text": text, "sources": sources, "problems": p, "usage": usage}


def scrub(o: dict | None) -> dict | None:
    """The opening, or None if it rests on a field held from publishing (fields.HELD): a quote found only in a
    held field's columns, or "incumbent" said about a track whose incumbent is held. Openings written before a
    field was held may still carry it; site.py and email.py read posts through digest.scrub_post, which calls this."""
    if not o:
        return o
    for src in o.get("sources") or []:
        t = src.get("track")
        found = [k.split("[")[0] for k in src.get("found_in") or []]
        if found and all(fields.held_columns(t, [k]) for k in found):
            return None
    if re.search(r"\bincumbent\b", o.get("text") or "", re.I) and any(
            not fields.published(src.get("track"), "incumbent") for src in o.get("sources") or []):
        return None
    return o


def kept_text(post: dict) -> str | None:
    o = post.get("opening") or {}
    return o.get("text") if o.get("status") == "kept" and o.get("text") else None
