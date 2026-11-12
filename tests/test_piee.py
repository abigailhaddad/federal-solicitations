"""Offline tests for the PIEE fetcher: which notices go to PIEE, how files merge, the statuses. No network."""
from solicitations import piee

PIEE = "https://piee.eb.mil/sol/xhtml/unauth/search/oppMgmtLink.xhtml?noticeId=N0002426R4421&noticeType=SolicitationNo"
TRAINING = "https://pieetraining.eb.mil/wbt/xhtml/wbt/sol/index.xhtml"


def rec(files=(), links=(), **extra):
    return {"notice_id": "x", "files": list(files), "links": [{"name": "l", "url": u} for u in links],
            "errors": [], **extra}


def textfile(pages):
    return {"resource_id": "r1", "name": "a.pdf", "kind": "pdf", "pages": pages}


class TestNeedsPiee:
    def test_no_files_and_piee_link(self):
        assert piee.needs_piee(rec(links=[PIEE]))

    def test_files_without_text_still_need_piee(self):
        assert piee.needs_piee(rec(files=[textfile(["", "  \n"]), {"name": "p.xlsx", "kind": "spreadsheet_not_read"}],
                                   links=[PIEE]))

    def test_file_with_text_does_not(self):
        assert not piee.needs_piee(rec(files=[textfile(["", "Section C"])], links=[PIEE]))

    def test_no_piee_link_does_not(self):
        assert not piee.needs_piee(rec(links=[TRAINING, "https://www.neco.navy.mil/x"]))

    def test_lookalike_hosts_are_not_piee(self):
        assert not piee.is_piee_url(TRAINING)
        assert not piee.is_piee_url("https://toolbox-piee.eb.mil/")
        assert not piee.is_piee_url("https://piee.eb.mil.example.com/")
        assert piee.is_piee_url("https://PIEE.eb.mil/sol")
        assert not piee.is_piee_url(None)

    def test_already_done_is_skipped(self):
        r = rec(links=[PIEE], piee={"status": "not_found"})
        assert not piee.needs_piee(r)
        assert not piee.needs_piee(r, retry_errors=True)

    def test_errors_redone_only_when_asked(self):
        r = rec(links=[PIEE], piee={"status": "error"})
        assert not piee.needs_piee(r)
        assert piee.needs_piee(r, retry_errors=True)

    def test_missing_record(self):
        assert not piee.needs_piee(None)


class TestChoose:
    def test_week_order_and_ids(self):
        rows = [{"notice_id": "a", "sol": "W1"}, {"notice_id": "b", "sol": "N0002426R4421"},
                {"notice_id": "c", "sol": "X"}, {"notice_id": "d", "sol": ""}]
        records = {"a": rec(files=[textfile(["text"])], links=[PIEE]), "b": rec(links=[PIEE]),
                   "d": rec(links=[PIEE])}  # c has no record
        assert piee.choose(rows, records) == [("b", ["N0002426R4421"]), ("d", ["N0002426R4421"])]

    def test_sol_first_then_link_notice_id(self):
        assert piee.piee_ids(rec(links=[PIEE, TRAINING]), " N00024-26-R-4421 ") == ["N00024-26-R-4421", "N0002426R4421"]


class TestStatus:
    def test_not_found(self):
        assert piee.status_from_row(None) == "not_found"

    def test_restricted(self):
        assert piee.status_from_row({"Notice ID": "W1", "Restricted": "Y", "Status": "Open"}) == "restricted"

    def test_open(self):
        assert piee.status_from_row({"Notice ID": "W1", "Restricted": "N", "Status": "Open"}) == "ok"


class TestSkipKind:
    def test_kinds(self):
        assert piee.skip_kind("Price Schedule.XLSX") == "spreadsheet_not_read"
        assert piee.skip_kind("data.csv") == "spreadsheet_not_read"
        assert piee.skip_kind("drawings.zip") == "zip_not_read"
        assert piee.skip_kind("sol.pdf") is None


