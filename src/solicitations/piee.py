"""Read the attachments that SAM.gov does not carry from PIEE's public Solicitation Module.

    uv run python -m solicitations.piee work/week.csv --text work/text [--limit N] [--only FILE]

Many Army and Navy notices post no files on SAM.gov, only a link to piee.eb.mil. For each notice in the
week whose work/text record has no file with any page text and has a piee.eb.mil link, this looks the
notice up on PIEE's no-login search (https://piee.eb.mil/sol/xhtml/unauth/index.xhtml) by its
solicitation number, downloads the attachments one at a time, keeps their text, deletes the files, and
adds them to the existing record with "source": "piee". A "piee" key on the record says what happened:

    {"status": "ok" | "not_found" | "restricted" | "error", "listed": n, "read": n, "fetched_at": iso, ...}

PIEE's search opens the latest version of a notice. Each version page has an "Update / Notice" table of
the other versions (base notice and every amendment); each of those is opened too, newest first, and its
files read. A file whose name (ignoring case) was already on a newer version is not downloaded again: the
newest copy is kept. Each file entry says which version it came from ("piee_version": "latest", "0004",
the base notice id ...; "piee_version_posted"; "piee_date" = the attachment's date on PIEE).

"listed" counts the distinct attachment files across versions (never the "Download All Attachments" zip,
which is not used); "read" counts the files downloaded and run through text extraction. The "piee" key
also has versions_found / versions_opened / duplicates_skipped and a per-version list. A record that already has a
"piee" key is skipped, so a run can resume (--retry-errors redoes the ones with status "error").

Politeness: one headless browser, one notice at a time, 2 s after each download, 3 s before each page
load (search form, search, notice, each version) and between notices.
If PIEE shows a login/CAC page, or the search page fails to load for 3 notices in a row, the run stops
and exits 2; those notices are not marked, so a later run picks them up. It never tries to get past a login.
"""
import argparse
import csv
import gzip
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from federal_ja.collect.extract import extract_file

INDEX_URL = "https://piee.eb.mil/sol/xhtml/unauth/index.xhtml"
SPREADSHEET_EXT = (".xlsx", ".xls", ".xlsm", ".csv")
ZIP_EXT = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz")
MAX_BYTES = 60_000_000
DOWNLOAD_PAUSE = 2.0
NOTICE_PAUSE = 3.0
PAGE_PAUSE = 3.0
MAX_UNREACHABLE = 3
TIMEOUT_MS = 90_000


class StopRun(Exception):
    """PIEE asked for a login or stopped answering; the whole run must stop."""


# ---------- pure parts (tested offline) ----------

