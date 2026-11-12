"""The fields asked for, their written definitions, the tool schema, and the rule each value must pass.

Every field is "what the document states", with the document's own sentence as `quote`. Definitions
name the traps that sank J&A fields (docs/checks/main_fields in federal-ja-documents): a requested
date is not a deadline, an option clause is not a cap, one mention is not a count, a category the
model infers is not a statement. Each value must also pass a rule in code against its own quote
(`RULES`), so a value its sentence doesn't support is dropped, not published.
"""
import re

PROMPT_VERSION = "2026-11-11.sol2"

# name -> (kind, definition). kind: "quoted" = {value, quote}; "list" = [{value, quote}]; "text" = model-written.
FIELDS = {
    "notice_kind": ("enum", "What this posting is, from the documents: rfp (request for proposals), rfq (request "
                    "for quotes), rfi_or_sources_sought (market research; no award will be made from it), "
                    "presolicitation (announces a coming solicitation), event (industry day, site visit or "
                    "conference notice only), other."),
    "requirement_model_written": ("text", "One plain sentence: what the government wants bought or done, and where. "
                                  "Model-written; not checked word for word."),
    "evaluation_basis": ("quoted", "How offers will be judged, ONLY as the document states it for this "
                         "acquisition: lpta (lowest price technically acceptable), tradeoff (best value with "
                         "non-price factors that can outweigh price), highest_technically_rated (highest "
                         "technically rated offeror(s) with a fair and reasonable price), lowest_price (award on "
                         "price alone among acceptable or responsive offers, with no technical tradeoff), other. "
                         "The quote must be the sentence that states it. NOT a clause title alone (52.212-2 "
                         "printed without its filled-in factors), NOT a description of FAR in general, NOT a "
                         "different acquisition. Empty if not stated."),
    "evaluation_factors": ("list", "Each evaluation factor the document lists for this acquisition (e.g. "
                           "Technical Approach, Past Performance, Price), as named, in the order listed, each "
                           "with the sentence or line naming it. Subfactors are not separate factors. Empty if "
                           "none are listed."),
    "relative_importance": ("quoted", "The sentence stating how the factors compare in importance (e.g. 'all "
                            "non-price factors, when combined, are significantly more important than price'). "
                            "value = that statement in a few words. Empty if no such sentence."),
    "contract_type": ("list", "Each contract type the document says THIS award will be, one of: "
                      "firm-fixed-price, fixed-price-incentive, fixed-price with economic price adjustment, "
                      "time-and-materials, labor-hour, cost-plus-fixed-fee, cost-plus-incentive-fee, "
                      "cost-plus-award-fee, cost-reimbursement, indefinite-delivery/indefinite-quantity, requirements, "
                      "blanket purchase agreement, other transaction agreement. value = that type, written as in this "
                      "list; the quote must name it. NOT clause titles in a clause list, NOT the type of some other "
                      "contract, NOT a purchase order, lease or personal services contract (those are not contract "
                      "types here)."),
    "award_count": ("quoted", "single or multiple: whether the document says one award or more than one will "
                    "be made; add the stated number if given ('up to five awards' -> multiple, 5). NOT a reservation "
                    "of the right to make multiple awards by itself ('the Government reserves the right to make "
                    "multiple awards' is not a statement that it will). Standard wording such as 'The Government will award a "
                    "contract resulting from this solicitation to the responsible offeror ...' is NOT a statement of a "
                    "single award: use single only for explicit wording ('one award', 'a single award', 'one (1) "
                    "contract', a sole source to one firm)."),
    "period_of_performance": ("quoted", "The period of performance as written for this award (e.g. 'one 12-month "
                              "base period and four 12-month option periods'), value = that text in a few "
                              "words. NOT the FAR 52.217-8 six-month extension option by itself, NOT a delivery "
                              "date."),
    "stated_value": ("quoted", "A dollar estimate or ceiling the document states for THIS acquisition, value = "
                     "the amount exactly as written, including a range ('Between $27M and $33M'), any qualifier ('exceeds', "
                     "'up to') and a currency other than US dollars ('AUD 400,000'), with what it is in "
                     "`what` ('estimated value', 'ceiling', 'magnitude'). NOT a plug or placeholder amount given only "
                     "for pricing or evaluation, "
                     "NOT a size standard, a bond amount, a wage rate, a past contract's value or a threshold "
                     "quoted from FAR."),
    "incumbent": ("quoted", "A current or previous contractor for this work, or the current contract's number, "
                  "that the document names as such ('currently performed by X', 'the incumbent contractor is X', "
                  "'follow-on to contract N'). NOT a manufacturer whose product is required, NOT a firm merely "
                  "listed as a source."),
    "clearance": ("quoted", "The highest security clearance the document says contractor personnel or the "
                  "facility must hold: confidential, secret, top_secret, ts_sci, or public_trust (a background "
                  "investigation that is not a clearance). NOT a DD-254 clause listed without a level."),
    "cmmc_level": ("quoted", "The Cybersecurity Maturity Model Certification level the document requires "
                   "(1, 2 or 3), as a number. NOT a clause title alone without a level."),
    "brand_name": ("quoted", "A brand name or brand-name-or-equal requirement: value = the brand and item as "
                   "written. NOT a part number list without a brand requirement."),
    "oral_presentation": ("quoted", "yes, only when the document says offerors will (or may be invited to) give "
                          "oral presentations or interviews as part of evaluation."),
    "page_limit": ("quoted", "The page limit on the technical proposal or volume, or on the whole response when one "
                   "limit covers it (an RFI or sources-sought capability statement counts), as a number of pages, "
                   "with what it covers in `what` ('technical volume', 'whole proposal', 'capability statement'). "
                   "NOT the limit for only the past performance or price volume."),
    "set_aside_stated": ("quoted", "The set-aside the documents state for this acquisition, as written (e.g. "
                         "'total small business set-aside', '8(a) sole source', 'SDVOSB', 'full and open "
                         "competition'). NOT a size standard alone."),
    "planned_strategy": ("quoted", "For market research only (rfi_or_sources_sought, presolicitation): what the "
                         "notice says the agency plans or is considering: small_business_set_aside, "
                         "full_and_open, sole_source (name the firm in `firm`), or undecided. NOT a generic "
                         "request for small businesses to respond, unless it says a set-aside is planned or "
                         "being considered. Standard wording that the decision is the contracting officer's "
                         "discretion, or that a set-aside will be considered 'if any' or based on responses, is "
                         "undecided."),
    "sole_source_intent": ("quoted", "Only when the notice says the agency intends to award, or is considering "
                           "awarding, THIS requirement to one named firm without competition (a sole source, "
                           "'only one responsible source', 'intends to negotiate with X'): value = that firm's name "
                           "as written. NOT a brand-name requirement that any vendor could supply, NOT a firm named "
                           "only as a manufacturer or a current contractor, NOT a general description of sole-source "
                           "rules."),
    "set_aside_considered": ("quoted", "Only when the notice says a set-aside is planned or being considered for THIS "
                             "requirement: value = the set-aside as written ('total small business set-aside', "
                             "'SDVOSB set-aside', '8(a)', 'HUBZone', 'WOSB'). NOT the contracting officer's "
                             "discretion or 'if any' boilerplate, NOT a general policy paragraph (e.g. the Buy Indian "
                             "Act or VA's Veterans First rule stated as policy), NOT only a request that small "
                             "businesses respond, NOT the SAM.gov set-aside field."),
    "notable_model_written": ("text", "Usually empty. Only something specific a reporter covering federal contracting would find unusual in "
                              "these documents (a very short response window for the scope, leftover text from "
                              "another acquisition, a requirement only one firm could meet), in one or two "
                              "sentences; empty if nothing. Model-written; reviewed separately."),
}

