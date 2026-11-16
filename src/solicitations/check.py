"""Check every answer: each quote must be in the package, and each value must pass its field's rule.

    uv run python -m solicitations.check work/week.csv work/answers.jsonl --out work/checked.jsonl

Writes one record per notice: the kept values (each with its quote and the pages it is on) and every
dropped value with the reason. Quotes are checked against the whole package, not the excerpt sent.

A quote found on a PDF page must also be in the second reading of the same page(s) (fetch.py reads every
PDF with both pypdf and pdftotext). If it is not, the value is dropped: "quote not in second extraction".
"""
import argparse
import json
import re
import sys
from pathlib import Path

from federal_ja.analysis.text import locate_quote, normalize

from . import fields, prepare


class NoSecondReading(RuntimeError):
    """A quote is on a PDF page whose second reading was never recorded. Never skipped: fetch again."""


def _letters(s: str | None) -> str:
    return re.sub(r"[\s-]+", "", normalize(s)).casefold()


def second_check(doc, quote: str | None, qm) -> str | None:
    """None if the quote is also in the second reading of the page(s) it was found on, else why not.

    pypdf read the NONSKID notice's page 63 (week of 2026-11-09) with lines out of order, and a quote spliced
    from two sentences ("...the offeror being significantly more important than price accedes to all
    solicitation requirements") passed the quote check; pdftotext reads that page in order. The quote and the
    page are normalized as for locate_quote and compared ignoring case (as locate_quote is called here) and
    ignoring spaces and hyphens: the two readers put kerning spaces in different places ("NA VOCEANO" /
    "NAVOCEANO") and pdftotext joins words hyphenated at a line end ("non-price" / "nonprice"). Neither changes
    the order of the letters, which is what this check is for. Both were seen in the 2026-11-09 samples.

    Measured on the 218 sampled values of that week, documents fetched again: 142 quotes are on PDF pages (2 of
    them XFA forms, read once); 4 fail this check. One is the NONSKID splice. Three are right values on form or
    table pages that pdftotext reads column by column (label column, then value column): eb7eba86 "Contract Type:
    Firm-Fixed Price", 6cf3c02b "Incumbent Contractor Name: Dawson Enterprises LLC", d5e14398 DD Form 254 block 4.
    That is the cost of the check: a quote that only one reader can assemble is dropped."""
    second = getattr(doc, "second", None) or {}
    pdf = [p for p in qm.pages if p.get("text_source") == "native" and p["resource_id"] in second]
    if not pdf:
        return None   # SAM.gov description, Word or text file: one reading only
    counts = {}
    for p in doc.pages:
        counts[p.resource_id] = counts.get(p.resource_id, 0) + 1
    texts = []
    for p in qm.pages:
        s = second.get(p["resource_id"]) if p.get("text_source") == "native" else None
        if s is None:
            texts += [x.text for x in doc.pages if x.resource_id == p["resource_id"] and x.page_number == p["page_number"]]
            continue
        if "missing" in s:
            raise NoSecondReading(f"{doc.notice_id}: {p['resource_id']} has no second reading; fetch it again")
        if "skipped" in s:
            texts += [x.text for x in doc.pages if x.resource_id == p["resource_id"] and x.page_number == p["page_number"]]
            continue
        if "error" in s:
            return "no second extraction of the file"
        if len(s["pages"]) != counts[p["resource_id"]]:
            return "second extraction has a different page count"
        texts.append(s["pages"][p["page_number"] - 1] or "")
    if _letters(quote) not in _letters("\n\n".join(texts)):
        return "quote not in second extraction"
    return None


def verify(quote, doc, nd, min_words: int = 5):
    """(QuoteMatch, why-not): the quote is in the package and, on PDF pages, in the second reading too."""
    qm = locate_quote(quote, doc, nd, ignore_case=True, min_words=min_words)
    if not qm.verified:
        return qm, "quote not found"
    return qm, second_check(doc, quote, qm)


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
                    qm, why = verify(quote, doc, nd)
                    if not why:
                        out.append({**item, "pages": qm.pages, "match": qm.match, "source": "rule"})
                break
    return out[:3]


