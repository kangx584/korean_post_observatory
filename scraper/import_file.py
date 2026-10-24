"""Import a CSV downloaded by hand from data.go.kr (the "CSV 다운로드" button).

Use this when the API is unavailable, or to add an older file that the portal
no longer lists as an API endpoint.

    python scraper/import_file.py 과학기술정보통신부_우정사업본부_전국_우체국_현황_20260831.csv
    python scraper/import_file.py some.csv --date 20250630

The 기준일 is read from the file name (8 digits) unless --date is given.
Portal CSVs are usually CP949; UTF-8 also works.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

import build
from collect import store


def read_any(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"Could not decode {path} as UTF-8 or CP949")
    return list(csv.DictReader(io.StringIO(text)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--date", help="기준일 YYYYMMDD (only with a single file)")
    args = ap.parse_args()
    if args.date and len(args.files) > 1:
        raise SystemExit("--date works with one file at a time")

    for path in args.files:
        date = args.date
        if not date:
            found = re.findall(r"(20\d{6})", path.name)
            if not found:
                raise SystemExit(f"No YYYYMMDD in {path.name}; pass --date")
            date = found[-1]
        records = read_any(path)
        status = store({"기준일": date, "uddi": "", "title": path.name}, records, source="manual")
        print(f"{date}: {len(records)} rows from {path.name} — {status}")
    build.build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