# Two narrower tracks (2026-11-11, after the pilot): each gets its own short prompt with only its fields.
# Chosen for what reporters, Hill staffers and lawyers use; rare or loose fields were dropped.
PROFILES = {
    # svc2: same prompt, new excerpt (evaluation text first). svc1 answers stand for packages that were
    # never cut, since their input is unchanged.
    "solicitation": {"version": "2026-11-11.svc2", "accept_uncut": ["2026-11-11.svc1"],
                     "fields": ["requirement_model_written", "evaluation_basis", "evaluation_factors",
                                "relative_importance", "incumbent", "contract_type", "stated_value"]},
    # ss2: planned_strategy (22 of 30 in its check) replaced by two narrow statements.
    "sources_sought": {"version": "2026-11-11.ss2", "accept_uncut": [],
                       # set_aside_considered was dropped after its check (6 of 30 right): nearly every
                       # notice has "responses will determine whether ... competitive or set aside"
                       # boilerplate, and SAM.gov's own set-aside field is published instead.
                       "fields": ["requirement_model_written", "sole_source_intent", "incumbent", "stated_value"]},
}


def profile_fields(profile: str) -> dict:
    return {k: FIELDS[k] for k in PROFILES[profile]["fields"]}


ENUMS = {
    "notice_kind": ["rfp", "rfq", "rfi_or_sources_sought", "presolicitation", "event", "other"],
    "evaluation_basis": ["lpta", "tradeoff", "highest_technically_rated", "lowest_price", "other"],
    "award_count": ["single", "multiple"],
    "clearance": ["confidential", "secret", "top_secret", "ts_sci", "public_trust"],
    "oral_presentation": ["yes"],
    "planned_strategy": ["small_business_set_aside", "full_and_open", "sole_source", "undecided"],
}
NUMERIC = {"cmmc_level", "page_limit"}
# A factor is often quoted by its heading ("Factor 1: Technical"), shorter than the 5-word default.
MIN_WORDS = {"evaluation_factors": 3}


