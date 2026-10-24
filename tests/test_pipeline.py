"""Offline tests: fake OpenAPI doc + fake API pages, run the full collect → build pipeline.

    python tests/test_pipeline.py      (or: pytest tests)
"""

import csv
import gzip
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="kpo-test-")
os.environ["OBSERVATORY_DATA"] = TMP
os.environ["DATA_GO_KR_KEY"] = "abc%2Bdef%3D%3D"  # an 'Encoding' key
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))

import collect  # noqa: E402
from common import clean_postal, normalize_rows, sido_of  # noqa: E402

U23 = "uddi:cea8854d-35c4-4a7f-a100-f241ea289d76"
U26 = "uddi:933fdcfa-7965-42c1-bc1a-89b976b276db"

OAS = {
    "swagger": "2.0",
    "paths": {
        f"/15070368/v1/{U23}": {"get": {"summary": "과학기술정보통신부 우정사업본부_전국 우체국 현황_20230630"}},
        f"/15070368/v1/{U26}": {"get": {"summary": "과학기술정보통신부 우정사업본부_전국 우체국 현황_20260831"}},
    },
}

ROWS_2023 = [
    {"지방우정청": "서울지방우정청", "총괄국명": "광화문우체국", "우체국명": "광화문우체국", "우편번호": "03154",
     "주소(지번)": "서울특별시 종로구 세종로 21-1", "주소(도로명)": "서울특별시 종로구 종로 6", "전화번호": "02-1111-1111"},
    {"지방우정청": "부산지방우정청", "총괄국명": "해운대우체국", "우체국명": "해운대우체국", "우편번호": "48094",
     "주소(지번)": "부산광역시 해운대구 중동 1", "주소(도로명)": "부산광역시 해운대구 중동로 1", "전화번호": "051-222-2222"},
    {"지방우정청": "경인지방우정청", "총괄국명": "수원우체국", "우체국명": "옛이름우체국", "우편번호": "16455",
     "주소(지번)": "경기도 수원시 팔달구 1", "주소(도로명)": "경기도 수원시 팔달구 효원로 1", "전화번호": "031-333-3333"},
    {"지방우정청": "강원지방우정청", "총괄국명": "춘천우체국", "우체국명": "폐국우체국", "우편번호": "24200",
     "주소(지번)": "강원도 춘천시 1", "주소(도로명)": "강원도 춘천시 중앙로 1", "전화번호": "033-444-4444"},
]

# 2026 model: no 주소(지번), 우편번호 typed as integer (leading zero lost).
ROWS_2026 = [
    {"지방우정청": "서울지방우정청", "총괄국명": "광화문우체국", "우체국명": "광화문우체국", "우편번호": 3154,
     "주소(도로명)": "서울특별시 종로구 종로 6", "전화번호": "02-1111-9999"},
    {"지방우정청": "부산지방우정청", "총괄국명": "해운대우체국", "우체국명": "해운대우체국", "우편번호": 48094,
     "주소(도로명)": "부산광역시  해운대구 중동로 1 ", "전화번호": "051-222-2222"},
    {"지방우정청": "경인지방우정청", "총괄국명": "수원우체국", "우체국명": "새이름우체국", "우편번호": 16455,
     "주소(도로명)": "경기도 수원시 팔달구 효원로 1", "전화번호": "031-333-3333"},
    {"지방우정청": "제주지방우정청", "총괄국명": "제주우체국", "우체국명": "신설우체국", "우편번호": 63100,
     "주소(도로명)": "제주특별자치도 제주시 관덕로 1", "전화번호": "064-555-5555"},
]

DATA_BY_UDDI = {U23: ROWS_2023, U26: ROWS_2026}
SEEN_KEYS = []


class FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, json.dumps(body, ensure_ascii=False)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def fake_get(url, params=None, tries=4):
    if "oas/docs" in url:
        return FakeResp(OAS)
    SEEN_KEYS.append(params["serviceKey"])
    uddi = url.rsplit("/", 1)[-1]
    rows = DATA_BY_UDDI[uddi]
    page, per = params["page"], params["perPage"]
    chunk = rows[(page - 1) * per: page * per]
    return FakeResp({"page": page, "perPage": per, "totalCount": len(rows),
                     "currentCount": len(chunk), "matchCount": len(rows), "data": chunk})


collect.get = fake_get
collect.PER_PAGE = 3  # force pagination
collect.time.sleep = lambda s: None


def run(*argv):
    sys.argv = ["collect.py", *argv]
    assert collect.main() == 0


def read(name):
    with open(Path(TMP) / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_helpers():
    assert clean_postal(3154) == "03154"
    assert clean_postal("6236.0") == "06236"
    assert clean_postal("") == ""
    rows, cols = normalize_rows([{" 관할청 ": "서울청", "우체국 명칭": "A", "도로명주소": "서울 종로구 1", "비고": "x"}])
    assert rows[0]["지방우정청"] == "서울청" and rows[0]["우체국명"] == "A" and cols[-1] == "비고"
    assert sido_of({"주소(도로명)": "강원특별자치도 춘천시"}) == "강원"
    assert sido_of({"주소(도로명)": "전북특별자치도 전주시"}) == "전북"


def test_pipeline():
    run()
    assert SEEN_KEYS and set(SEEN_KEYS) == {"abc+def=="}, SEEN_KEYS  # encoding key decoded once
    index = read("snapshots/index.csv")
    assert [r["기준일"] for r in index] == ["20230630", "20260831"]
    assert [r["rows"] for r in index] == ["4", "4"]

    snap = read("snapshots/20260831.csv")
    assert {r["우편번호"] for r in snap} >= {"03154"}
    with gzip.open(Path(TMP) / "raw/20260831.json.gz", "rt", encoding="utf-8") as f:
        assert len(json.load(f)) == 4

    log = [r for r in read("changelog.csv") if r["기준일"] == "20260831"]
    kinds = sorted((r["변경"], r["우체국명"], r["항목"]) for r in log)
    assert kinds == [
        ("변경", "광화문우체국", "전화번호"),
        ("변경", "새이름우체국", "우체국명"),
        ("삭제", "폐국우체국", ""),
        ("추가", "신설우체국", ""),
    ], kinds
    rename = next(r for r in log if r["항목"] == "우체국명")
    assert rename["이전값"] == "옛이름우체국" and rename["새값"] == "새이름우체국"

    sido = {(r["기준일"], r["시도"]): r["우체국수"] for r in read("stats/by_sido.csv")}
    assert sido[("20260831", "제주")] == "1" and ("20260831", "강원") not in sido

    # Re-running with identical data changes nothing.
    before = (Path(TMP) / "snapshots/index.csv").read_text(encoding="utf-8")
    run("--refresh")
    assert (Path(TMP) / "snapshots/index.csv").read_text(encoding="utf-8") == before

    # A silent re-upload with corrected data is recorded as a revision.
    ROWS_2026[1] = {**ROWS_2026[1], "전화번호": "051-222-0000"}
    run("--refresh")
    latest = read("snapshots/index.csv")[-1]
    assert latest["revisions"] == "1", latest

    # The portal dropping an old file is remembered; our snapshot stays.
    del OAS["paths"][f"/15070368/v1/{U23}"]
    run()
    src = json.loads((Path(TMP) / "sources.json").read_text(encoding="utf-8"))
    old = next(s for s in src if s["기준일"] == "20230630")
    assert old["on_portal"] is False and (Path(TMP) / "snapshots/20230630.csv").exists()


if __name__ == "__main__":
    test_helpers()
    test_pipeline()
    print("all tests passed")
