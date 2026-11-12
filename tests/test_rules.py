"""Rules that decide what is published, on real wording from the week of 2026-11-02."""
from solicitations import fields
from solicitations.check import as_written
from solicitations.digest import check_pick
from solicitations.table import cp1252, incumbent_parts


def sv(value, quote):
    return fields.rule("stated_value", {"value": value, "quote": quote})


class TestStatedValue:
    def test_per_contract_amount_is_dropped(self):
        q = "5-year ceiling amount of $500,000.00 each."
        assert sv("500000", q) == "per-contract amount read as a total"

    def test_amount_to_any_one_contractor_is_dropped(self):
        q = "a maximum aggregate value of $25,000,000.00 to any one contractor."
        assert sv("25000000", q) == "per-contract amount read as a total"

    def test_guaranteed_minimum_is_dropped(self):
        q = "The United States Government (USG) shall place orders guaranteeing a minimum of BZ $787,936.07 for each period"
        assert sv("BZ $787,936.07", q) == "guaranteed minimum read as a value"

    def test_ceiling_next_to_a_minimum_is_kept(self):
        q = "The minimum guarantee is $10,000.00 and the ceiling is $7,500,000.00 for the entire duration of the contract."
        assert sv("$7,500,000.00", q) is None

    def test_maximum_after_a_minimum_is_kept(self):
        q = "not less than a total of $2,500 (minimum) nor more than a total of $90,000,000 (maximum)"
        assert sv("$90,000,000", q) is None

    def test_shared_capacity_with_each_contract_nearby_is_kept(self):
        q = "Each contract would likely have a base period of one year ... and have a total shared capacity of $35,000,000."
        assert sv("$35,000,000", q) is None

    def test_qualifier_must_be_kept(self):
        assert sv("$3,000,000", "estimated total contract value exceeds $3 million") is not None

    def test_currency_must_be_kept(self):
        assert sv("400000", "The amount of all orders shall not exceed $400,000.00AUD.") == "non-US currency left out"


class TestAsWritten:
    def test_bare_number_takes_the_quote_spelling(self):
        assert as_written("8000000", "estimated value of $8,000,000.00 over five years") == "$8,000,000.00"

    def test_range_of_bare_numbers(self):
        q = "between $10,000,000.00 to $15,000,000.00"
        assert as_written("10000000 to 15000000", q) == "$10,000,000.00 to $15,000,000.00"

    def test_trailing_comma_from_a_clause_break(self):
        assert as_written("10000000", "funding of $10,000,000, subject to") == "$10,000,000"

    def test_two_spellings_leaves_it_alone(self):
        assert as_written("500", "$500 and $500.00") == "500"


class TestIncumbentParts:
    def test_firm_and_number(self):
        assert incumbent_parts("Petersons LLC (contract H9821021C004)", "H9821021C004") == ("Petersons LLC", "H9821021C004")

    def test_number_with_a_model_note_has_no_firm(self):
        v = "Contract No. 19BH2022D0001 (prior contract whose SOW is attached as current benefits; incumbent contractor not named)"
        assert incumbent_parts(v, "19BH2022D0001") == (None, "19BH2022D0001")

    def test_bare_contract_number(self):
        assert incumbent_parts("W9124J-25-P-A057", None) == (None, "W9124J-25-P-A057")

    def test_firm_only(self):
        assert incumbent_parts("SAIC", None) == ("SAIC", None)


def test_cp1252_restores_curly_quotes():
    assert cp1252("offeror\x92s") == "offeror’s"
    assert cp1252("café") == "café"


class TestPickChecks:
    def notice(self, **kw):
        base = {"title": "T", "description": "The Government intends to buy widgets for the base.",
                "stated_value": None, "stated_value_quote": None, "sole_source_intent": None,
                "incumbent_quote": None, "evaluation_basis": None}
        return base | kw

    def test_quote_must_be_in_the_notice(self):
        c = [(self.notice(), "solicitation")]
        assert check_pick({"index": 0, "headline": "h", "why": "w", "quote": "intends to buy gadgets"}, c)
        assert check_pick({"index": 0, "headline": "h", "why": "w", "quote": "intends to buy widgets"}, c) is None

    def test_dollar_figure_must_be_the_stated_value(self):
        n = self.notice(stated_value="$15,000,000,000", stated_value_quote="maximum of $15,000,000,000 for all orders")
        p = {"index": 0, "headline": "A $15 billion contract", "why": "w", "quote": "intends to buy widgets"}
        assert check_pick(p, [(n, "solicitation")]) is None
        assert check_pick(p | {"headline": "A $20 billion contract"}, [(n, "solicitation")])

    def test_sole_source_claim_needs_the_value(self):
        p = {"index": 0, "headline": "Army plans sole-source award", "why": "w", "quote": "intends to buy widgets"}
        assert check_pick(p, [(self.notice(), "sources_sought")])
        assert check_pick(p, [(self.notice(sole_source_intent="X"), "sources_sought")]) is None
