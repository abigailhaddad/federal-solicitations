"""opening.py: the facts it builds, and every check that drops a sentence (and with it the opening)."""
import json
from types import SimpleNamespace

from solicitations import digest, email, opening, site

RIGA = {"notice_id": "a1", "sam_link": "https://sam.gov/opp/a1/view", "solicitation_number": "19LG7527Q0001",
        "title": "MSG Cook Service, Riga, Latvia", "agency": "STATE, DEPARTMENT OF", "sub_tier": "STATE, DEPARTMENT OF",
        "office": "U.S. EMBASSY RIGA", "psc": "S203", "new_in_week": True, "response_deadline": "2026-12-03T14:00:00+02:00",
        "evaluation_basis": "lpta",
        "evaluation_basis_quote": "The Government intends to award a contract/purchase order resulting from this solicitation to the lowest priced, technically acceptable offeror/quoter who is a responsible contractor.",
        "incumbent_firm": "Acme Catering", "incumbent_quote": "The current contractor is Acme Catering under contract 19LG.",
        "description": "The U.S. Embassy in Riga, Latvia, has a requirement for a food services program for the Marine Security "
                       "Guards at the Marine Security Guard Residence (MSGR). The Contractor shall perform services in spaces "
                       "designated by the Government."}
DOCK = {"notice_id": "b2", "sam_link": "https://sam.gov/opp/b2/view", "solicitation_number": "N1",
        "title": "Shipyard Infrastructure Optimization Program Multi-Mission Dry Dock, Multiple Award Construction Contract",
        "agency": "DEPT OF THE NAVY", "sub_tier": "NAVFAC", "office": "NAVFAC MIDLANT", "psc": "Y1PZ", "new_in_week": True,
        "response_deadline": "2026-12-15", "stated_value": "$15,000,000,000", "stated_value_what": "combined capacity",
        "stated_value_quote": "The combined capacity of all contracts is $15,000,000,000 over the ordering period.",
        "description": "NAVFAC intends to award multiple award construction contracts for the construction of a multi-mission "
                       "dry dock at Norfolk Naval Shipyard, Portsmouth, Virginia, under the Shipyard Infrastructure Optimization Program."}
JACOBS = {"notice_id": "c3", "sam_link": "https://sam.gov/opp/c3/view", "solicitation_number": "W1",
          "title": "Architect-Engineer (A-E) Services for Puerto Quetzal Phase II Expansion Design-Build (DB) RFP Package",
          "agency": "DEPT OF THE ARMY", "sub_tier": "DEPT OF THE ARMY", "office": "W2SR ENDIST CARIBBEAN(PROVIS)", "psc": "C211",
          "new_in_week": True, "response_deadline": "2026-12-08",
          "sole_source_intent": "Jacobs Government Services",
          "sole_source_intent_quote": "hereby announces its intent to negotiate and award a standalone, sole-source Firm Fixed-Price contract to Jacobs Government Services",
          "description": "The U.S. Army Corps of Engineers (USACE) Latin America and Caribbean District (SAA) hereby announces its "
                         "intent to negotiate and award a standalone, sole-source Firm Fixed-Price contract to Jacobs Government Services "
                         "for design of the Puerto Quetzal Phase II expansion in Guatemala."}
SOL = [RIGA, DOCK]
SS = [JACOBS]
POST = {"week_start": "2026-11-16", "week_end": "2026-11-22",
        "picks": [{"headline": "USACE plans sole-source A-E award to Jacobs", "why": "w", "quote": "q",
                   "notice": {"notice_id": "c3", "track": "sources_sought"}}]}


def ids(f):
    """title -> id the model sees"""
    out = {p["title"]: p["id"] for p in f["picked_out_below"]}
    for g in f["kinds_of_work_with_the_most_notices_this_week"]:
        out |= {n["title"]: n["id"] for n in g["example_notices"]}
    return out


def fake_client(sentences=None, exc=None):
    def create(**kw):
        if exc:
            raise exc
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({"sentences": sentences}))],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
    return SimpleNamespace(messages=SimpleNamespace(create=create))


def good(f):
    i = ids(f)
    return [{"text": "The U.S. Embassy in Riga wants a contractor to run a food services program for the Marine Security Guards.",
             "sources": [{"notice": i[RIGA["title"]], "quote": "has a requirement for a food services program for the Marine Security Guards"}]},
            {"text": "The Navy is seeking construction firms for a multi-mission dry dock at Norfolk Naval Shipyard in Portsmouth, Virginia, with a stated combined capacity of $15 billion and responses due December 15.",
             "sources": [{"notice": i[DOCK["title"]], "quote": "construction of a multi-mission dry dock at Norfolk Naval Shipyard"}]},
            {"text": "The Army Corps of Engineers plans a sole-source award to Jacobs Government Services for design work on the Puerto Quetzal Phase II expansion in Guatemala.",
             "sources": [{"notice": i[JACOBS["title"]], "quote": "intent to negotiate and award a standalone, sole-source Firm Fixed-Price contract"}]}]