def _quoted(name):
    """Every part is a plain string ("" = not stated); numbers are written as digits and parsed in check.
    The API's strict mode allows at most 16 nullable parameters, so nothing here is nullable."""
    v = {"type": "string", "enum": ENUMS[name] + ["not_stated"]} if name in ENUMS else {"type": "string"}
    props = {"value": v, "quote": {"type": "string"}}
    if name in ("stated_value", "page_limit"):
        props["what"] = {"type": "string"}
    if name == "award_count":
        props["max_awards"] = {"type": "string"}
    if name == "incumbent":
        props["contract_number"] = {"type": "string"}
    if name == "planned_strategy":
        props["firm"] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def schema(profile: str | None = None) -> dict:
    props = {}
    for name, (kind, _) in (profile_fields(profile) if profile else FIELDS).items():
        if kind == "enum":
            props[name] = {"type": "string", "enum": ENUMS[name]}
        elif kind == "text":
            props[name] = {"type": "string"}
        elif kind == "list":
            props[name] = {"type": "array", "items": {"type": "object", "properties": {
                "value": {"type": "string"}, "quote": {"type": "string"}},
                "required": ["value", "quote"], "additionalProperties": False}}
        else:
            props[name] = _quoted(name)
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def to_number(v):
    """A number the model wrote as text ("$2,500,000", "25"), or None."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


INSTRUCTIONS = """You are reading a federal contracting notice posted on SAM.gov and its attachments: a solicitation,
request for quotes, request for information, sources-sought notice, presolicitation or special notice.
Record what the documents STATE about the fields below, using the record tool once.

Rules for every field:
- Only what the documents state for THIS acquisition. If they don't state it, use null (or an empty list).
  Never infer, estimate, count or guess. A clause title in a list of clauses states nothing by itself.
- `quote` is copied exactly from the text after DOCUMENTS:, never from the SAM.gov lines above it: one passage of 1-2 sentences or one line, at least 5
  words, containing the value. No ellipses, no stitching passages together, no paraphrase, no headers you
  added. If you can't quote it, the value is null.
- The excerpts may be partial: text marked [...] was left out. Don't assume what it said.
- When the documents don't state a field: "not_stated" for a field with a fixed list of values, otherwise an
  empty string "" for the value and the quote (and an empty list for lists). Write numbers as digits only
  (e.g. "2500000", "25").

