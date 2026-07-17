---
name: econ-fill
description: "증시 리뷰 시트 '2.경제 지표'가 '(발표 없음)'으로 잘못 비어 있을 때, 해당일 지표(미국 3성급 / 한·중·일)를 재수집해 시트에 기록. investing.com Cloudflare 403으로 파이프라인이 조용히 빈칸을 남기는 사고 복구용. '경제지표 안 채워졌어', '별3개 지표 넣어줘' 요청 시 사용. 호출: /econ-fill [YYYY-MM-DD] [KR|US]"
---

# 도담아빠 — 경제지표 재수집·기록 규약

`daily_review.py`가 investing.com 캘린더 수집에 실패하면 **실제 발표가 있어도** 시트에
`(전날 주요 지표 발표 없음)`을 박는다. 예외도 안 나고 워크플로우는 success로 끝난다.
이 스킬은 그 구멍을 사후에 메운다.

## 🚨 절대 원칙
- **빈 econ 섹션을 "진짜 발표 없음"으로 믿지 말 것.** 403과 진짜 0건은 시트에서 구분되지 않는다. **반드시 로그로 판정**(2단계).
- **지어내지 말 것.** 지표명·수치·예상치는 investing 소스에서 확인한 것만. 기억으로 채우지 않는다.
- **소스를 보고하라.** 파이프라인 API가 아닌 폴백(위젯)으로 받았으면 요약에 명시한다. 예상치·지표명이 API 원문과 다를 수 있다.

## 1. 입력 파싱
- 날짜: 인자 없으면 **어제**(= 오늘자 글로벌 시트의 대상일). 시장: `US`(기본) | `KR`.
- **시트↔대상일 매핑 주의**: 미국 `2026-07-16` 발표분 → 시트 `증시 리뷰_260717`(익일). 한국은 당일 시트.
- 시장별 수집 범위(`daily_review.build_economic_indicators`의 country 인자):
  - `US`: 미국 **3성급만** (`importance[]=3`, country 5)
  - `KR` 시트: `KR`(2성급↑) + `CN`(3성급) + `JP`(3성급), 지표명 앞에 `[한]`/`[중]`/`[일]` 접두.

## 2. 진단 먼저 — 진짜 발표가 없던 날인가?
```bash
gh run list --workflow=daily-global.yml --limit 12 --json databaseId,createdAt,conclusion \
  -q '.[] | "\(.databaseId) \(.createdAt) \(.conclusion)"'
gh run view <id> --log | grep -E "investing.com: |재시도|실패|예상: |전날 발표된 주요"
```
- `전날 발표된 주요 경제지표 없음` → **진짜 0건**. 시트가 맞다. 여기서 중단.
- `HTTP 403` / `수집 실패` → **복구 대상**. 3단계로.
- 러너 로그의 `지표명: 실제 ▲ (예상: X / 이전: Y)` 줄은 **파이프라인이 쓰는 표기의 진실 원천**이다.
  같은 요일의 과거 성공 로그를 보면 그날의 3성급 세트·한글명 관행을 확인할 수 있다.

## 3. 수집 — 소스 우선순위 (위에서부터 시도)

### 3-1. 파이프라인 API 재실행 (가장 정확, 러너에서만)
```bash
gh workflow run econ-refetch.yml -f date=2026-07-16 -f country=US
gh run watch <id> --exit-status; gh run view <id> --log | grep "Refetch economic"
```
성공 시 `ECON_JSON=[...]`이 그대로 시트에 쓸 값이다(지표명·예상·이전 전부 API 원문). **성공하면 4단계로 직행.**
- ⚠️ **로컬 실행은 불가** — 한국 가정 IP는 워밍업 GET부터 상시 403(재시도·브라우저 프로필 변경 모두 무효).
- ⚠️ 러너도 간헐 403이며 **4회 재시도가 전부 실패하는 구간이 있다**(2026-07-16 실측). 그러면 3-2로.

### 3-2. 폴백 — investing 공개 위젯 (Cloudflare 없음, WebFetch로 접근)
```
https://sslecal2.investing.com/?columns=exc_flags,exc_currency,exc_importance,exc_actual,exc_forecast,exc_previous&importance=3&countries=5&calType=week&timeZone=88&lang=1
```
- **`importance=3`을 URL에 반드시 걸 것.** 필터 없이 위젯의 중요도 컬럼을 읽으면 Continuing Jobless Claims·Retail Sales (YoY)까지 "High"로 나와 **파이프라인 3성급 세트와 어긋난다**(2026-07-16 실측: 필터 없이 6건 → 필터 걸면 4건. 7/2 러너 로그가 4건 쪽을 뒷받침).
- `countries=`: 미국 5 / 한국 11 / 중국 37 / 일본 35. `calType=week`는 **현재 주만** 조회 가능 → 지난주 이전 날짜는 3-1이나 3-3으로.
- 위젯은 영어명이다. 한글명은 3-3·2단계 로그에서 확인.