class TestMerge:
    def result(self, **kw):
        base = {"status": "ok", "fetched_at": "2026-11-12T00:00:00+00:00", "searched": ["W1"], "errors": [],
                "files": [
                    {"resource_id": "piee:a.pdf", "name": "a.pdf", "kind": "pdf", "pages": ["abc", "de"]},
                    {"resource_id": "piee:b.xlsx", "name": "b.xlsx", "kind": "spreadsheet_not_read"},
                    {"resource_id": "piee:c.pdf", "name": "c.pdf", "kind": "error"},
                ]}
        base.update(kw)
        return base

    def test_adds_files_keeps_sam_files_and_links(self):
        sam = {"resource_id": "s1", "name": "p.xlsx", "kind": "spreadsheet_not_read"}
        r = rec(files=[sam], links=[PIEE], errors=[{"step": "list", "error": "x"}])
        out = piee.merge(r, self.result(errors=[{"step": "piee_file", "name": "c.pdf", "error": "canceled"}]))
        assert out["files"][0] == sam
        assert [f["name"] for f in out["files"][1:]] == ["a.pdf", "b.xlsx", "c.pdf"]
        assert all(f["source"] == "piee" for f in out["files"][1:])
        assert "source" not in out["files"][0]
        assert out["links"] == r["links"]
        assert [e["step"] for e in out["errors"]] == ["list", "piee_file"]
        assert out["piee"] == {"status": "ok", "listed": 3, "read": 1, "fetched_at": "2026-11-12T00:00:00+00:00",
                               "searched": ["W1"]}
        assert piee.has_text(out)
        assert r["files"] == [sam]  # input not changed

    def test_rerun_replaces_earlier_piee_files(self):
        once = piee.merge(rec(links=[PIEE]), self.result(status="error", files=[], errors=[{"step": "piee", "error": "t"}]))
        twice = piee.merge(once, self.result())
        assert len(twice["files"]) == 3
        assert twice["errors"] == []
        assert twice["piee"]["status"] == "ok"

    def test_not_found_and_restricted_have_no_files(self):
        for st in ("not_found", "restricted"):
            out = piee.merge(rec(links=[PIEE]), self.result(status=st, files=[]))
            assert out["piee"]["status"] == st
            assert out["piee"]["listed"] == 0 and out["piee"]["read"] == 0
            assert out["files"] == []

    def test_text_chars(self):
        assert piee.text_chars(self.result()["files"]) == 5


def test_write_record_atomic(tmp_path):
    import gzip
    import json
    p = tmp_path / "n.json.gz"
    piee.write_record(p, {"a": 1})
    assert json.load(gzip.open(p, "rt")) == {"a": 1}
    assert not (tmp_path / "n.json.gz.part").exists()


class TestHistoryDedup:
    def test_newest_copy_kept(self):
        seen = set()
        latest, _ = piee.new_names(["Amendment_005.pdf", "PWS.docx"], seen)
        older, dup = piee.new_names(["Amendment_004.pdf", "pws.DOCX ", "L3_Staffing_Plan.xlsx"], seen)
        assert latest == ["Amendment_005.pdf", "PWS.docx"]
        assert older == ["Amendment_004.pdf", "L3_Staffing_Plan.xlsx"]
        assert dup == ["pws.DOCX "]

    def test_same_name_twice_on_one_page_read_once(self):
        new, dup = piee.new_names(["a.pdf", "A.pdf", "b.pdf"], set())
        assert new == ["a.pdf", "b.pdf"] and dup == ["A.pdf"]

    def test_versions_newest_first_by_posted_date(self):
        rows = [{"version": "0001", "posted": "2026/10/15 18:34 UTC"},
                {"version": "N0042126R1033", "posted": "2026/09/09 19:15 UTC"},
                {"version": "0004", "posted": "2026/10/22 13:59 UTC"},
                {"version": "x", "posted": None}]
        assert [r["version"] for r in piee.order_versions(rows)] == ["0004", "0001", "N0042126R1033", "x"]

    def test_whole_notice_dedup_keeps_newest_version(self):
        # what fetch_notice does: latest page first, then older versions newest first
        pages = [("0005", ["Amendment_005.pdf"]),
                 ("0004", ["Amendment_004.pdf", "J1_PWS.docx"]),
                 ("N1", ["J1_PWS.docx", "DD254.pdf", "Amendment_004.pdf"])]
        seen, source = set(), {}
        for v, names in pages:
            new, _ = piee.new_names(names, seen)
            for n in new:
                source[n] = v
        assert source == {"Amendment_005.pdf": "0005", "Amendment_004.pdf": "0004", "J1_PWS.docx": "0004",
                          "DD254.pdf": "N1"}

    def test_name_latest(self):
        res = {"versions": [{"version": "latest"}], "files": [{"name": "a", "piee_version": "latest"}]}
        piee.name_latest(res, ["0004", "0001", "N1"], ["0005", "0001", "N1"])
        assert res["versions"][0]["version"] == "0005" and res["files"][0]["piee_version"] == "0005"

    def test_name_latest_left_alone_when_unclear(self):
        res = {"versions": [{"version": "latest"}], "files": []}
        piee.name_latest(res, ["0004"], ["0005", "0006"])
        assert res["versions"][0]["version"] == "latest"

    def test_merge_reports_versions(self):
        result = {"status": "ok", "searched": ["W1"], "errors": [], "fetched_at": "t",
                  "files": [{"name": "a.pdf", "kind": "pdf", "pages": ["x"], "piee_version": "0005"}],
                  "versions": [{"version": "0005", "listed": 1, "new": 1, "duplicates": 0, "opened": True},
                               {"version": "0004", "listed": 2, "new": 0, "duplicates": 2, "opened": True},
                               {"version": "N1", "opened": False}]}
        p = piee.merge(rec(links=[PIEE]), result)["piee"]
        assert (p["versions_found"], p["versions_opened"], p["duplicates_skipped"]) == (3, 2, 2)
        assert p["listed"] == 1 and p["read"] == 1