def is_piee_url(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "piee.eb.mil" or host.endswith(".piee.eb.mil")


def has_text(rec: dict) -> bool:
    return any(any((p or "").strip() for p in (f.get("pages") or [])) for f in rec.get("files", []))


def piee_ids(rec: dict, sol: str | None) -> list[str]:
    """Notice ids to search PIEE by: the solicitation number first, then any noticeId in the PIEE links."""
    out = []
    if sol and sol.strip():
        out.append(sol.strip())
    for link in rec.get("links", []):
        if is_piee_url(link.get("url")):
            for v in parse_qs(urlparse(link["url"]).query).get("noticeId", []):
                if v.strip() and v.strip() not in out:
                    out.append(v.strip())
    return out


def needs_piee(rec: dict | None, retry_errors: bool = False) -> bool:
    if rec is None:
        return False
    if "piee" in rec and not (retry_errors and rec["piee"].get("status") == "error"):
        return False
    return not has_text(rec) and any(is_piee_url(l.get("url")) for l in rec.get("links", []))


def choose(rows: list[dict], records: dict, retry_errors: bool = False) -> list[tuple[str, list[str]]]:
    """(notice_id, ids to search) for each week row whose record needs PIEE, in week order."""
    out = []
    for r in rows:
        rec = records.get(r["notice_id"])
        if needs_piee(rec, retry_errors):
            ids = piee_ids(rec, r.get("sol"))
            if ids:
                out.append((r["notice_id"], ids))
    return out


def skip_kind(name: str) -> str | None:
    n = name.lower()
    if n.endswith(SPREADSHEET_EXT):
        return "spreadsheet_not_read"
    if n.endswith(ZIP_EXT):
        return "zip_not_read"
    return None


def status_from_row(row: dict | None) -> str:
    """Search result row (header -> cell text) to a status before opening the notice."""
    if row is None:
        return "not_found"
    if (row.get("Restricted") or "").strip().upper().startswith("Y"):
        return "restricted"
    return "ok"


def posted_key(posted: str | None) -> str:
    """PIEE dates look like '2026/10/22 13:59 UTC'; that sorts correctly as text. Missing dates sort last."""
    return (posted or "").strip()


def order_versions(rows: list[dict]) -> list[dict]:
    """Earlier versions from the history table, newest first by posted date (ties keep table order)."""
    return sorted(rows, key=lambda r: posted_key(r.get("posted")), reverse=True)


def new_names(names: list[str], seen: set) -> tuple[list[str], list[str]]:
    """Split a version's file names into ones not seen in a newer version (read them) and duplicates
    (skip them: the newer copy is kept). Names compare without case or surrounding space. Adds to seen."""
    new, dup = [], []
    for n in names:
        k = n.strip().casefold()
        if k in seen:
            dup.append(n)
        else:
            seen.add(k)
            new.append(n)
    return new, dup


def merge(rec: dict, result: dict) -> dict:
    """Add PIEE files and errors to a SAM.gov record. Earlier PIEE files/errors are replaced, not doubled."""
    out = dict(rec)
    out["files"] = [f for f in rec.get("files", []) if f.get("source") != "piee"] + [
        dict(f, source="piee") for f in result["files"]]
    out["errors"] = [e for e in rec.get("errors", []) if not str(e.get("step", "")).startswith("piee")] + result["errors"]
    files = result["files"]
    out["piee"] = {
        "status": result["status"],
        "listed": len(files),
        "read": sum(1 for f in files if f.get("kind") not in (
            "error", "spreadsheet_not_read", "zip_not_read", "too_large_not_read")),
        "fetched_at": result.get("fetched_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "searched": result.get("searched", []),
    }
    if "versions" in result:
        v = result["versions"]
        out["piee"].update(versions_found=len(v), versions_opened=sum(1 for x in v if x.get("opened")),
                           duplicates_skipped=sum(x.get("duplicates", 0) for x in v), versions=v)
    if result.get("piee_row"):
        out["piee"]["row"] = result["piee_row"]
    return out


def text_chars(files: list[dict]) -> int:
    return sum(len(p or "") for f in files for p in (f.get("pages") or []))


def write_record(path: Path, rec: dict) -> None:
    tmp = path.with_name(path.name + ".part")
    with gzip.open(tmp, "wt") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path)


# ---------- browser parts ----------

def looks_like_login(pg) -> bool:
    url = pg.url.lower()
    if "piee.eb.mil" in url and "/unauth/" not in url:
        return True
    if any(w in url for w in ("login", "logon", "/sso", "cac")):
        return True
    return pg.locator("input[type=password]").count() > 0


def open_search(pg):
    """Load the public search form. Raises StopRun on a login page; other failures propagate."""
    pg.goto(INDEX_URL, timeout=TIMEOUT_MS)
    if looks_like_login(pg):
        raise StopRun(f"PIEE showed a login page at {pg.url}")
    time.sleep(PAGE_PAUSE)
    pg.locator("#searchSolicitation").first.click()
    pg.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    if looks_like_login(pg):
        raise StopRun(f"PIEE showed a login page at {pg.url}")
    box = pg.get_by_role("textbox", name="Enter the Notice ID")
    box.wait_for(timeout=TIMEOUT_MS)
    return box


def search(pg, pid: str):
    """(first matching link, its row as header->cell, number of matching links); (None, None, 0) if none.
    Raises SearchPageDown if the search form never loads, StopRun on a login page."""
    try:
        box = open_search(pg)
    except StopRun:
        raise
    except Exception as e:  # noqa: BLE001
        raise SearchPageDown(f"{type(e).__name__}: {str(e)[:200]}") from e
    box.fill(pid)
    time.sleep(PAGE_PAUSE)
    pg.locator("#search").click()
    pg.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    if looks_like_login(pg):
        raise StopRun(f"PIEE showed a login page at {pg.url}")
    link = pg.get_by_role("link", name=pid, exact=True)
    if link.count() == 0:
        return None, None, 0
    first = link.first
    heads = [h.strip() for h in first.locator("xpath=ancestor::table[1]").locator("th").all_inner_texts()]
    cells = [c.strip() for c in first.locator("xpath=ancestor::tr[1]").locator("td").all_inner_texts()]
    row = dict(zip(heads, cells)) if len(heads) == len(cells) else {"cells": cells}
    return first, row, link.count()


class SearchPageDown(Exception):
    pass


def page_files(pg) -> list[tuple[str, str | None, object]]:
    """(name, PIEE attachment date, anchor) for each single-file attachment link on a notice page.
    The "Download All Attachments" zip link is left out."""
    dates = {}
    th = pg.locator("th", has_text="Include in Response")
    if th.count():
        for tr in th.first.locator("xpath=ancestor::table[1]").locator("tbody tr").all():
            c = [x.strip() for x in tr.locator("td").all_inner_texts()]
            if len(c) > 3:
                dates.setdefault(c[2], c[3])
    out = []
    for a in pg.locator("a").all():
        oc = a.get_attribute("onclick") or ""
        name = (a.inner_text() or "").strip()
        if "viewAttachment" in oc and "downloadAllAttachments" not in oc and name != "Download All Attachments":
            out.append((name, dates.get(name), a))
    return out


def history_rows(pg) -> list[dict]:
    """The "Update / Notice" table on a notice page: the other versions of this notice."""
    th = pg.locator("th", has_text="Update / Notice ID")
    if not th.count():
        return []
    out = []
    for tr in th.first.locator("xpath=ancestor::table[1]").locator("tbody tr").all():
        c = [x.strip() for x in tr.locator("td").all_inner_texts()]
        if c and c[0]:
            out.append({"version": c[0], "posted": c[2] if len(c) > 2 else None})
    return out


def open_version(pg, label: str) -> None:
    """Click the history row whose first cell is `label` (rows are clicked by label, not position,
    because each version page lists the *other* versions)."""
    th = pg.locator("th", has_text="Update / Notice ID")
    if not th.count():
        raise RuntimeError("no history table on the current page")
    for tr in th.first.locator("xpath=ancestor::table[1]").locator("tbody tr").all():
        if tr.locator("td").first.inner_text().strip() == label:
            tr.locator("a").first.click()
            pg.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
            if looks_like_login(pg):
                raise StopRun(f"PIEE showed a login page at {pg.url}")
            return
    raise RuntimeError(f"history row {label!r} not found on the current page")


def read_file(pg, name: str, a, tmp: Path, i: int, res: dict) -> dict:
    f = {"resource_id": f"piee:{name}", "name": name, "bytes": None, "sam_posted": None}
    kind = skip_kind(name)
    if kind:
        f["kind"] = kind
        return f
    dest = tmp / f"piee_{i}"
    d = None
    try:
        with pg.expect_download(timeout=TIMEOUT_MS) as dl:
            a.click()
        d = dl.value
        if d.suggested_filename and d.suggested_filename != name:
            f["name"] = d.suggested_filename
            f["resource_id"] = f"piee:{d.suggested_filename}"
        d.save_as(dest)
        f["bytes"] = dest.stat().st_size
        if f["bytes"] > MAX_BYTES:
            f["kind"] = "too_large_not_read"
        else:
            x = extract_file(str(dest), f["name"])
            f.update(kind=x.file_kind, needs_transcription=bool(x.needs_transcription),
                     pages=x.page_texts if x.page_texts else ([x.text] if x.text else []),
                     error=x.extract_error or None)
    except Exception as e:  # noqa: BLE001 - recorded, never skipped silently
        f["kind"] = "error"
        res["errors"].append({"step": "piee_file", "name": name, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    finally:
        if dest.exists():
            dest.unlink()
        if d is not None:
            try:
                d.delete()  # Playwright keeps its own copy until the browser closes
            except Exception:  # noqa: BLE001
                pass
    time.sleep(DOWNLOAD_PAUSE)
    return f


def read_page(pg, version: str, posted: str | None, seen: set, tmp: Path, res: dict) -> dict:
    """Read the files on the open version page that a newer version did not already have."""
    listed = page_files(pg)
    new, dup = new_names([n for n, _, _ in listed], seen)
    info = {"version": version, "posted": posted, "listed": len(listed), "new": len(new), "duplicates": len(dup)}
    want = set(new)
    for name, date, a in listed:
        if name not in want:
            continue
        want.discard(name)  # a name listed twice on one page is read once
        f = read_file(pg, name, a, tmp, len(res["files"]), res)
        f.update(piee_version=version, piee_version_posted=posted, piee_date=date)
        res["files"].append(f)
    return info


def fetch_notice(pg, ids: list[str], tmp: Path) -> dict:
    res = {"status": "not_found", "files": [], "errors": [], "searched": [], "versions": []}
    link = row = None
    for pid in ids:
        res["searched"].append(pid)
        link, row, n = search(pg, pid)
        if link is not None:
            if n > 1:
                res["errors"].append({"step": "piee_search", "name": pid,
                                      "error": f"{n} result rows matched; used the first"})
            break
        time.sleep(NOTICE_PAUSE)
    res["status"] = status_from_row(row)
    if row:
        res["piee_row"] = {k: row.get(k) for k in ("Notice ID", "Notice Type", "Restricted", "Status",
                                                    "PIEE Posted Date", "Number of Updates") if k in row}
    if res["status"] != "ok":
        return res
    time.sleep(PAGE_PAUSE)
    link.click()
    pg.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    if looks_like_login(pg):
        raise StopRun(f"PIEE showed a login page at {pg.url}")
    seen: set = set()
    older = order_versions(history_rows(pg))
    res["versions"].append(read_page(pg, "latest", (row or {}).get("PIEE Posted Date"), seen, tmp, res))
    for v in older:
        time.sleep(PAGE_PAUSE)
        try:
            open_version(pg, v["version"])
        except StopRun:
            raise
        except Exception as e:  # noqa: BLE001
            res["errors"].append({"step": "piee_version", "name": v["version"], "error": f"{type(e).__name__}: {str(e)[:200]}"})
            res["versions"].append({"version": v["version"], "posted": v["posted"], "opened": False})
            continue
        if res["versions"][0]["version"] == "latest":
            name_latest(res, [r["version"] for r in older], [r["version"] for r in history_rows(pg)])
        res["versions"].append(dict(read_page(pg, v["version"], v["posted"], seen, tmp, res), opened=True))
    res["versions"][0]["opened"] = True
    return res


def name_latest(res: dict, older: list[str], seen_from_older_page: list[str]) -> None:
    """The latest version's own label only shows up in the other versions' history tables."""
    extra = [x for x in seen_from_older_page if x not in older]
    if len(extra) == 1:
        res["versions"][0]["version"] = extra[0]
        for f in res["files"]:
            if f.get("piee_version") == "latest":
                f["piee_version"] = extra[0]


def load(path: Path) -> dict:
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("week_csv")
    ap.add_argument("--text", default="work/text")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="file of notice ids to do (one per line)")
    ap.add_argument("--retry-errors", action="store_true", help="redo notices whose piee status is error")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=None,
                    help="stop starting new notices after this long; the rest are counted as not reached")
    a = ap.parse_args(argv)
    text = Path(a.text)
    rows = list(csv.DictReader(open(a.week_csv)))
    if a.only:
        want = set(Path(a.only).read_text().split())
        rows = [r for r in rows if r["notice_id"] in want]
    records = {}
    for r in rows:
        p = text / f"{r['notice_id']}.json.gz"
        if p.exists():
            records[r["notice_id"]] = load(p)
    todo = choose(rows, records, a.retry_errors)
    # solicitations first: when the time budget runs out, sources-sought notices (fewer fields) are what's left
    kind = {r["notice_id"]: r.get("type") for r in rows}
    todo.sort(key=lambda t: kind.get(t[0]) != "Solicitation")
    if a.limit:
        todo = todo[:a.limit]
    print(json.dumps({"week_rows": len(rows), "with_record": len(records), "to_do": len(todo)}), flush=True)

    from playwright.sync_api import sync_playwright

    counts: dict[str, int] = {}
    files_read = chars = 0
    unreachable = 0
    stopped = None
    per_notice = []
    not_reached = 0
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="piee_dl_") as td, sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not a.headed)
        ctx = browser.new_context(accept_downloads=True)
        pg = ctx.new_page()
        try:
            for n, (nid, ids) in enumerate(todo, 1):
                if a.max_minutes and (time.time() - t0) / 60 > a.max_minutes:
                    not_reached = len(todo) - n + 1
                    break
                if n > 1:
                    time.sleep(NOTICE_PAUSE)
                t1 = time.time()
                try:
                    res = fetch_notice(pg, ids, Path(td))
                    unreachable = 0
                except StopRun as e:
                    stopped = str(e)
                    break
                except SearchPageDown as e:
                    unreachable += 1
                    print(json.dumps({"notice_id": nid, "search_page_down": str(e), "in_a_row": unreachable}), flush=True)
                    if unreachable >= MAX_UNREACHABLE:
                        stopped = f"PIEE search page did not load for {unreachable} notices in a row; last: {e}"
                        break
                    continue  # not marked, so a later run retries it
                except Exception as e:  # noqa: BLE001
                    res = {"status": "error", "files": [], "searched": ids,
                           "errors": [{"step": "piee", "name": ids[0], "error": f"{type(e).__name__}: {str(e)[:200]}"}]}
                res["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                rec = merge(records[nid], res)
                write_record(text / f"{nid}.json.gz", rec)
                counts[rec["piee"]["status"]] = counts.get(rec["piee"]["status"], 0) + 1
                files_read += rec["piee"]["read"]
                c = text_chars(res["files"])
                chars += c
                line = {"n": n, "of": len(todo), "notice_id": nid, "searched": ids[0], **{k: rec["piee"][k] for k in ("status", "listed", "read")},
                        "chars": c, "errors": len(res["errors"]), "seconds": round(time.time() - t1, 1)}
                per_notice.append(line)
                print(json.dumps(line), flush=True)
        finally:
            ctx.close()
            browser.close()
    summary = {"to_do": len(todo), "done": sum(counts.values()), "by_status": counts, "files_read": files_read,
               "text_chars_added": chars, "minutes": round((time.time() - t0) / 60, 2), "not_reached": not_reached}
    if stopped:
        summary["stopped"] = stopped
    print(json.dumps(summary), flush=True)
    if stopped:
        print(f"STOPPED: {stopped}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
