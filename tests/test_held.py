"""Fields held from publishing (fields.HELD: solicitation relative_importance and incumbent) stay out of the
tables, the dataset files, the post, the site and the email, including the post and files made before they were held."""
import json
from pathlib import Path

import pandas as pd

from solicitations import digest, email, fields, hf, site, table

ROOT = Path(__file__).resolve().parents[1]
OLD_POST = ROOT / "data" / "posts" / "2026-11-02.json"


def test_setting():
    assert not fields.published("solicitation", "relative_importance")
    assert not fields.published("solicitation", "incumbent")
    assert fields.published("sources_sought", "incumbent")
    assert fields.published("solicitation", "evaluation_basis")


def test_table_columns():
    assert "relative_importance" not in table.columns_out("solicitation")
    assert "incumbent" not in table.columns_out("solicitation")
    assert "incumbent" in table.columns_out("sources_sought")


def held_values(post):
    """Every held quote and solicitation incumbent firm in a post as it was built (before scrubbing)."""
    out = set()
    for n in [x["notice"] for x in post["picks"]] + [n for v in post["lists"].values() for n in v]:
        if n.get("track") == "solicitation":
            for c in fields.held_columns("solicitation", n):
                if n.get(c) and c.endswith("_quote"):
                    out.add(n[c])
    return out


def test_old_post_has_held_values_and_scrub_removes_them():
    raw = json.loads(OLD_POST.read_text())
    clean = digest.scrub_post(raw)
    assert "solicitations_with_incumbent" not in clean["summary"]
    assert all(n["track"] != "solicitation" for n in clean["lists"]["incumbent"])
    for n in [x["notice"] for x in clean["picks"]] + [n for v in clean["lists"].values() for n in v]:
        assert not fields.held_columns(n["track"], n)


def test_site_and_email_leave_out_held_values():
    raw = {"week_start": "2026-11-02", "week_end": "2026-11-08", "as_of": "2026-11-10", "built_at": "2026-11-10T00:00:00+00:00",
           "summary": {"solicitations": 2, "sources_sought": 1, "evaluation_basis": {}, "evaluation_basis_found": 0,
                       "solicitations_with_value": 0, "sources_sought_with_value": 0, "solicitations_with_incumbent": 1,
                       "sources_sought_with_incumbent": 1, "sole_source_intent": 0, "no_document_text": 0},
           "picks": [{"headline": "Incumbent Acme replaced", "why": "w", "quote": "currently performed by Acme Widget Corp",
                      "notice": {"track": "solicitation", "title": "T", "sam_link": "https://sam.gov/opp/a/view",
                                 "incumbent_firm": "Acme Widget Corp", "incumbent_quote": "currently performed by Acme Widget Corp"}},
                     {"headline": "Factors", "why": "w", "quote": "Technical is significantly more important than price",
                      "notice": {"track": "solicitation", "title": "U", "sam_link": "https://sam.gov/opp/b/view",
                                 "relative_importance_quote": "Technical is significantly more important than price"}}],
           "lists": {"incumbent": [{"track": "solicitation", "title": "T", "sam_link": "https://sam.gov/opp/a/view",
                                    "incumbent_firm": "Acme Widget Corp", "incumbent_quote": "currently performed by Acme Widget Corp"},
                                   {"track": "sources_sought", "title": "S", "sam_link": "https://sam.gov/opp/c/view",
                                    "incumbent_firm": "Zeta Services LLC", "incumbent_quote": "the incumbent is Zeta Services LLC"}],
                     "sole_source_intent": [], "stated_value": [], "largest_values": []}}
    p = digest.scrub_post(raw)
    page = site.post_content(p)
    mail = email.body(p)
    for s in (page, mail, json.dumps(p)):
        assert "Acme Widget" not in s and "significantly more important" not in s
    assert "Zeta Services LLC" in page
    assert len(p["dropped"]) == 2 and not p["picks"]
    assert site.EVAL_EMPTY in page and site.EVAL_EMPTY in mail


def test_site_reads_posts_scrubbed(tmp_path):
    (tmp_path / "2026-11-02.json").write_text(OLD_POST.read_text())
    raw = json.loads(OLD_POST.read_text())
    out = tmp_path / "dist"
    site.build(site.load_posts(tmp_path), out)
    html = "".join(p.read_text() for p in out.rglob("*.html")) + (out / "feed.xml").read_text()
    for q in held_values(raw):
        assert site.e(q) not in html and q not in html


def test_dataset_files_lose_held_columns(tmp_path):
    d = tmp_path / "data" / "solicitations"
    d.mkdir(parents=True)
    df = pd.DataFrame({"notice_id": ["0012"], "relative_importance": ["x"], "relative_importance_quote": ["q"],
                       "relative_importance_source": ["s"], "incumbent_firm": ["A"], "incumbent_contract_number": ["N1"],
                       "incumbent_quote": ["q"], "incumbent_source": ["s"], "evaluation_basis": ["lpta"]})
    df.to_parquet(d / "2026-11-02.parquet", index=False)
    df.to_csv(d / "2026-11-02.csv", index=False)
    (tmp_path / "data" / "sources_sought").mkdir()
    ss = pd.DataFrame({"notice_id": ["9"], "incumbent_firm": ["Z"], "incumbent_quote": ["q"]})
    ss.to_parquet(tmp_path / "data" / "sources_sought" / "2026-11-02.parquet", index=False)
    changed = hf.strip_held(tmp_path)
    assert sorted(p.name for p in changed) == ["2026-11-02.csv", "2026-11-02.parquet"]
    assert list(pd.read_parquet(d / "2026-11-02.parquet").columns) == ["notice_id", "evaluation_basis"]
    assert (d / "2026-11-02.csv").read_text().splitlines() == ["notice_id,evaluation_basis", "0012,lpta"]
    assert "incumbent_firm" in pd.read_parquet(tmp_path / "data" / "sources_sought" / "2026-11-02.parquet").columns
    assert hf.strip_held(tmp_path) == []