# ---- evaluation basis by rule ----------------------------------------------------------------------------
# On the week of 2026-11-09, 8 of 10 sampled solicitations with no evaluation_basis plainly stated one. The
# wording below is from those sentences (docs: grading of GitHub run 43883487862):
#   "This acquisition will utilize Lowest Price Technically Acceptable (LPTA) source selection procedures ..."
#   "Award will be made to the responsible Offeror submitting the lowest-priced offer that receives an
#    Acceptable rating for both Technical Capability and Past Performance."
#   "BSA will make award to responsive and responsible Offerors who submit the lowest total prices and who meet
#    all of BSA's technical qualifiers ..."
#   "The award will be made to the Contractor determined to have the Highest Technically Ranked Offer (HTRO) ..."
#   "... award to the responsible offeror whose proposal ... is determined to be the most advantageous to the
#    Government, past performance and price considered."  /  "... most advantageous to the Government, price and
#    other factors considered."
#   "The Contracting Officer reserves the right to make award to ... based on a rational tradeoff between
#    evaluation factors other than cost or price and cost or price."
# A sentence counts only if it says what WILL happen in this award (will/shall/reserves the right, with award,
# evaluate or source selection), and not if it defines a method, lists more than one, or is conditional.
# Measured 2026-11-16, no model calls, on the 161 services solicitations in that week's check samples, their
# documents fetched again with fetch.py (pypdf first, as in the run):
# - the 10 sampled blanks: filled 8 of the 8 the grader marked missed, each with the grader's value (X-ray lease
#   16c3a84f was a "moderate confidence" tradeoff); the 2 correctly blank stayed blank.
# - the 30 sampled model values: the rule gives a value for 17, the same value as the model's in all 17.
# - the other 121 notices: 57 hits, each quote read by hand, and for the 21 that rest on 52.212-2(a) alone the
#   package searched for other evaluation wording. 51 right; 6 not confirmed (4 with only 52.212-2(a) and nothing
#   else on evaluation; 7cea9a6c "considered during the technical evaluation and trade-off"; f3f61661 "the
#   Government anticipates using Best Value Tradeoff"). Two wrong hits found on the way were fixed and are tests:
#   a Q&A question listing the methods (3acce4e5) and 52.212-2(a) next to "offers the lowest price" (49ba0cfa).
_BASIS_TERMS = {
    "lpta": re.compile(r"lowest[\s,/-]+price[ds]?[\s,/-]+(?:and\s+)?technically[\s-]+(?:acceptable|qualified)|\bLPTA\b|"
                       r"lowest[\s-]+(?:total[\s-]+)?(?:evaluated[\s-]+)?price[ds]?\b[^.]{0,120}?"
                       r"(?:\bacceptable\s+rating|technical\s+qualifiers|technically\s+acceptable)", re.I),
    "tradeoff": re.compile(r"\btrade[\s-]?offs?\b|most\s+advantageous\s+to\s+the\s+Government,?\s+(?:[\w ,]{0,40}?\s)?"
                           r"(?:price|cost)(?:\s+and\s+other\s+factors)?\s+considered", re.I),
    "highest_technically_rated": re.compile(r"highest[\s-]+technically[\s-]+(?:rated|ranked)", re.I),
}
_BASIS_SAYS = re.compile(r"\b(?:will|shall)\b|reserves\s+the\s+right", re.I)
_BASIS_ABOUT = re.compile(r"\baward|\bevaluat|source\s+selection|\bselect|"
                          r"\bissue\s+(?:a|an|the)\s+(?:purchase\s+order|contract|order|task\s+order)", re.I)
_BASIS_NOT = re.compile(r"\bmeans\b|\bdefin|is\s+appropriate|\b15\.101|\bif\b|\bunless\b|\bthen\b|"
                        r"in\s+(?:the|such\s+an?)\s+event|"
                        r"\bmay\s+(?:use|utilize|employ|select|be\s+(?:used|utilized|evaluated))|\[insert|\be\.g\.|"
                        r"\b(?:no|not|without)\b[^.]{0,30}trade[\s-]?offs?|trade[\s-]?offs?\s+(?:will|shall)\s+not", re.I)
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])")
# where a sentence starts when no full stop comes before it (after a heading run together with the text)
_SUBJECT = re.compile(r"\b(?:This|The|Award|Awards|Offers?|Quotes?|Quotations?|Proposals?)\b")
LOOK = 400
MAX_BASIS_SENTENCE = 700
# award on price alone among acceptable offers: never filled by rule, but a tradeoff or highest-technically-rated
# value is not filled when the package also says this (a 52.212-2 printed whole in a lowest-price package)
_LOWEST_SAYS = re.compile(r"(?<!other than )\b(?:to|on)\s+the\s+lowest[\s-]+(?:total[\s-]+)?(?:evaluated[\s-]+)?"
                          r"(?:price[ds]?|bid(?:der)?|offer(?:or)?|quot\w*|cost)\b(?![\s,-]+technically)|"
                          r"\bprice\s+(?:alone|only)\b|\bsealed[\s-]+bid|\boffers?\s+the\s+lowest[\s-]+(?:total[\s-]+)?price\b", re.I)


