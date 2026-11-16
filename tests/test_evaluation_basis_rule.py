"""evaluation_basis filled by rule when the model left it empty (check.evaluation_basis_by_rule), on the sentences
the model missed in the week of 2026-11-09 and on wording that must not fill it."""
import pytest
from federal_ja.analysis.text import normalize

from solicitations import check, fields
from solicitations.prepare import Package, Page


def by_rule(text):
    doc = Package("n", {"psc": "R499"}, [Page("description", "SAM.gov notice description", 1, "native", text)], {})
    return check.evaluation_basis_by_rule(doc, doc.normalized())


MISSED = [
    # SABER V, Hill AFB (a0666ada)
    ("lpta", "Proposal Preparation Instructions This acquisition will utilize Lowest Price Technically Acceptable (LPTA) "
             "source selection procedures conducted in accordance with (IAW) Revolutionary Federal Acquisition Regulation "
             "(FAR) Overhaul (RFO) Part 15, Contracting by Negotiation. Technical tradeoffs will not be made, and no "
             "additional credit will be given for exceeding acceptability."),
    # B462 Entryway Carpet, Offutt AFB (9f6aeb35)
    ("lpta", "The Government will evaluate the three lowest-priced quotations. Award will be made to the responsible "
             "Offeror submitting the lowest-priced offer that receives an Acceptable rating for both Technical "
             "Capability and Past Performance."),
    # Analytical Services (eb7eba86)
    ("lpta", "3.0 BASIS OF AWARD BSA will make award to responsive and responsible Offerors who submit the lowest total "
             "prices and who meet all of BSA's technical qualifiers, as stated within the RFQ."),
    # NAWCAD (c4862b27): the conditional tradeoff sentences after it must not count against it
    ("highest_technically_rated",
     "1.2 The award will be made to the Contractor determined to have the Highest Technically Ranked Offer (HTRO), "
     "provided that the HTRO achieves an assigned Past Performance confidence rating of 'Neutral Confidence' or higher. "
     "1.4 The evaluation approach permits a best-value tradeoff only if the HTRO receives a Past Performance confidence "
     "rating of 'Limited Confidence'. In such an event, the Government will evaluate the Past Performance and Cost/Price "
     "of the next highest technically ranked Offer(s). The tradeoff will then be conducted across the factors."),
    # Turkey hazardous waste (b68d2889)
    ("tradeoff", "ADDENDA TO 52.212-2 EVALUATION SOURCE SELECTION METHOD --BEST VALUE (a) Trade-off Process Past "
                 "Performance and Price: Award shall be made to the responsible offeror whose proposal conforms to the "
                 "solicitation and is determined to be the most advantageous to the Government, past performance and "
                 "price considered."),
    # MOD/MODE Bypass Canal (6ea9e9bf)
    ("tradeoff", "The Contracting Officer reserves the right to make award to either a lower cost/price, lower-scored "
                 "offeror or a higher-rated, higher-cost/price offeror based on a rational tradeoff between evaluation "
                 "factors other than cost or price and cost or price."),
    # Exit signs (1943cc58)
    ("tradeoff", "Basis for Award. The Government will issue a purchase order to the responsible quoter whose quotation "
                 "conforming to the solicitation will be most advantageous to the Government, price and other factors "
                 "considered."),
]


@pytest.mark.parametrize("value,text", MISSED)
def test_stated_basis_is_filled(value, text):
    got = by_rule(text)
    assert got and got["value"] == value and got["source"] == "rule"
    assert got["quote"] in normalize(text)
    assert fields.rule("evaluation_basis", got) is None


NOT_STATED = [
    # a definition
    "Lowest Price Technically Acceptable (LPTA) means the source selection process in which the award will be made "
    "on the basis of the lowest evaluated price of proposals meeting the acceptability standards.",
    # FAR 15.101 in general, every option listed
    "An agency can obtain best value in negotiated acquisitions by using any one or a combination of source selection "
    "approaches, and the Government will select the tradeoff process or the lowest price technically acceptable process.",
    # may, not will
    "The Government may use the lowest price technically acceptable process to award this contract.",
    # conditional
    "If fewer than three offers are received, the Government will award on a lowest price technically acceptable basis.",
    # a title in a list of attachments: no sentence that says what will happen
    "18 Bldg 586 Seed Project FOPR (LPTA) Amendment #1 19 Cancellation and Reissue of SABER V requirement",
    # only a negated tradeoff
    "Technical tradeoffs will not be made, and no additional credit will be given for exceeding acceptability.",
    # an LPTA sentence and a tradeoff sentence that names the method: they disagree
    "The Government will award on a best-value tradeoff basis. "
    "Award will be made to the lowest priced quote which is technically acceptable.",
    # 52.212-2 printed whole in a lowest-price package
    "The Government will award a contract resulting from this solicitation to the responsible offeror whose offer "
    "conforming to the solicitation will be most advantageous to the Government, price and other factors considered. "
    "Award will be made to the lowest priced responsive bid.",
    # a question in a Q&A (3acce4e5)
    "Question 16: Will quotations be evaluated on a Price Only, LPTA, or Best Value basis?",
    # best value alone is not a basis
    "The Contractor shall deliver best value services to the Government without any accidents or mishaps.",
]


@pytest.mark.parametrize("text", NOT_STATED)
def test_no_fill(text):
    assert by_rule(text) is None


def test_lpta_sentence_outweighs_printed_52_212_2():
    """Offutt carpet (9f6aeb35): 52.212-2(a) printed whole, and the addendum's LPTA sentence."""
    got = by_rule("The Government will award a contract resulting from this solicitation to the responsible offeror "
                  "whose offer conforming to the solicitation will be most advantageous to the Government, price and "
                  "other factors considered. " + MISSED[1][1])
    assert got["value"] == "lpta" and got["quote"].startswith("Award will be made to the responsible Offeror")


def test_price_only_blocks_52_212_2_tradeoff():
    """413fe68a: 52.212-2(a) printed, but the notice says award is "based on price only"."""
    assert by_rule("The Government intends to award a firm fixed-price (FFP) contract, as required by the procedures "
                   "pursuant to FAR Part 12, based on price only. The Government will award a contract resulting from "
                   "this solicitation to the responsible Offeror whose offer conforming to the solicitation will be most "
                   "advantageous to the Government, price and other factors considered.") is None


def test_model_value_is_not_replaced():
    text = MISSED[0][1]
    doc = Package("n", {"psc": "R499"}, [Page("description", "SAM.gov notice description", 1, "native", text)], {})
    model = {"value": "tradeoff", "quote": "Technical tradeoffs will not be made, and no additional credit will be given"}
    a = {name: ([] if kind == "list" else None) for name, (kind, _) in fields.profile_fields("solicitation").items()}
    kept, _ = check.check_answer(doc, a | {"evaluation_basis": model}, "solicitation")
    assert kept["evaluation_basis"]["value"] == "tradeoff" and "source" not in kept["evaluation_basis"]
    kept, _ = check.check_answer(doc, a, "solicitation")
    assert kept["evaluation_basis"]["value"] == "lpta" and kept["evaluation_basis"]["source"] == "rule"
