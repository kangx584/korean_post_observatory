"""Shared paths, normalization and CSV helpers for the Korean Post Observatory."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("OBSERVATORY_DATA", ROOT / "data"))  # tests point this at a temp dir
SNAPSHOTS = DATA / "snapshots"
RAW = DATA / "raw"
STATS = DATA / "stats"
INDEX_CSV = SNAPSHOTS / "index.csv"
SOURCES_JSON = DATA / "sources.json"
CHANGELOG_CSV = DATA / "changelog.csv"
CURRENT_CSV = DATA / "post_offices.csv"
META_JSON = DATA / "meta.json"

NAMESPACE = "15070368/v1"
DATASET_PAGE = "https://www.data.go.kr/data/15070368/fileData.do"
OAS_URL = f"https://infuser.odcloud.kr/oas/docs?namespace={NAMESPACE}"
API_BASE = "https://api.odcloud.kr/api"

# Standard column order. The 2023 file has 주소(지번); the 2026 file does not.
# 상태 is derived: tags such as (임시청사), (운영중지), [시간제_오전] are moved out of the address into it.
COLUMNS = ["지방우정청", "총괄국명", "우체국명", "우편번호", "주소(도로명)", "주소(지번)", "전화번호", "상태"]

# Header spellings seen (or likely) in API models and hand-downloaded CSVs.
HEADER_ALIASES = {
    "지방우정청": "지방우정청", "관할우정청": "지방우정청", "관할청": "지방우정청", "지방청": "지방우정청", "우정청": "지방우정청",
    "총괄국명": "총괄국명", "총괄국": "총괄국명",
    "우체국명": "우체국명", "우체국명칭": "우체국명", "우체국": "우체국명", "관서명": "우체국명", "국명": "우체국명",
    "우편번호": "우편번호",
    "주소(도로명)": "주소(도로명)", "도로명주소": "주소(도로명)", "주소(도로명주소)": "주소(도로명)",
    "소재지주소": "주소(도로명)", "소재지": "주소(도로명)", "주소": "주소(도로명)",
    "주소(지번)": "주소(지번)", "지번주소": "주소(지번)", "주소(지번주소)": "주소(지번)",
    "전화번호": "전화번호", "전화": "전화번호", "대표전화": "전화번호",
}

_WS = re.compile(r"\s+")


def canon_header(name: str) -> str:
    key = _WS.sub("", str(name or "")).replace("﻿", "")
    return HEADER_ALIASES.get(key, key)


def clean(value) -> str:
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).strip()


def clean_postal(value) -> str:
    """The 2026 API model types 우편번호 as integer, so 06xxx codes lose their leading zero."""
    s = clean(value)
    if s.endswith(".0"):
        s = s[:-2]
    digits = re.sub(r"\D", "", s)
    if 3 <= len(digits) <= 5:
        return digits.zfill(5)
    return digits or s


_LEAD_TAG = re.compile(r"^\s*\(([^)]*)\)\s*")
_BRACKET_TAG = re.compile(r"\s*\[([^\]]*)\]\s*")
STATUS_WORDS = ("임시청사", "운영중지", "업무중지", "업무개시", "기존", "휴국", "폐국", "이전", "신축")


def split_status(addr: str) -> tuple[str, list[str]]:
    """'(임시청사) 강원도 ... [시간제_오전]' -> ('강원도 ...', ['임시청사', '시간제_오전'])."""
    tags = []
    while True:
        m = _LEAD_TAG.match(addr)
        if not m or not any(w in m.group(1) for w in STATUS_WORDS):
            break
        tags.append(m.group(1).strip())
        addr = addr[m.end():]
    for t in _BRACKET_TAG.findall(addr):
        tags.append(t.strip())
    addr = _BRACKET_TAG.sub(" ", addr)
    return clean(addr), tags


def normalize_rows(records: list[dict]) -> tuple[list[dict], list[str]]:
    """Map arbitrary source records onto the standard columns.

    Returns (rows, columns) where columns is COLUMNS followed by any unknown
    source columns (kept so nothing is silently dropped).
    """
    extra: list[str] = []
    rows = []
    for rec in records:
        row = {c: "" for c in COLUMNS}
        for k, v in rec.items():
            col = canon_header(k)
            if col not in COLUMNS and col not in extra:
                extra.append(col)
            row[col] = clean_postal(v) if col == "우편번호" else clean(v)
        tags: list[str] = []
        for col in ("주소(도로명)", "주소(지번)"):
            row[col], t = split_status(row[col])
            tags += [x for x in t if x not in tags]
        if tags and not row["상태"]:
            row["상태"] = "; ".join(tags)
        rows.append(row)
    columns = COLUMNS + extra
    for row in rows:
        for c in extra:
            row.setdefault(c, "")
    rows.sort(key=lambda r: (r["지방우정청"], r["총괄국명"], r["우체국명"], r["주소(도로명)"]))
    return rows, columns


def rows_to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in columns})
    return buf.getvalue()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rows_to_csv(rows, columns), encoding="utf-8")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_gzip_json(path: Path, obj) -> None:
    """Deterministic gzip (mtime=0) so an unchanged payload never shows up as a git change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(payload)


# --- 시도 normalization -------------------------------------------------------

SIDO = {
    "서울": "서울", "서울특별시": "서울", "서울시": "서울",
    "부산": "부산", "부산광역시": "부산",
    "대구": "대구", "대구광역시": "대구",
    "인천": "인천", "인천광역시": "인천",
    "광주": "광주", "광주광역시": "광주",
    "대전": "대전", "대전광역시": "대전",
    "울산": "울산", "울산광역시": "울산",
    "세종": "세종", "세종특별자치시": "세종", "세종시": "세종",
    "경기": "경기", "경기도": "경기",
    "강원": "강원", "강원도": "강원", "강원특별자치도": "강원",
    "충북": "충북", "충청북도": "충북",
    "충남": "충남", "충청남도": "충남",
    "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북",
    "전남": "전남", "전라남도": "전남",
    "경북": "경북", "경상북도": "경북",
    "경남": "경남", "경상남도": "경남",
    "제주": "제주", "제주특별자치도": "제주", "제주도": "제주",
    # 2026: 광주광역시 + 전라남도 → 전남광주통합특별시
    "전남광주": "전남광주", "전남광주통합특별시": "전남광주",
}
SIDO_ORDER = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
              "강원", "충북", "충남", "전북", "전남", "전남광주", "경북", "경남", "제주"]


def canon_addr(addr: str) -> str:
    """Address key that ignores 시도 renames (강원도→강원특별자치도 ...), status tags,
    a trailing (동) note and spacing/commas — for matching and for deciding whether
    an address really changed."""
    a, _ = split_status(addr or "")
    if not a:
        return ""
    first, _, rest = a.partition(" ")
    first = SIDO.get(first, first)
    if first in ("광주", "전남"):
        first = "전남광주"
    a = first + " " + rest
    a = re.sub(r"\([^)]*\)\s*$", "", a)
    return re.sub(r"[\s,]", "", a)


def sido_of(row: dict) -> str:
    for col in ("주소(도로명)", "주소(지번)"):
        addr = row.get(col, "")
        if addr:
            first = addr.split(" ", 1)[0]
            if first in SIDO:
                return SIDO[first]
    # e.g. '(영업과,FC실) 서울특별시 양천구 ...': take the first full 시도 name anywhere.
    addr = row.get("주소(도로명)", "")
    hits = [(addr.find(k), v) for k, v in SIDO.items() if len(k) > 2 and k in addr]
    return min(hits)[1] if hits else "미상"