def run(sentences):
    f, index = opening.facts(POST, SOL, SS)
    return opening.problems(sentences, f, index)


def test_facts_cover_picks_and_kinds_of_work_without_held_fields():
    f, index = opening.facts(POST, SOL, SS)
    assert [p["title"] for p in f["picked_out_below"]] == [JACOBS["title"]]
    groups = {g["kind_of_work"] for g in f["kinds_of_work_with_the_most_notices_this_week"]}
    assert groups == {"Utilities and Housekeeping Services", "Construction of Structures and Facilities",
                      "Architect and Engineering Services"}
    text = opening.facts_text(f)
    assert "Acme Catering" not in text            # solicitations' incumbent is held from publishing
    assert opening.facts(POST, SOL, SS)[0] == f   # deterministic
    # a picked notice is not repeated as an example
    assert sum(t == JACOBS["title"] for t in ids(f)) == 1 and len(index) == 3


def test_good_sentences_pass():
    f, _ = opening.facts(POST, SOL, SS)
    assert run(good(f)) == []


def test_each_check_drops_the_opening():
    f, _ = opening.facts(POST, SOL, SS)
    i = ids(f)
    riga = {"notice": i[RIGA["title"]], "quote": "has a requirement for a food services program for the Marine Security Guards"}
    dock = {"notice": i[DOCK["title"]], "quote": "construction of a multi-mission dry dock at Norfolk Naval Shipyard"}
    cases = {
        "word not in its cited notices: 'Tallinn'": [{"text": "The embassy in Tallinn wants meals cooked.", "sources": [riga]}],
        "quote not in": [{"text": "The embassy in Riga wants meals cooked.", "sources": [{**riga, "quote": "wants a chef for the ambassador's residence"}]}],
        "no checked source": [{"text": "The embassy in Riga wants meals cooked.", "sources": []}],
        "unknown notice": [{"text": "The embassy in Riga wants meals cooked.", "sources": [{**riga, "notice": "N99"}]}],
        "dollar figure": [{"text": "The Navy dry dock has a stated capacity of $20 billion.", "sources": [dock]}],
        "numbers not in its cited notices": [{"text": "The Navy wants 4 dry docks at Norfolk Naval Shipyard.", "sources": [dock]}],
        "date not in its cited notices": [{"text": "Responses on the Navy dry dock are due December 9.", "sources": [dock]}],
        "counting word": [{"text": "Most of the week's notices are for construction, such as a dry dock at Norfolk Naval Shipyard.", "sources": [dock]}],
        "hype word": [{"text": "The Navy wants a massive dry dock at Norfolk Naval Shipyard.", "sources": [dock]}],
        "advice": [{"text": "Firms should look at the Navy dry dock at Norfolk Naval Shipyard.", "sources": [dock]}],
        "exclamation": [{"text": "The Navy wants a dry dock at Norfolk Naval Shipyard!", "sources": [dock]}],
        "says sole source": [{"text": "The Navy plans a sole-source dry dock award at Norfolk Naval Shipyard.", "sources": [dock]}],
        "says incumbent": [{"text": "The embassy in Riga is replacing its incumbent cook.", "sources": [riga]}],
        "says LPTA": [{"text": "The Navy will judge the dry dock bids as lowest price technically acceptable.", "sources": [dock]}],
        "prose about the input": [{"text": "The input lists a dry dock at Norfolk Naval Shipyard.", "sources": [dock]}],
        "quote for": [{"text": "The embassy in Riga wants meals cooked.", "sources": [{**riga, "quote": "food services"}]}],
        "count": [{"text": "Three notices seek a food services program and a dry dock at Norfolk Naval Shipyard.", "sources": [riga, dock]}],
    }
    for want, sentences in cases.items():
        p = run(sentences)
        assert any(want in x for x in p), (want, p)


def test_names_and_numbers_must_come_from_the_notice_the_sentence_cites():
    # "Norfolk" is in the dry-dock notice, but this sentence cites only the Riga notice
    f, _ = opening.facts(POST, SOL, SS)
    i = ids(f)
    s = [{"text": "The embassy in Riga and Norfolk Naval Shipyard want services.",
          "sources": [{"notice": i[RIGA["title"]], "quote": "has a requirement for a food services program"}]}]
    assert any("'Norfolk'" in x for x in run(s))