Fields:
"""


def instructions(profile: str | None = None) -> str:
    f = profile_fields(profile) if profile else FIELDS
    return INSTRUCTIONS + "\n".join(f"- {name}: {d}" for name, (_, d) in f.items())


# ---- rules each value must pass against its own quote -------------------------------------------
_LPTA = re.compile(r"lowest[\s,/-]+price(?:d)?[\s,/-]+(?:and[\s]+)?technically[\s-]+acceptable|\bLPTA\b|"
                   r"low[\s-]+priced?[\s,]+technically[\s-]+acceptable|"
                   r"lowest[\s-]+(?:total[\s-]+)?(?:evaluated[\s-]+)?price.{0,60}?(?:technical(?:ly)?[\s-]+(?:acceptab|requirement)|technical[\s-]+acceptability)|"
                   r"technically[\s-]+acceptable.{0,100}?(?:lowest|low[\s-]+price)|"
                   r"lowest[\s-]+(?:total[\s-]+)?(?:evaluated[\s-]+)?price.{0,60}?non-?price[\s-]+factors?.{0,40}?acceptable", re.I | re.S)
_TRADEOFF = re.compile(r"trade[\s-]?off|best[\s-]+(?:overall[\s-]+)?value|more important than (?:cost or )?price|"
                       r"price and other factors considered|other than (?:the )?lowest[\s-]+price|equally[\s-]+weighted|"
                       r"most advantageous.{0,80}?(?:non-?price|price and (?:other|non-?price)|other factors|evaluation factors)|"
                       r"(?:significantly|approximately) (?:more|less) important|equal to price|weigh(?:ed|ted)? more heavily", re.I | re.S)
_HTRO = re.compile(r"highest[\s-]+technically[\s-]+rated|highest[\s-]+rated", re.I)
_LOWEST = re.compile(r"lowest[\s-]+(?:total[\s-]+)?(?:evaluated[\s-]+)?(?:responsive|responsible|price|priced|bid|bidder|offer|cost|evaluation total)|"
                     r"price[\s-]+alone|price[\s-]+only|(?:based|basis)\s+(?:solely\s+|only\s+)?(?:up)?on\s+(?:the\s+)?price|"
                     r"sealed[\s-]+bid|low(?:est)?[\s-]+bid|low[\s-]+price|only price and price[\s-]+related", re.I)
_HYPHENS = str.maketrans({c: "-" for c in "‐‑‒–—―−"})
_CLEAR = {"confidential": r"confidential", "secret": r"\bsecret\b", "top_secret": r"top[\s-]+secret",
          "ts_sci": r"\bSCI\b|sensitive[\s-]+compartmented", "public_trust": r"public[\s-]+trust|background[\s-]+investigation|suitability"}
_CTYPES = [r"firm[\s-]*fixed[\s-]*price|\bFFP\b", r"fixed[\s-]*price[\s-]*incentive|\bFPI", r"economic price adjustment|\bEPA\b",
           r"time[\s-]*(?:and|&)[\s-]*materials?|\bT&M\b", r"labor[\s-]*hours?", r"cost[\s-]*plus[\s-]*fixed[\s-]*fee|\bCPFF\b",
           r"cost[\s-]*plus[\s-]*incentive|\bCPIF\b", r"cost[\s-]*plus[\s-]*award|\bCPAF\b", r"cost[\s-]*reimburse",
           r"indefinite[\s-]*delivery|indefinite[\s-]*quantity|\bIDIQ\b|\bID/IQ\b", r"\brequirements\s+(?:type\s+)?contract|^requirements$",
           r"blanket[\s-]*purchase|\bBPA\b", r"other[\s-]*transaction|\bOTA?\b"]
_WORDNUM = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                       "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty "
                                       "twenty-five thirty forty fifty sixty".split())}
_WORDNUM.update({"twenty-five": 25, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60})


def numbers_in(text: str) -> set[float]:
    """Numbers written in the text, as digits (commas and $ allowed, 'million'/'billion'/'M'/'B' applied)
    or as words."""
    out = set()
    t = text or ""
    for m in re.finditer(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(million|billion|thousand|[MBK]\b)?", t, re.I):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        mult = {"million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9, "thousand": 1e3, "k": 1e3}.get((m.group(2) or "").lower(), 1)
        out.add(v)
        out.add(v * mult)
    for w, n in _WORDNUM.items():
        if re.search(r"\b" + w + r"\b", t, re.I):
            out.add(float(n))
    return out


def rule(name: str, item: dict) -> str | None:
    """None if the value passes its rule against its own quote; else why not."""
    v, q = item.get("value"), (item.get("quote") or "").translate(_HYPHENS)
    if name == "evaluation_basis":
        rx = {"lpta": _LPTA, "tradeoff": _TRADEOFF, "highest_technically_rated": _HTRO, "lowest_price": _LOWEST}.get(v)
        if rx and not rx.search(q):
            return f"quote has no {v} wording"
        if v == "tradeoff" and _LPTA.search(q):
            return "quote says LPTA"
    if name == "clearance" and v in _CLEAR and not re.search(_CLEAR[v], q, re.I):
        return f"quote doesn't name {v}"
    if name in NUMERIC:
        n = to_number(v)
        if n is None:
            return "value is not a number"
        if not any(abs(x - n) <= max(0.5, 0.0005 * abs(n)) for x in numbers_in(q)):
            return "number not in quote"
    if name == "award_count" and v == "multiple" and to_number(item.get("max_awards")) is not None:
        if to_number(item["max_awards"]) not in numbers_in(q):
            return "number of awards not in quote"
    if name == "oral_presentation" and not re.search(r"oral|interview|presentation", q, re.I):
        return "quote doesn't mention orals"
    if name == "brand_name" and not re.search(r"brand|or[\s-]+equal", q, re.I):
        return "quote doesn't say brand name or equal"
    if name == "award_count":
        if v == "single" and not re.search(r"\bone\b|\(1\)|single|sole[\s-]?source|only one", q, re.I):
            return "no single-award wording"
        if v == "multiple" and not re.search(r"multiple|\btwo\b|\bthree\b|\bseveral\b|\(\d+\)|awards|contracts", q, re.I):
            return "no multiple-award wording"
    if name == "planned_strategy" and v in ("small_business_set_aside", "full_and_open", "sole_source"):
        if re.search(r"discretion|if any|will be given|based (?:up)?on (?:the )?(?:small business )?responses", q, re.I):
            return "boilerplate, not a plan"
    if name == "contract_type":
        if not any(re.search(rx, v or "", re.I) and re.search(rx, q, re.I) for rx in _CTYPES):
            return "not a standard contract type named in the quote"
    if "[insert" in q.lower():
        return "unfilled template text"
    if name == "sole_source_intent":
        if not re.search(r"sole[\s-]?source|only (?:one|responsible) source|single source|intends? to (?:award|negotiate|issue|procure|contract)|limited (?:competitive|sources?)", q, re.I):
            return "no sole-source wording"
        first = re.sub(r"[^a-z0-9 ]", " ", (v or "").lower()).split()
        if not first or first[0] not in re.sub(r"[^a-z0-9 ]", " ", q.lower()).split():
            return "firm not in quote"
    if name == "set_aside_considered":
        if not re.search(r"set[\s-]?aside|8\(a\)|SDVOSB|SDVOSBC|HUBZone|WOSB|EDWOSB|small business", q, re.I):
            return "no set-aside wording"
        if re.search(r"discretion|if any|will be given|based (?:up)?on (?:the )?(?:small business )?responses", q, re.I):
            return "boilerplate, not a plan"
    if name == "stated_value":
        if re.search(r"(?:(?<!not to )(?<!not )exceed\w*|in excess of|more than|greater than|\bover)\s+\$", q, re.I) and not re.search(r"exceed|excess|more than|greater|over", v or "", re.I):
            return "qualifier ('exceeds') left out"
        cur = re.search(r"(?<![A-Za-z])(AUD|CAD|EUR|GBP|JPY|KRW|MXN|PHP|NZD)\b|€|£", q)
        if cur and cur.group(0) not in (v or ""):
            return "non-US currency left out"
        want = [x for x in numbers_in(v or "") if x >= 1000]
        if not want:
            return "no amount in value"
        got = numbers_in(q)
        if not all(any(abs(g - w) <= max(0.5, 0.0005 * w) for g in got) for w in want):
            return "amount not in quote"
    if name == "cmmc_level" and not re.search(r"CMMC|Cybersecurity Maturity", q, re.I):
        return "quote doesn't mention CMMC"
    if name == "relative_importance" and not re.search(r"important|weight|equal|descending order|order of importance", q, re.I):
        return "quote doesn't compare importance"
    return None
