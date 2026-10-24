"""Rebuild derived files from data/snapshots/*.csv.

  data/post_offices.csv        the latest snapshot (copy)
  data/changelog.csv           row-level changes between consecutive snapshots
  data/stats/by_region.csv     office counts per 지방우정청 per snapshot
  data/stats/by_sido.csv       office counts per 시도 per snapshot

Matching offices across snapshots (there is no official office ID):
  1. same (지방우정청, 우체국명)      — when unique on both sides
  2. same 우체국명                    — catches a change of 지방우정청
  3. same 주소(도로명), after normalizing 시도 renames (강원도→강원특별자치도,
     광주/전남→전남광주통합특별시), status tags and (동) notes — catches a rename
  4. same 지방우정청 + name without its type suffix — catches 우체국→우편취급국
Address fields count as changed only when that normalized form differs.
Anything left over is 추가 (added) or 삭제 (removed).
"""

from __future__ import annotations

import re
from collections import Counter

from common import (CHANGELOG_CSV, COLUMNS, CURRENT_CSV, SIDO_ORDER, SNAPSHOTS, STATS,
                    canon_addr, read_csv, sido_of, write_csv)

SNAP_RE = re.compile(r"^\d{8}\.csv$")
CHANGELOG_COLUMNS = ["기준일", "이전기준일", "변경", "지방우정청", "우체국명", "항목", "이전값", "새값"]


def snapshot_dates() -> list[str]:
    return sorted(p.stem for p in SNAPSHOTS.glob("*.csv") if SNAP_RE.match(p.name))


def load(date: str) -> list[dict]:
    return read_csv(SNAPSHOTS / f"{date}.csv")


TYPE_SUFFIX = re.compile(r"(우편취급국|우편출장소|우체국|취급국|출장소)$")


def _addr_key(row: dict) -> str:
    return canon_addr(row.get("주소(도로명)", ""))


def _stem_key(row: dict) -> tuple[str, str]:
    """'의성가음우체국' and '의성가음우편취급국' share a stem: catches a change of office type."""
    return (row["지방우정청"], TYPE_SUFFIX.sub("", row["우체국명"]))


def _same(field: str, a: str, b: str) -> bool:
    if field in ("주소(도로명)", "주소(지번)"):
        return canon_addr(a) == canon_addr(b)  # ignore 시도 renames and formatting
    return a == b


def _match(old: list[dict], new: list[dict], keyfn) -> list[tuple[dict, dict]]:
    """Pair rows whose key is non-empty and unique on both sides; remove them from the lists."""
    oc = Counter(keyfn(r) for r in old)
    nc = Counter(keyfn(r) for r in new)
    new_by_key = {keyfn(r): r for r in new if nc[keyfn(r)] == 1}
    pairs = []
    for r in list(old):
        k = keyfn(r)
        if k and oc[k] == 1 and k in new_by_key:
            pairs.append((r, new_by_key[k]))
    for o, n in pairs:
        old.remove(o)
        new.remove(n)
    return pairs


def _populated(rows: list[dict]) -> set[str]:
    return {c for c in COLUMNS if any(r.get(c) for r in rows)}


def diff(old_rows: list[dict], new_rows: list[dict]) -> list[dict]:
    """Return changelog rows (without the date columns)."""
    old, new = list(old_rows), list(new_rows)
    pairs = []
    pairs += _match(old, new, lambda r: (r["지방우정청"], r["우체국명"]))
    pairs += _match(old, new, lambda r: r["우체국명"])
    pairs += _match(old, new, _addr_key)
    pairs += _match(old, new, _stem_key)

    # Only compare fields both snapshots actually carry (2026 dropped 주소(지번)).
    fields = [c for c in COLUMNS if c in _populated(old_rows) and c in _populated(new_rows)]
    out = []
    for o, n in pairs:
        for f in fields:
            if not _same(f, o.get(f, ""), n.get(f, "")):
                out.append({"변경": "변경", "지방우정청": n["지방우정청"], "우체국명": n["우체국명"],
                            "항목": f, "이전값": o.get(f, ""), "새값": n.get(f, "")})
    for r in old:
        out.append({"변경": "삭제", "지방우정청": r["지방우정청"], "우체국명": r["우체국명"],
                    "항목": "", "이전값": r.get("주소(도로명)", ""), "새값": ""})
    for r in new:
        out.append({"변경": "추가", "지방우정청": r["지방우정청"], "우체국명": r["우체국명"],
                    "항목": "", "이전값": "", "새값": r.get("주소(도로명)", "")})
    order = {"추가": 0, "삭제": 1, "변경": 2}
    out.sort(key=lambda c: (order[c["변경"]], c["지방우정청"], c["우체국명"], c["항목"]))
    return out


def build() -> dict:
    dates = snapshot_dates()
    if not dates:
        print("No snapshots yet.")
        return {}

    changelog: list[dict] = []
    first = load(dates[0])
    for r in first:
        changelog.append({"기준일": dates[0], "이전기준일": "", "변경": "기준",
                          "지방우정청": r["지방우정청"], "우체국명": r["우체국명"],
                          "항목": "", "이전값": "", "새값": r.get("주소(도로명)", "")})
    summary = {}
    prev_date, prev = dates[0], first
    for d in dates[1:]:
        cur = load(d)
        changes = diff(prev, cur)
        for c in changes:
            changelog.append({"기준일": d, "이전기준일": prev_date, **c})
        summary[d] = Counter(c["변경"] for c in changes)
        prev_date, prev = d, cur
    write_csv(CHANGELOG_CSV, changelog, CHANGELOG_COLUMNS)

    latest = load(dates[-1])
    cols = list(latest[0].keys()) if latest else COLUMNS
    write_csv(CURRENT_CSV, latest, cols)

    by_region, by_sido = [], []
    for d in dates:
        rows = load(d)
        for region, n in sorted(Counter(r["지방우정청"] for r in rows).items()):
            by_region.append({"기준일": d, "지방우정청": region, "우체국수": n})
        sc = Counter(sido_of(r) for r in rows)
        for s in SIDO_ORDER + sorted(k for k in sc if k not in SIDO_ORDER):
            if sc.get(s):
                by_sido.append({"기준일": d, "시도": s, "우체국수": sc[s]})
    write_csv(STATS / "by_region.csv", by_region, ["기준일", "지방우정청", "우체국수"])
    write_csv(STATS / "by_sido.csv", by_sido, ["기준일", "시도", "우체국수"])

    for d, c in summary.items():
        print(f"{d}: 추가 {c.get('추가', 0)}, 삭제 {c.get('삭제', 0)}, 변경 {c.get('변경', 0)}")
    return summary


if __name__ == "__main__":
    build()
