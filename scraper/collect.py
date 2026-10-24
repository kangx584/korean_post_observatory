"""Korean Post Observatory collector.

1. Reads the dataset's OpenAPI document (no key needed) to list every file the
   portal currently exposes as an API endpoint (one `uddi:` per uploaded file).
2. Downloads any file we have not archived yet through api.odcloud.kr
   (needs the DATA_GO_KR_KEY service key).
3. With --refresh (the workflow does this weekly), re-downloads the newest file
   too, because the portal sometimes re-uploads corrections under the same name.
4. Rebuilds the changelog and stats when anything changed.

Writes:
  data/snapshots/YYYYMMDD.csv   normalized table per 기준일 (UTF-8)
  data/snapshots/index.csv      one row per snapshot
  data/raw/YYYYMMDD.json.gz     raw API rows as received
  data/sources.json             every endpoint ever seen, incl. ones the portal later removed
  data/meta.json                last check
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import time
import urllib.parse

import requests

import build
from common import (API_BASE, CHANGELOG_CSV, INDEX_CSV, META_JSON, OAS_URL, RAW, SNAPSHOTS, SOURCES_JSON,
                    normalize_rows, read_csv, read_json, rows_to_csv, sha256, write_csv,
                    write_gzip_json, write_json)

INDEX_COLUMNS = ["기준일", "uddi", "title", "rows", "sha256", "source",
                 "first_fetched_utc", "last_fetched_utc", "revisions"]
PER_PAGE = 1000
HEADERS = {"User-Agent": "korean-post-observatory (+https://github.com)", "Accept": "application/json"}
UDDI_RE = re.compile(r"(uddi:[0-9a-fA-F-]{36})")
DATE_RE = re.compile(r"(20\d{6})")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def get(url: str, params: dict | None = None, tries: int = 4) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=60)
            if resp.status_code < 500:
                return resp
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {tries} tries: {last}")


# --- discovery -----------------------------------------------------------------

def parse_oas(doc: dict) -> list[dict]:
    """Extract (uddi, title, 기준일) from a Swagger 2 / OpenAPI 3 document."""
    out = []
    for path, ops in (doc.get("paths") or {}).items():
        m = UDDI_RE.search(path)
        if not m:
            continue
        op = (ops or {}).get("get") or {}
        title = op.get("summary") or op.get("description") or ""
        dates = DATE_RE.findall(title)
        out.append({"uddi": m.group(1), "path": path, "title": title,
                    "기준일": dates[-1] if dates else ""})
    out.sort(key=lambda e: (e["기준일"], e["uddi"]))
    return out


def discover() -> list[dict]:
    resp = get(OAS_URL)
    resp.raise_for_status()
    endpoints = parse_oas(resp.json())
    if not endpoints:
        raise RuntimeError("OpenAPI document listed no uddi endpoints — layout changed?")
    for e in endpoints:
        if not e["기준일"]:
            raise RuntimeError(f"No YYYYMMDD date in endpoint title {e['title']!r}")
    return endpoints


# --- download --------------------------------------------------------------------

def service_key() -> str:
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "DATA_GO_KR_KEY is not set. Add your data.go.kr service key as a repository secret "
            "(Settings → Secrets and variables → Actions → New repository secret).")
    # The portal shows an 'Encoding' and a 'Decoding' key; requests encodes for us,
    # so turn an already-encoded key back into the raw one.
    return urllib.parse.unquote(key) if "%" in key else key


def fetch_all(path: str, key: str) -> list[dict]:
    url = API_BASE + path
    rows: list[dict] = []
    page, total = 1, None
    while True:
        resp = get(url, {"page": page, "perPage": PER_PAGE, "serviceKey": key})
        if resp.status_code in (401, 403):
            raise RuntimeError(f"API refused the key (HTTP {resp.status_code}): {resp.text[:300]}")
        resp.raise_for_status()
        body = resp.json()
        if "data" not in body:
            raise RuntimeError(f"Unexpected API response: {str(body)[:300]}")
        total = body.get("totalCount", total)
        batch = body["data"]
        rows.extend(batch)
        if not batch or (total is not None and len(rows) >= total):
            break
        page += 1
        time.sleep(0.3)
    if total is not None and len(rows) != total:
        raise RuntimeError(f"Got {len(rows)} rows but totalCount is {total}")
    return rows


# --- archive -------------------------------------------------------------------

def store(endpoint: dict, records: list[dict], source: str = "api") -> str:
    """Write snapshot + raw + index row. Returns 'new', 'revised' or 'same'."""
    date = endpoint["기준일"]
    rows, columns = normalize_rows(records)
    text = rows_to_csv(rows, columns)
    digest = sha256(text)

    index = {r["기준일"]: r for r in read_csv(INDEX_CSV)}
    prev = index.get(date)
    ts = now()
    if prev and prev["sha256"] == digest:
        return "same"  # identical content: touch nothing, so the run commits nothing
    else:
        snap = SNAPSHOTS / f"{date}.csv"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(text, encoding="utf-8")
        if source == "api":
            write_gzip_json(RAW / f"{date}.json.gz", records)
        index[date] = {
            "기준일": date, "uddi": endpoint.get("uddi", ""), "title": endpoint.get("title", ""),
            "rows": len(rows), "sha256": digest, "source": source,
            "first_fetched_utc": prev["first_fetched_utc"] if prev else ts,
            "last_fetched_utc": ts,
            "revisions": int(prev["revisions"]) + 1 if prev else 0,
        }
        status = "revised" if prev else "new"
    write_csv(INDEX_CSV, [index[k] for k in sorted(index)], INDEX_COLUMNS)
    return status


def update_sources(endpoints: list[dict]) -> list[dict]:
    """Remember every endpoint ever seen; flag ones the portal no longer lists."""
    known = {s["uddi"]: s for s in read_json(SOURCES_JSON, [])}
    ts = now()
    live = {e["uddi"] for e in endpoints}
    for e in endpoints:
        s = known.setdefault(e["uddi"], {**{k: e[k] for k in ("uddi", "title", "기준일")},
                                         "first_seen_utc": ts})
        s["title"] = e["title"]
        s["on_portal"] = True
        s.pop("gone_since_utc", None)
    for uddi, s in known.items():
        if uddi not in live and s.get("on_portal", True):
            s["on_portal"] = False
            s["gone_since_utc"] = ts
            print(f"::notice::Portal no longer lists {s['기준일']} ({uddi}); our snapshot is kept.")
    out = sorted(known.values(), key=lambda s: (s["기준일"], s["uddi"]))
    write_json(SOURCES_JSON, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-download the newest file even if archived")
    ap.add_argument("--refresh-all", action="store_true", help="re-download every listed file")
    args = ap.parse_args()

    endpoints = discover()
    print(f"Portal lists {len(endpoints)} file(s): " + ", ".join(e["기준일"] for e in endpoints))
    update_sources(endpoints)

    archived = {r["기준일"] for r in read_csv(INDEX_CSV)}
    todo = [e for e in endpoints if e["기준일"] not in archived]
    if args.refresh_all:
        todo = endpoints
    elif args.refresh and endpoints[-1] not in todo:
        todo.append(endpoints[-1])

    changed = False
    if todo:
        key = service_key()
        for e in todo:
            records = fetch_all(e["path"], key)
            status = store(e, records)
            print(f"{e['기준일']}: {len(records)} rows — {status}")
            changed |= status != "same"
    else:
        print("Nothing new on the portal.")

    if changed or (INDEX_CSV.exists() and not CHANGELOG_CSV.exists()):
        build.build()

    index = read_csv(INDEX_CSV)
    latest = index[-1] if index else {}
    meta = read_json(META_JSON, {})
    meta.update({
        "dataset_page": "https://www.data.go.kr/data/15070368/fileData.do",
        "openapi_doc": OAS_URL,
        "last_checked_utc": now(),
        "portal_files": [e["기준일"] for e in endpoints],
        "archived_snapshots": [r["기준일"] for r in index],
        "latest_기준일": latest.get("기준일"),
        "latest_rows": int(latest["rows"]) if latest else None,
    })
    if changed:
        meta["last_changed_utc"] = meta["last_checked_utc"]
    write_json(META_JSON, meta)

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
            f.write(f"latest={latest.get('기준일', '')}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"::error::{e}")
        sys.exit(1)
