"""solicitations.email: which topic, edition and mode it hands to topic_mail."""
import json
from pathlib import Path

import pytest

from solicitations import email

POST = sorted(p for p in Path("data/posts").glob("2026-*.json") if "replaced" not in p.name)[-1]


@pytest.fixture
def calls(monkeypatch):
    seen = []
    def fake_send(topic, subject, body, edition, mode="draft", preview_to=None, **kw):
        seen.append({"topic": topic, "subject": subject, "body": body, "edition": edition, "mode": mode,
                     "preview_to": preview_to})
        return {"action": "fake", "id": "em_1", "status": "draft", "url": None}
    monkeypatch.setattr(email, "send", fake_send)
    return seen


def test_default_is_a_draft_for_the_rfp_watch_topic(calls):
    email.main([str(POST)])
    week = json.loads(POST.read_text())["week_start"]
    assert calls == [calls[0]] and calls[0]["topic"] == "RFP Watch"
    assert calls[0]["mode"] == "draft" and calls[0]["edition"] == week
    assert calls[0]["subject"].startswith("RFP Watch: week of") and len(calls[0]["subject"]) <= 250


def test_send_only_with_the_flag(calls):
    email.main([str(POST), "--send"])
    assert calls[0]["mode"] == "send"


def test_dry_run_and_preview(calls):
    email.main([str(POST), "--dry-run"])
    email.main([str(POST), "--preview-to", "a@example.com", "--preview-to", "b@example.com"])
    assert calls[0]["mode"] == "dry-run"
    assert calls[1]["mode"] == "draft" and calls[1]["preview_to"] == ["a@example.com", "b@example.com"]


def test_print_sends_nothing(calls, capsys):
    email.main([str(POST), "--print"])
    assert calls == [] and "RFP Watch: week of" in capsys.readouterr().out


def test_held_fields_never_reach_the_email(calls):
    email.main([str(POST)])
    from solicitations import fields
    for f in getattr(fields, "HELD", ()):
        assert f"\"{f}\"" not in calls[0]["body"]


def test_site_signup_subscribes_to_rfp_watch_only(tmp_path):
    # 2026-11-27: one Buttondown list, several topics; this site's form must tag "RFP Watch"
    from solicitations import site
    out = tmp_path / "dist"   # build() only wipes a directory named dist
    site.build(site.load_posts(), out)
    pages = [out / "index.html"] + sorted((out / "posts").glob("*.html"))
    assert len(pages) > 1
    for f in pages:
        t = f.read_text()
        assert f'action="{site.SUBSCRIBE_FORM_URL}"' in t and '<input type="hidden" name="tag" value="RFP Watch">' in t, f
        assert 'href="https://buttondown.com/abigailhaddad"' not in t, f
    assert 'href="index.html#subscribe"' in (out / "about.html").read_text()