def _sentence(nd: str, a: int, b: int) -> str | None:
    """The sentence around nd[a:b], or None when its ends can't be found within LOOK characters."""
    head = nd[max(0, a - LOOK): a]
    starts = [m.end() for m in _SENT_END.finditer(head)] or [m.start() for m in _SUBJECT.finditer(head)]
    if not starts and a > LOOK:
        return None
    start = (a - len(head)) + (starts[-1] if starts else 0)
    tail = _SENT_END.search(nd, b, min(len(nd), b + MAX_BASIS_SENTENCE))
    if not tail and b + MAX_BASIS_SENTENCE < len(nd):
        return None
    return nd[start: tail.start() if tail else len(nd)].strip()


def basis_sentences(nd: str) -> list[tuple[str, str]]:
    """(value, sentence) for each sentence in the normalized package that states one evaluation basis."""
    out, seen = [], set()
    for value, rx in _BASIS_TERMS.items():
        for m in rx.finditer(nd):
            sent = _sentence(nd, m.start(), m.end())
            if not sent or sent in seen or len(sent) > MAX_BASIS_SENTENCE:
                continue
            seen.add(sent)
            kinds = {v for v, r in _BASIS_TERMS.items() if r.search(sent)}
            # a question ("Will quotations be evaluated on a Price Only, LPTA, or Best Value basis?") states nothing
            if len(kinds) != 1 or not _BASIS_SAYS.search(sent) or not _BASIS_ABOUT.search(sent) or _BASIS_NOT.search(sent) \
                    or sent.rstrip().endswith("?"):
                continue
            out.append((value, sent))
    return out


def _lowest_award(s: str) -> bool:
    """A sentence saying award will be on lowest price ("... intends to award ... based on price only"), not a
    clause condition ("if this contract was awarded using other than sealed bid procedures") or a caution
    ("award may not necessarily be made to the lowest priced offer")."""
    return bool(re.search(r"\baward", s, re.I) and re.search(r"\b(?:will|shall|intends?\s+to)\b", s, re.I)
                and not _BASIS_NOT.search(s) and not re.search(r"\bnot\b|other\s+than", s, re.I))


# FAR 52.212-2(a) as printed in most commercial solicitations, LPTA ones included: weaker than a sentence
# naming the method, so it does not count against one
_BOILERPLATE = re.compile(r"most\s+advantageous\s+to\s+the\s+Government,?\s+price\s+and\s+other\s+factors\s+considered", re.I)


def evaluation_basis_by_rule(doc, nd) -> dict | None:
    """The evaluation basis when every stating sentence in the package gives the same one, with the first such
    sentence (in document order) that passes the quote checks as its quote; None when there is none, or they
    disagree, or a tradeoff package also has LPTA wording anywhere, or a tradeoff or highest-technically-rated
    package also says award is on lowest price. The 52.212-2(a) sentence alone ("most advantageous ..., price
    and other factors considered") gives way to an LPTA or highest-technically-rated sentence. Used only when
    the model left the field empty."""
    found = basis_sentences(nd)
    if any(v != "tradeoff" for v, _ in found):
        found = [(v, s) for v, s in found
                 if not (v == "tradeoff" and _BOILERPLATE.search(s) and not re.search(r"trade[\s-]?off", s, re.I))]
    values = {v for v, _ in found}
    if len(values) != 1:
        return None
    value = values.pop()
    if value == "tradeoff" and fields._LPTA.search(nd.translate(fields._HYPHENS)):
        return None
    if value != "lpta" and any(_lowest_award(_sentence(nd, m.start(), m.end()) or "") for m in _LOWEST_SAYS.finditer(nd)):
        return None
    for quote in sorted((s for _, s in found), key=nd.find):
        item = {"value": value, "quote": quote}
        if fields.rule("evaluation_basis", item):
            continue
        qm, why = verify(quote, doc, nd)
        if not why:
            return {**item, "pages": qm.pages, "match": qm.match, "source": "rule"}
    return None


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
            qm, why = verify(it.get("quote"), doc, nd, min_words=fields.MIN_WORDS.get(name, 5))
            if why:
                dropped.append({"field": name, "value": it.get("value"), "quote": it.get("quote"), "why": why})
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
    # only when the model gave no basis at all; a model value that failed its check is not replaced
    if profile == "solicitation" and not kept.get("evaluation_basis") and \
            (answer.get("evaluation_basis") or {}).get("value") in (None, "", "not_stated"):
        kept["evaluation_basis"] = evaluation_basis_by_rule(doc, nd)
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
