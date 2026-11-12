"""Run one week end to end: SAM.gov files, documents, extraction, checks, tables, dataset, post.

    uv run python -m solicitations.weekly [--week 2026-11-09] [--max-usd 8] [--no-publish]

Default week: the last full Monday–Sunday week before today (UTC). Steps, each one a module of this package:
  1. download SAM.gov's active-notices CSV and the fiscal-year archive(s) that can hold the week
  2. week     pick the week's notices (one per solicitation number, latest posting)
  3. tracks   services solicitations (PSC starts with a letter) and all sources-sought notices
  4. fetch    attachments from SAM.gov;  piee: documents for Defense notices hosted on PIEE
  5. run      Claude Haiku 5.5, one batch per track, refused if the estimate is over --max-usd
  6. check    quotes and rules;  table: this week's files;  sample: this week's check samples
  7. hf       the dataset (this week's files and the card);  digest: the week's post
Nothing is published with --no-publish. Each step's summary goes to work/weekly_<week>.json.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import requests

# the same S3 files the J&A collection reads (federal_ja.collect.config)
from federal_ja.collect.config import ACTIVE_CSV_URL as ACTIVE, ARCHIVE_CSV_URL_TEMPLATE as ARCHIVE  # noqa: E402
HF_REPO_ID = "abigailhaddad/federal-solicitations"


def last_week(today: dt.date) -> str:
    monday = today - dt.timedelta(days=today.weekday())
    return (monday - dt.timedelta(days=7)).isoformat()


def fiscal_years(start: dt.date) -> list[int]:
    end = start + dt.timedelta(days=6)
    return sorted({d.year + (1 if d.month >= 10 else 0) for d in (start, end)})


def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(".part")
    with requests.get(url, stream=True, timeout=(30, 600), headers={"User-Agent": "federal-solicitations weekly"}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.rename(dest)
    return dest


def step(name: str, args: list[str], log: dict, ok=(0,)) -> int:
    print(f"== {name}: {' '.join(args)}", flush=True)
    rc = subprocess.call([sys.executable, "-m", *args])
    log[name] = {"exit": rc}
    if rc not in ok:
        raise SystemExit(f"{name} failed (exit {rc})")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="solicitations.weekly")
    ap.add_argument("--week", default=None, help="Monday the week starts (default: last full week)")
    ap.add_argument("--work", type=Path, default=Path("work"))
    ap.add_argument("--max-usd", type=float, default=8.0, help="cap on each track's extraction estimate")
    ap.add_argument("--post-max-usd", type=float, default=1.0)
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--skip-piee", action="store_true")
    ap.add_argument("--piee-minutes", type=float, default=150, help="time budget for PIEE (about 105 s a notice)")
    a = ap.parse_args(argv)
    week = a.week or last_week(dt.datetime.now(dt.timezone.utc).date())
    start = dt.date.fromisoformat(week)
    if start.weekday() != 0:
        raise SystemExit(f"{week} is not a Monday")
    end = (start + dt.timedelta(days=6)).isoformat()
    w = a.work / week
    (w / "csv").mkdir(parents=True, exist_ok=True)
    log: dict = {"week": week, "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}

    files = [download(ACTIVE, w / "csv" / "active.csv")]
    for fy in fiscal_years(start):
        try:
            files.append(download(ARCHIVE.format(fy=fy), w / "csv" / f"FY{fy}.csv"))
        except requests.HTTPError as e:   # the new year's archive may not exist yet
            log.setdefault("archives_missing", []).append(f"FY{fy}: {e}")
    week_csv = w / "week.csv"
    step("week", ["solicitations.week", "--from", week, "--to", end, *map(str, files), "--out", str(week_csv)], log)

    rows = list(csv.DictReader(open(week_csv)))
    sol = [r["notice_id"] for r in rows if r["type"] == "Solicitation" and (r["psc"] or "")[:1].isalpha()]
    ss = [r["notice_id"] for r in rows if r["type"] == "Sources Sought"]
    for name, ids in (("solicitation", sol), ("sources_sought", ss), ("tracks", sol + ss)):
        (w / f"ids_{name}.txt").write_text("\n".join(ids) + "\n")
    log["tracks"] = {"week_records": len(rows), "solicitation": len(sol), "sources_sought": len(ss)}

    text = w / "text"
    step("fetch", ["solicitations.fetch", str(week_csv), "--out", str(text), "--only", str(w / "ids_tracks.txt")], log)
    if not a.skip_piee:
        # exit 2 = PIEE unreachable or asking for a login: carry on without it, and say so
        rc = step("piee", ["solicitations.piee", str(week_csv), "--text", str(text), "--only", str(w / "ids_tracks.txt"),
                           "--max-minutes", str(a.piee_minutes)],
                  log, ok=(0, 2))
        log["piee"]["note"] = "stopped: PIEE unreachable or asked for a login" if rc == 2 else "ok"

    answers = w / "answers.jsonl"
    for profile in ("solicitation", "sources_sought"):
        step(f"run_{profile}", ["solicitations.run", str(week_csv), "--ids", str(w / f"ids_{profile}.txt"),
                                "--profile", profile, "--text", str(text), "--out", str(answers),
                                "--max-usd", str(a.max_usd)], log)
    checked = w / "checked.jsonl"
    step("check", ["solicitations.check", str(week_csv), str(answers), "--text", str(text), "--out", str(checked)], log)

    publish = a.work / "publish"
    if not a.no_publish:
        from huggingface_hub import snapshot_download
        snapshot_download(HF_REPO_ID, repo_type="dataset", local_dir=str(publish), allow_patterns=["data/*/*.parquet", "data/*/*.csv"])
    step("table", ["solicitations.table", str(week_csv), str(checked), "--week", week, "--text", str(text), "--out", str(publish)], log)
    step("sample", ["solicitations.sample", str(week_csv), str(checked), "--text", str(text), "--out", str(w / "checks"),
                    "--seed", week.replace("-", "")], log)
    if a.no_publish:
        step("card", ["solicitations.hf", "card", str(publish)], log)
    else:
        step("hf", ["solicitations.hf", "publish", str(publish), "--message", f"Add week of {week}"], log)
    step("digest", ["solicitations.digest", str(publish), "--week", week, "--max-usd", str(a.post_max_usd)]
         + (["--dry-run"] if a.no_publish else []), log)
    log["finished"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (a.work / f"weekly_{week}.json").write_text(json.dumps(log, indent=1))
    print(json.dumps(log, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
