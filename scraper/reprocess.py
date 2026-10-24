"""Re-normalize every snapshot from data/raw/*.json.gz after a change to the
normalization rules, then rebuild the changelog and stats.

Only the snapshot CSVs and their sha256 in index.csv change; revisions are not
bumped, because the portal's data did not change.  Manually imported snapshots
(no raw file) are re-normalized from their own CSV.

    python scraper/reprocess.py
"""

from __future__ import annotations

import gzip
import json

import build
from common import INDEX_CSV, RAW, SNAPSHOTS, normalize_rows, read_csv, rows_to_csv, sha256, write_csv
from collect import INDEX_COLUMNS


def main() -> None:
    index = read_csv(INDEX_CSV)
    for row in index:
        date = row["기준일"]
        raw = RAW / f"{date}.json.gz"
        if raw.exists():
            with gzip.open(raw, "rt", encoding="utf-8") as f:
                records = json.load(f)
        else:
            records = read_csv(SNAPSHOTS / f"{date}.csv")
        rows, columns = normalize_rows(records)
        text = rows_to_csv(rows, columns)
        (SNAPSHOTS / f"{date}.csv").write_text(text, encoding="utf-8")
        row["sha256"] = sha256(text)
        print(f"{date}: {len(rows)} rows re-normalized")
    write_csv(INDEX_CSV, index, INDEX_COLUMNS)
    build.build()


if __name__ == "__main__":
    main()
