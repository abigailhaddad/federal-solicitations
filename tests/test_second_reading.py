"""A quote on a PDF page must be in both readings of that page (check.second_check), on the page that showed
the problem: NONSKID FOR NAVAL SHIPS/VESSELS, SAM.gov resource 20fef4e3de6a47b38b8af632d53c125a, page 63
(week of 2026-11-09). fixtures/nonskid_p63.pdf is that page alone, cut from the file as posted; pypdf and
pdftotext read it exactly as they read page 63 of the whole file. Needs poppler's pdftotext (the weekly
workflow installs it); without it these tests fail rather than skip."""
from pathlib import Path

import pytest
from federal_ja.collect.extract import pdftotext_page_texts, pypdf_page_texts

from solicitations import check, fetch, fields
from solicitations.prepare import NOT_RECORDED, Package, Page

PAGE = Path(__file__).parent / "fixtures" / "nonskid_p63.pdf"
RID = "20fef4e3de6a47b38b8af632d53c125a"
# the published quote: two sentences spliced by pypdf's line order
SPLICED = ("By submission of all requested items in Volumes I, II, and III as noted in Section L, the offeror being "
           "significantly more important than price accedes to all solicitation requirements")
# the same sentence as the page prints it
REAL = ("By submission of all requested items in Volumes I, II, and III as noted in Section L, the offeror accedes "
        "to all solicitation requirements")
BOTH = "This Request for Proposal is a Total Small Business Set-Aside under NAICS 336611."


def package(second, description=""):
    pages = [Page("description", "SAM.gov notice description", 1, "native", description)] if description else []
    pages.append(Page(RID, "N4002727R0004.pdf", 1, "native", pypdf_page_texts(str(PAGE))[0]))
    return Package("4fd3aec720cf473f83a4569ad2df886e", {"psc": "2090"}, pages, {RID: second})


def answer(**kw):
    a = {name: ([] if kind == "list" else None) for name, (kind, _) in fields.profile_fields("solicitation").items()}
    return a | kw


def second_pdftotext():
    pages = pdftotext_page_texts(str(PAGE))
    assert pages is not None, "pdftotext (poppler) is needed for these tests"
    return {"engine": "pdftotext", "pages": pages}


def test_spliced_quote_passes_pypdf_alone():
    doc = package(second_pdftotext())
    assert check.locate_quote(SPLICED, doc, doc.normalized(), ignore_case=True).verified


def test_spliced_quote_is_dropped():
    doc = package(second_pdftotext())
    item = {"value": "Past Performance significantly more important than price", "quote": SPLICED}
    kept, dropped = check.check_answer(doc, answer(relative_importance=item), "solicitation")
    assert kept["relative_importance"] is None
    assert [d["why"] for d in dropped] == ["quote not in second extraction"]


def test_quote_in_both_readings_is_kept():
    doc = package(second_pdftotext())
    item = {"value": "total small business set-aside", "quote": BOTH}
    qm, why = check.verify(BOTH, doc, doc.normalized())
    assert qm.verified and why is None
    assert qm.pages == [{"resource_id": RID, "page_number": 1, "text_source": "native"}]
    assert check.second_check(doc, item["quote"], qm) is None


def test_real_sentence_is_not_in_pypdf_reading():
    """The page's real sentence fails the first check (pypdf's order), so the model could not have quoted it."""
    doc = package(second_pdftotext())
    assert not check.locate_quote(REAL, doc, doc.normalized(), ignore_case=True).verified


def test_missing_second_reading_fails_loudly():
    doc = package(NOT_RECORDED)
    item = {"value": "x", "quote": BOTH}
    with pytest.raises(check.NoSecondReading):
        check.check_answer(doc, answer(relative_importance=item), "solicitation")


def test_failed_second_reading_drops_the_value():
    doc = package({"error": "pdftotext refused the file, failed or timed out"})
    kept, dropped = check.check_answer(doc, answer(relative_importance={"value": "x", "quote": BOTH}), "solicitation")
    assert kept["relative_importance"] is None
    assert dropped[0]["why"] == "no second extraction of the file"


def test_page_count_mismatch_drops_the_value():
    doc = package({"engine": "pdftotext", "pages": ["a", "b"]})
    kept, dropped = check.check_answer(doc, answer(relative_importance={"value": "x", "quote": BOTH}), "solicitation")
    assert dropped[0]["why"] == "second extraction has a different page count"


def test_description_quote_needs_one_reading():
    desc = "The Government will award one firm-fixed-price contract for nonskid deck coverings."
    doc = package(NOT_RECORDED, description=desc)
    qm, why = check.verify("will award one firm-fixed-price contract for nonskid", doc, doc.normalized())
    assert qm.verified and why is None


def test_fetch_reads_pdfs_twice(monkeypatch):
    monkeypatch.setenv("JA_PDF_ENGINE", "pypdf")
    f = fetch.read_text(str(PAGE), "N4002727R0004.pdf")
    assert f["kind"] == "pdf" and f["engine"] == "pypdf"
    assert f["second"]["engine"] == "pdftotext" and len(f["second"]["pages"]) == len(f["pages"]) == 1
    monkeypatch.setenv("JA_PDF_ENGINE", "pdftotext")
    f = fetch.read_text(str(PAGE), "N4002727R0004.pdf")
    assert f["engine"] == "pdftotext" and f["second"]["engine"] == "pypdf"


def test_no_pdftotext_stops_the_run(monkeypatch):
    monkeypatch.setattr(fetch.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit):
        fetch.require_second_reader()


def one_page(first, second):
    return Package("n", {}, [Page("r", "a.pdf", 1, "native", first)], {"r": {"engine": "pdftotext", "pages": [second]}})


def test_kerning_space_and_line_end_hyphen_are_not_drops():
    """Seen in the 2026-11-09 samples: pypdf "NA VOCEANO" / pdftotext "NAVOCEANO"; pypdf "non-\\nprice" /
    pdftotext "nonprice" (pdftotext joins a word hyphenated at a line end)."""
    q1 = "This is considered a new requirement for NA VOCEANO as previously"
    doc = one_page(q1 + ", these services", "This is considered a new requirement for NAVOCEANO as previously, these")
    assert check.verify(q1, doc, doc.normalized())[1] is None
    q2 = "All non-price evaluation factors, when combined, are significantly more important than price."
    doc = one_page("All non-\nprice evaluation factors, when combined, are significantly more important than price.",
                   "All nonprice evaluation factors, when combined, are significantly more important than price.")
    assert check.verify(q2, doc, doc.normalized())[1] is None


def test_words_out_of_order_are_a_drop():
    q = "the offeror being significantly more important than price accedes"
    doc = one_page("Section L, the offeror being significantly more important than price accedes to all",
                   "based on Past Performance being significantly more important than price. Section L, the offeror accedes")
    assert check.verify(q, doc, doc.normalized())[1] == "quote not in second extraction"
