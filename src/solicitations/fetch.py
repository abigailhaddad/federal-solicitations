"""Download each record's attachments from SAM.gov, keep their text, delete the files.

    uv run python -m solicitations.fetch work/week.csv --out work/text [--limit N] [--rps 2]

One gzipped JSON per notice in --out: the files (name, kind, pages of text, whether pages lack a text
layer), the links to other sites (PIEE, NECO ...) and any failures. Downloads go to a temporary folder
and are deleted as soon as their text is read (the disk is nearly full). Spreadsheets are listed but not
read (in J&A packages they were price sheets). A notice already done is skipped, so a run can resume.

Every PDF is read twice from the same downloaded bytes: once by the reader whose text the model sees
(`engine`, pypdf in the weekly run) and once by the other reader (`second`: pdftotext when the first was
pypdf, pypdf when it was pdftotext). check.py keeps a quote on a PDF page only if it is in both readings
of that page. pypdf put lines of the NONSKID notice's page 63 (week of 2026-11-09) out of order, and a quote
spliced from two sentences passed the one-reading check. A run without pdftotext stops before any download.
"""
import argparse
import csv
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from federal_ja.collect.extract import (ENGINE_PDFTOTEXT, ENGINE_PYPDF, extract_file, pdftotext_page_texts,
                                        pypdf_page_texts)
from federal_ja.collect.sam_api import SamClient

SKIP_EXT = (".xlsx", ".xls", ".xlsm", ".csv")
MAX_BYTES = 60_000_000


def require_second_reader() -> None:
    """Stop the run, before anything is downloaded, if pdftotext (poppler) is missing: without it there is no
    second reading of PDF pages, and check.py would have to drop or wave through every PDF quote."""
    if not shutil.which("pdftotext"):
        raise SystemExit("pdftotext (poppler) is not installed; it is needed for the second reading of PDF pages "
                         "(apt-get install poppler-utils / brew install poppler)")


def second_reading(path: str, x) -> dict | None:
    """The other reader's page texts of the same PDF file: {"engine", "pages"}, or {"error"} when it could not
    read the file, or {"skipped"} for an XFA form (its text is the form's XML, not the pages). None for files
    that are not PDFs."""
    if x.file_kind != "pdf" or not x.page_texts:
        return None
    if x.xfa_form:
        return {"skipped": "XFA form: text is the form XML, read once"}
    if x.text_engine == ENGINE_PDFTOTEXT:
        try:
            return {"engine": ENGINE_PYPDF, "pages": pypdf_page_texts(path)}
        except Exception as e:  # noqa: BLE001 - recorded; check.py drops quotes on this file
            return {"error": f"pypdf: {type(e).__name__}: {str(e)[:200]}"}
    require_second_reader()
    pages = pdftotext_page_texts(path)
    if pages is None:
        return {"error": "pdftotext refused the file, failed or timed out"}
    return {"engine": ENGINE_PDFTOTEXT, "pages": pages}


def read_text(path: str, name: str) -> dict:
    """The file entry fields from text extraction, with the second reading of a PDF."""
    x = extract_file(path, name)
    out = {"kind": x.file_kind, "needs_transcription": bool(x.needs_transcription),
           "pages": x.page_texts if x.page_texts else ([x.text] if x.text else []),
           "error": x.extract_error or None}
    if x.file_kind == "pdf":
        out["engine"] = x.text_engine or None
        out["second"] = second_reading(path, x)
    return out


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
                f.update(read_text(str(dest), a["filename"]))
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
    require_second_reader()
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
