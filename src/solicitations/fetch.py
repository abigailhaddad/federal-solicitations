"""Download each record's attachments from SAM.gov, keep their text, delete the files.

    uv run python -m solicitations.fetch work/week.csv --out work/text [--limit N] [--rps 2]

One gzipped JSON per notice in --out: the files (name, kind, pages of text, whether pages lack a text
layer), the links to other sites (PIEE, NECO ...) and any failures. Downloads go to a temporary folder
and are deleted as soon as their text is read (the disk is nearly full). Spreadsheets are listed but not
read (in J&A packages they were price sheets). A notice already done is skipped, so a run can resume.
"""
import argparse
import csv
import gzip
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from federal_ja.collect.extract import extract_file
from federal_ja.collect.sam_api import SamClient

SKIP_EXT = (".xlsx", ".xls", ".xlsm", ".csv")
MAX_BYTES = 60_000_000


def fetch_one(cli: SamClient, notice_id: str, tmp: Path) -> dict:
    rec = {"notice_id": notice_id, "files": [], "links": [], "errors": []}
    try:
        res = cli.list_resources(notice_id)
    except Exception as e:  # noqa: BLE001 - recorded, never skipped silently
        rec["errors"].append({"step": "list", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        return rec
    for a in res:
        if a["deleted_flag"]:
            continue
        if a["resource_type"] == "link":
            rec["links"].append({"name": a["filename"], "url": a["link_url"]})
            continue
        f = {"resource_id": a["resource_id"], "name": a["filename"], "bytes": a["size"],
             "sam_posted": a["sam_posted_date"]}
        if a["filename"].lower().endswith(SKIP_EXT):
            f["kind"] = "spreadsheet_not_read"
        elif a["size"] > MAX_BYTES:
            f["kind"] = "too_large_not_read"
        else:
            dest = tmp / a["resource_id"]
            try:
                cli.download(a["resource_id"], str(dest))
                x = extract_file(str(dest), a["filename"])
                f.update(kind=x.file_kind, needs_transcription=bool(x.needs_transcription),
                         pages=x.page_texts if x.page_texts else ([x.text] if x.text else []),
                         error=x.extract_error or None)
            except Exception as e:  # noqa: BLE001
                f["kind"] = "error"
                rec["errors"].append({"step": "file", "name": a["filename"], "error": f"{type(e).__name__}: {str(e)[:200]}"})
            finally:
                if dest.exists():
                    dest.unlink()
        rec["files"].append(f)
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("week_csv")
    ap.add_argument("--out", default="work/text")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="file of notice ids to fetch (one per line)")
    ap.add_argument("--rps", type=float, default=2.0)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ids = [r["notice_id"] for r in csv.DictReader(open(a.week_csv))]
    if a.only:
        want = set(Path(a.only).read_text().split())
        ids = [i for i in ids if i in want]
    todo = [i for i in ids if not (out / f"{i}.json.gz").exists()]
    if a.limit:
        todo = todo[:a.limit]
    cli = SamClient(requests_per_second=a.rps)
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="sol_dl_") as td:
        for n, nid in enumerate(todo, 1):
            rec = fetch_one(cli, nid, Path(td))
            tmp = out / f"{nid}.json.gz.part"
            with gzip.open(tmp, "wt") as fh:
                json.dump(rec, fh)
            os.replace(tmp, out / f"{nid}.json.gz")
            if n % 25 == 0 or n == len(todo):
                print(json.dumps({"done": n, "of": len(todo), "minutes": round((time.time() - t0) / 60, 1)}), flush=True)


if __name__ == "__main__":
    sys.exit(main())