### 3-3. 개별 지표 페이지 교차검증 (권장 — 값·한글명 확인)
`https://kr.investing.com/economic-calendar/<slug>-<id>` (GET, WebFetch 가능). 히스토리 표에서 대상일 행의 실제/예상/이전을 확인.
예: `retail-sales-256`, `core-retail-sales-63`, `initial-jobless-claims-294`, `philadelphia-fed-manufacturing-index-236`.
- 페이지 제목은 `미국 소매판매`처럼 **`미국` 접두가 붙지만 캘린더 표기는 대개 접두가 없다**(단 `미국 평균 시간당 임금`처럼 붙는 것도 있음 — investing이 일관되지 않다). 2단계 로그가 있으면 그 표기를 따른다.

### 3-4. TradingEconomics — 실제값 교차검증 **전용**
`https://tradingeconomics.com/united-states/calendar`
- **예상치는 쓰지 말 것.** 컨센서스 출처가 달라 investing과 불일치한다(2026-07-16 소매판매 예상: TE 0.5% vs investing 0.2%). 시트 규약은 **investing 기준**.

## 4. 시트에 쓸 행 구성
`daily_review`와 **똑같은 3열 구조**를 만든다:
- C(체크포인트) = 지표명 — 관행: `근원 소매판매 (MoM)  (6월)` (기간 앞 **공백 2칸**), `신규 실업수당청구건수`(기간 없는 지표는 접미 없음)
- D(내용) = `0.2% ▼` — **방향 기호를 손으로 정하지 말 것**:
  ```python
  from daily_review import get_direction
  d = get_direction(actual, prev)     # 실제 vs 이전 비교 (▲/▼/=)
  ```
- E(비고) = `예상: 0.2% / 이전: 1.0%`
- A(단계) = 첫 행만 `2.경제 지표`(글로벌) / `2.경제지표`(국장 — **점 뒤 공백 없음**, 표기가 다르다), 나머지 행은 빈칸.

## 5. 시트 기록 — 행 번호를 절대 가정하지 말 것
- **시트는 세션 중에도 바뀐다.** `/market-news`가 뉴스 행을 채우면 경제지표 섹션이 아래로 밀린다(2026-07-17 실측: 6행 → 7행). **쓰기 직전에 다시 읽고** placeholder 위치를 찾는다.
- 반드시 **가드**를 넣는다 — placeholder가 그 행에 있고, 이어받을 행들이 비어 있을 때만 쓴다:
  ```python
  grid = ws.get('A7:F11')
  assert '발표 없음' in cell(0, 2), 'placeholder 아님 — 중단'
  for r in (1, 2, 3):
      assert not any(cell(r, c).strip() for c in range(6)), '비어있지 않음 — 중단'
  ```
- **빈 행이 충분하면 `update`로 채우고, 부족할 때만 `insert_rows`**(삽입은 아래 매크로 섹션 전체를 밀어낸다).
- **`value_input_option="RAW"` 필수** — `USER_ENTERED`는 `0.2%`를 숫자로 파싱해 셀을 깨뜨린다.
- 인증: `sheet_auth.get_credentials()` + `fetch_today_review.find_sheet(creds, 'YYMMDD')` (레포 루트에서 실행).
- 워크시트: `글로벌` | `국장`.
- 기록 후 **읽어서 검증**하고 결과를 보여준다.

## 6. 보고
- 어느 시트·행에 무엇이 들어갔는지 표로.
- **소스 명시**: 3-1(API 원문) / 3-2(위젯 폴백 — 지표명·예상치가 API와 미세하게 다를 수 있음).
- 진단 결과(403 발생일)와, **같은 이유로 비어 있을 다른 날짜**가 있으면 함께 알리고 백필 여부를 묻는다.

## 관련
- 근본 원인·소스 계보: `docs/DATA_PITFALLS.md`, 메모리 `project_econ_calendar_source`
- 재수집 도구: `econ_refetch.py`, `.github/workflows/econ-refetch.yml`
- 시장 뉴스(같은 시트 1번 섹션): `/market-news`
