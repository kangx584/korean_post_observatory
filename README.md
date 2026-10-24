# Korean Post Observatory

전국 우체국 현황(과학기술정보통신부 우정사업본부)을 시점별로 보관하고, 우체국의 추가·삭제·변경을 추적하는 저장소입니다.

- 원천 데이터: 공공데이터포털 [전국 우체국 현황 (15070368)](https://www.data.go.kr/data/15070368/fileData.do)
- 수집 경로: 포털이 업로드 파일마다 자동 생성하는 Open API (`api.odcloud.kr`)

GitHub Actions가 매일 포털을 확인하고, **새 파일이 올라왔을 때만** 내려받아 커밋합니다. `data/`의 git 이력 자체가 변경 기록입니다.

## 왜 직접 보관하나

포털은 파일을 새로 올릴 때 예전 파일을 내리기도 합니다. 2026년 10월 기준으로 API에 남아 있는 파일은 `20230630`과 `20260831` 두 개뿐이고, 그 사이 버전은 확인되지 않습니다. 이 저장소는 포털에서 사라진 버전도 계속 보관합니다(`data/sources.json`의 `on_portal: false`).

## 데이터

| 파일 | 내용 |
|---|---|
| `data/post_offices.csv` | 최신 기준일의 우체국 목록 |
| `data/snapshots/YYYYMMDD.csv` | 기준일별 전체 목록 (UTF-8, 표준 컬럼) |
| `data/snapshots/index.csv` | 스냅샷마다 한 행: 기준일, uddi, 원본 제목, 행 수, 해시, 수집 경로(`api`/`manual`), 수집 시각, 재업로드 반영 횟수 |
| `data/raw/YYYYMMDD.json.gz` | API가 돌려준 원본 행 |
| `data/changelog.csv` | 연속한 두 스냅샷 사이의 변경 (`기준일`, `이전기준일`, `변경`, `지방우정청`, `우체국명`, `항목`, `이전값`, `새값`) |
| `data/stats/by_region.csv` | 기준일 × 지방우정청 우체국 수 |
| `data/stats/by_sido.csv` | 기준일 × 시도 우체국 수 (도로명주소 첫 단어 기준) |
| `data/sources.json` | 지금까지 확인된 모든 API 엔드포인트와 포털 게시 여부 |
| `data/meta.json` | 마지막 확인 시각, 포털 파일 목록, 최신 기준일과 행 수 |

**표준 컬럼:** `지방우정청, 총괄국명, 우체국명, 우편번호, 주소(도로명), 주소(지번), 전화번호`
- 2026년 파일에는 `주소(지번)`이 없어서 빈 칸입니다.
- 2026년 API는 우편번호를 정수로 내보내 서울(0으로 시작) 우편번호의 앞자리 0이 빠집니다. 여기서는 5자리로 복원합니다.

**changelog 읽는 법**
- `기준`: 가장 오래된 스냅샷에 있던 우체국
- `추가` / `삭제`: 이전 스냅샷에 없던 / 있던 우체국. 실제 신설·폐국일 수도 있지만 목록 작성 방식이 바뀐 경우일 수도 있습니다. 변경은 **두 기준일 사이 어느 시점**에 일어난 것입니다.
- `변경`: 같은 우체국으로 판단된 행의 값이 달라진 경우. 항목별로 한 행씩 기록합니다.
- 공식 우체국 ID가 없으므로 같은 우체국인지는 ① (지방우정청, 우체국명) ② 우체국명 ③ 도로명주소 순서로 맞춥니다. 같은 주소에서 이름만 바뀌면 `항목=우체국명` 변경으로 잡힙니다.
- 두 파일에 모두 있는 컬럼만 비교합니다.

## 설정 (처음 한 번)

1. 공공데이터포털에서 이 데이터의 **활용신청**을 하고 인증키를 받습니다. 개발계정은 보통 바로 승인됩니다.
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DATA_GO_KR_KEY`
   - Secret: 마이페이지의 일반 인증키. Encoding/Decoding 어느 쪽이든 됩니다.
3. **Actions → Check post office dataset → Run workflow**를 실행하면 현재 포털에 있는 파일이 모두 보관됩니다.

인증키가 없어도 매일 확인은 돌아갑니다(목록 확인에는 키가 필요 없음). 새 파일을 내려받아야 할 때만 키가 없으면 실패하고, GitHub가 메일로 알려줍니다.

**활용기간:** 활용신청 승인은 보통 2년이며 마이페이지에서 연장할 수 있습니다. 만료되면 다운로드 단계가 `API refused the key` 오류로 실패하니, 그때 연장하면 됩니다.

## 동작 방식

- `scraper/collect.py`
  1. `https://infuser.odcloud.kr/oas/docs?namespace=15070368/v1`(OpenAPI 문서, 키 불필요)에서 `uddi:` 엔드포인트 목록과 제목의 기준일(YYYYMMDD)을 읽습니다.
  2. 아직 보관하지 않은 기준일만 API로 전부 받습니다(1,000행씩 페이지 처리, `totalCount`와 행 수가 다르면 실패).
  3. `--refresh`는 최신 파일도 다시 받아, 같은 이름으로 내용만 고쳐 재업로드한 경우를 잡습니다. 내용이 같으면 아무것도 바꾸지 않습니다.
- `scraper/build.py`: 스냅샷에서 changelog, 통계, `post_offices.csv`를 다시 만듭니다.
- `.github/workflows/collect.yml`: 매일 01:23 UTC(10:23 KST)에 실행하고 월요일에는 `--refresh`를 붙입니다. 수동 실행 시 `new` / `refresh` / `refresh-all`을 고를 수 있습니다. 변경이 없으면 커밋하지 않고, 매월 1일에만 `meta.json`을 커밋합니다(60일 동안 활동이 없으면 GitHub가 예약 실행을 멈추기 때문).

## 손으로 받은 CSV 넣기

API가 막히거나, 포털에서 이미 내려간 예전 파일을 다른 곳에서 구했을 때:

```bash
python scraper/import_file.py "과학기술정보통신부 우정사업본부_전국 우체국 현황_20250630.csv"
python scraper/import_file.py some.csv --date 20250630
```

기준일은 파일 이름의 8자리 숫자에서 읽습니다. 포털 CSV의 CP949 인코딩도 자동으로 처리합니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python tests/test_pipeline.py                 # 오프라인 테스트
DATA_GO_KR_KEY=... python scraper/collect.py  # 실제 수집
```

## 알려진 위험

- GitHub Actions 러너는 해외 IP입니다. 포털이 해외 접속을 막으면 수집 단계가 연결 오류로 실패합니다. 그럴 때는 국내 PC에서 로컬로 실행하거나 self-hosted runner를 쓰세요.
- 포털이 OpenAPI 문서 형식이나 파일 제목 규칙(끝의 YYYYMMDD)을 바꾸면 수집이 잘못된 데이터를 쓰지 않고 실패하도록 해 두었습니다.

## 라이선스

원천 데이터는 공공데이터포털 이용허락범위 "제한 없음"입니다. 출처: 과학기술정보통신부 우정사업본부.