def test_single_digit_dates_match_zero_padded_deadlines():
    # JACOBS is due 2026-12-08; "December 8" must pass the number and date checks
    f, _ = opening.facts(POST, SOL, SS)
    i = ids(f)
    s = [{"text": "Responses to the Army Corps of Engineers notice on the Puerto Quetzal Phase II expansion are due December 8.",
          "sources": [{"notice": i[JACOBS["title"]], "quote": "Puerto Quetzal Phase II Expansion Design-Build"}]}]
    assert run(s) == []


def test_dollar_units_are_not_names_and_two_notices_counts_two():
    f, _ = opening.facts(POST, SOL, SS)
    i = ids(f)
    s = [{"text": "Two notices seek a food services program in Riga and a $15B dry dock at Norfolk Naval Shipyard.",
          "sources": [{"notice": i[RIGA["title"]], "quote": "has a requirement for a food services program"},
                      {"notice": i[DOCK["title"]], "quote": "construction of a multi-mission dry dock at Norfolk Naval Shipyard"}]}]
    assert run(s) == []


def test_too_long():
    f, _ = opening.facts(POST, SOL, SS)
    assert any(x.startswith("too long") for x in run(good(f) * 2))


def test_write_keeps_a_passing_opening_and_the_email_and_page_show_it():
    f, _ = opening.facts(POST, SOL, SS)
    o = opening.write(POST, SOL, SS, client=fake_client(good(f)))
    assert o["status"] == "kept" and o["problems"] == [] and o["text"].startswith("The U.S. Embassy in Riga")
    assert {s["notice_id"] for s in o["sources"]} == {"a1", "b2", "c3"}
    post = json.loads(sorted(p for p in __import__("pathlib").Path("data/posts").glob("2026-*.json") if "replaced" not in p.name)[-1].read_text())
    post["opening"] = o
    p = digest.scrub_post(post)
    assert email.body(p).startswith("The U.S. Embassy in Riga")
    assert '<p class="lede opening">The U.S. Embassy in Riga' in site.post_content(p)


def test_failed_or_errored_opening_is_left_out():
    f, _ = opening.facts(POST, SOL, SS)
    bad = good(f)
    bad[0]["text"] = bad[0]["text"].replace("Riga", "Tallinn")
    o = opening.write(POST, SOL, SS, client=fake_client(bad))
    assert o["status"] == "dropped" and any("Tallinn" in x for x in o["problems"])
    e = opening.write(POST, SOL, SS, client=fake_client(exc=RuntimeError("boom")))
    assert e["status"] == "error" and e["text"] is None
    for x in (o, e):
        post = {"opening": x}
        assert opening.kept_text(post) is None
    p = digest.scrub_post({**json.loads(open("data/posts/2026-11-16.json").read()), "opening": o})
    assert "Tallinn" not in email.body(p) and "Tallinn" not in site.post_content(p)


def test_scrub_drops_an_opening_resting_on_a_held_field():
    o = {"status": "kept", "text": "The embassy in Riga wants meals cooked.",
         "sources": [{"notice_id": "a1", "track": "solicitation", "quote": "x", "found_in": ["incumbent_quote"]}]}
    assert opening.scrub(o) is None
    o2 = {**o, "sources": [{**o["sources"][0], "found_in": ["description"]}]}
    assert opening.scrub(o2) == o2
    o3 = {**o2, "text": "The embassy in Riga is replacing its incumbent cook."}
    assert opening.scrub(o3) is None


def test_old_posts_without_an_opening_still_render():
    post = json.loads(open("data/posts/2026-11-16.json").read())
    p = digest.scrub_post(post)
    assert "opening" not in p and email.body(p).startswith("Federal services and construction solicitations")


def test_prompt_carries_the_light_voice_and_announcing_oddness_is_refused():
    assert "dry and a little wry" in opening.PROMPT.format(facts="{}", voice=opening.VOICE)
    assert "at most one wry turn" in opening.VOICE
    f, _ = opening.facts(POST, SOL, SS)
    i = ids(f)
    s = [{"text": "Oddly, the Navy wants a dry dock at Norfolk Naval Shipyard.",
          "sources": [{"notice": i[DOCK["title"]], "quote": "construction of a multi-mission dry dock at Norfolk Naval Shipyard"}]}]
    assert any("hype word: 'Oddly'" in x for x in run(s))


def test_written_out_dates_in_a_notice_count():
    # 2026-11-16 sample: the embassy notice says "for the period January 1, 2027, through December 31, 2027."
    from solicitations import opening
    text = "for the period January 1, 2027, through December 31, 2027.The requirement is the rental; due 12/08/2026."
    assert {(2027, 1, 1), (2027, 12, 31), (2026, 12, 8)} <= opening._iso_dates(text)
    assert (2027, 2, 1) not in opening._iso_dates(text)
