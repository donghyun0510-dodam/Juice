# 데이터 수집 함정 (재발 방지 기록)

시장 데이터 수집에서 **조용히 틀리는** 실패들을 기록한다. 예외도 안 나고 로그도 깨끗한데
값만 틀리는 종류라, 한 번 당하면 발견까지 몇 주가 걸린다. 새 지표·새 소스를 붙이기 전에 읽을 것.

---

## 1. 마감 후 배치는 라이브 호가를 쓰면 안 된다 — `settled=True`

### 규칙
`daily_review`·`scouter_logger`처럼 **미 장 마감 후 도는 배치**는 네이버 원자재(금·은·구리·WTI·브렌트)와
채권을 반드시 `settled=True`로 읽는다.

```python
naver_quote("GOLD", settled=True)          # /prices 정산 일별 바만 사용
naver_quote_for_ticker("GC=F", settled=True)
collect_macro_scores(settled=True)         # scouter_logger
```

장중 대시보드(`market_dashboard`)는 기본값(`settled=False`, 라이브)을 그대로 쓴다.
용도가 다르므로 **호출부가 의도를 명시**하는 구조다. 자동 판별하려 들지 말 것 (아래 참조).

### 왜 (2026-06-23 ~ 07-09 사고)
NYMEX/COMEX 선물은 17:00~18:00 ET 정비 휴장 후 **다음 세션으로 재개장**한다. 재개장 뒤의
라이브 `fluctuationsRatio`는 *새 세션의 몇 분치* 등락률(±0.3% 수준)이지, 우리가 원하는
직전 세션 종가 대비 변동이 아니다.

`_from_item(guard_session=True)`의 PREOPEN 가드는 **휴장 구간(17:00~18:00 ET)에서만** 발동하도록
짜여 있었다. "daily-global은 cron `37 21 UTC` = 17:37 ET에 도니 휴장 구간과 겹친다"는 전제였다.
**그 전제가 틀렸다.** GitHub Actions 스케줄은 상시 ~1시간 지연된다:

```
예약 21:37 UTC  →  실제 22:35 ~ 22:56 UTC  =  18:35 ~ 18:52 ET  (재개장 후)
```

그래서 가드는 **단 한 번도 발동한 적이 없고**, 네이버 전환(6/23) 이후 매일 야간세션 값이 시트에 박혔다.

| 대상일 | 지표 | 시트에 기록된 값 | 실제 정산값 |
|---|---|---|---|
| 7/8 | SILVER | +0.31% | **-4.55%** |
| 7/9 | SILVER | -0.66% | **+3.77%** |
| 7/9 | GOLD | -0.21% | **+1.43%** |
| 7/9 | COPPER | -0.31% | **+2.59%** |
| 7/9 | WTI | -0.39% | **-1.96%** |

부호까지 반전됐고, C-Risk·S-Risk·★매크로 종합이 전부 이 값으로 계산됐다.

**BRN만 정상이었던 건 실력이 아니라 우연이다.** ICE 브렌트의 휴장 구간은 18:00~19:00 ET라,
하필 실행 시각과 겹쳐 가드가 발동했다. "하나는 맞으니 소스는 멀쩡하겠지"가 발견을 늦췄다.

### 교훈
- **시각 가정에 기대는 가드를 만들지 말 것.** cron은 늦고, DST는 바뀌고, 거래소별 휴장 구간은 제각각이다.
- 데이터의 *의미*(직전 세션 정산 vs 현재 세션 진행 중)를 호출부가 선언하게 하라.
- 일부만 맞는 상태가 가장 위험하다 — 맞는 항목이 전체를 검증해주는 것처럼 보인다.

### 검증 방법
```python
from naver_macro import naver_settled_date
{k: naver_settled_date(k) for k in ("WTI", "BRENT", "GOLD", "SILVER", "COPPER")}
# 전부 시트 대상일과 같아야 한다. daily_review가 실행 시 자동으로 찍고, 어긋나면 경고한다.
```
진실 원천은 네이버 `/prices` **일별 바**다. 웹 요약·라이브 호가를 보고 뒤집지 말 것.
(같은 원칙의 채권 버전: `naver_quote` `bond` 분기의 롤오버 가드.)

---

## 2. 휴장일 판정 — "빈 응답"이 아니라 "거래일"로 판정하라

### 규칙
대상일에 미국 증시가 열렸는지는 `daily_review.us_traded_on(target_date)`로 판정한다.
네이버 지수 `.DJI`의 `localTradedAt`(**마감 직후 즉시 반영되는 최신 거래일**)이 1차 권위,
yfinance 일봉 인덱스가 폴백이다.

### 왜 (2026-07-03 유령 시트)
옛 가드는 `yf.download("^GSPC", start=D, end=D+1)`가 **비었는지**만 봤다. 문제는 실행 시각
(18:35~18:52 ET)에 **"휴장"과 "일봉이 아직 안 올라옴"이 같은 모습**이라는 것이다. 7/3(독립기념일
대체휴장)에 이 체크가 통과해버렸고, yfinance가 마지막 두 일봉(7/1·7/2)을 물어와
**`증시 리뷰_260704` 전체가 7/2 데이터의 복사본**이 되었다(지수·섹터·금리·환율 전부).
원자재만 라이브라 노이즈였고, ICE 브렌트만 진짜 7/3 값이었다. → 시트는 휴지통으로.

### 교훈
- 데이터 소스의 **부재(empty)를 의미(휴장)로 해석하지 말 것.** 부재는 "아직 없음"일 수도 있다.
- 판정은 반환된 **행의 날짜**로 한다. 판단 불가면 조용히 넘어가지 말고 **실패(exit 1)로 알린다** —
  유령 시트는 조용한 성공보다 나쁘다.
- `cron: * * 1-5`는 요일만 본다. 휴장일에도 워크플로우는 돈다.

---

## 3. 구글 시트에 등락률 쓸 때 `value_input_option`

`gspread`의 기본값은 `RAW`이고 `daily_review`도 RAW로 쓴다. `USER_ENTERED`로 쓰면
`"+4.37%"`가 **숫자 `0.0437`로 파싱**되어 셀 타입이 바뀐다(표시도 깨진다).
시트 수정 스크립트는 반드시 `value_input_option="RAW"`.

---

## 4. FX(fxlist)도 마감 후 배치는 `settled=True` — 라이브는 장중 스냅샷

### 규칙
글로벌 매크로 환율(DXY·EUR/USD·USD/KRW)도 마감 후 배치는 `settled=True`로 읽는다.
`daily_review`의 `get_price_and_change("EURUSD=X", settled=True)` 등. USD/JPY·USD/CNY는
원래 `worldDailyQuote` 일별 종가(fxdesk)라 무관.

### 왜 (2026-07-15 사고)
`fxlist`(네이버 `marketindex/exchange`)는 **라이브 호가**다. daily-global은 06:37 KST(=미 마감
후)에 도는데, 그 시각 FX는 이미 다음 세션 장중값이다. DXY·USD/JPY는 우연히 일별 종가와 근접해
맞았지만 **EUR/USD만 장중값(1.138, -0.30%)이 박혀 실제 07-14 종가(1.1444, +0.37%)와 부호까지 반전**됐다.

### 픽스
`naver_quote(key, settled=True)`가 `fxlist`도 `exchange/{code}/prices` 일별 정산 바를 먼저 읽고,
이 경로가 비는 크로스(EURUSD)는 `worldDailyQuote(FX_EURUSD)` 일별 종가로 폴백하도록 확장.
(`.DXY`·`FX_USDKRW`는 `/prices` 있음, `EURUSD`는 없음 — 소스마다 제각각이라 둘 다 시도.)

---

## 5. 구리는 '현물'(CMCU0) — '선물'(HGcv1) 아님

### 규칙
구리 표시·C-Risk 입력은 네이버 **구리 현물(`CMCU0`, USD/TONNE)**를 쓴다.
`naver_macro._DISPATCH["COPPER"] = ("metals", "CMCU0")`. 이미 $/톤이라 **환산(×2204.62) 불필요**.

### 왜 (2026-07-15 사고)
네이버 metals에는 구리가 둘 있다 — `HGcv1`=**구리(선물)** $/lb, `CMCU0`=**구리(현물)** $/tonne.
옛 코드는 선물($/lb)을 ×2204.62해 $/톤을 **합성**했는데(1 metric tonne=2204.62 lb, 옛 주석의
"short ton"은 오기), 현물과 ~3% 어긋났다(합성 14,061 vs 현물 13,596). 사용자가 네이버증권에서 보는
값은 현물이라 안 맞았다. 폴백(yfinance HG=F 선물·investing)만 ×2204.62로 $/톤 근사 유지.

### 주의 (3파일 동기)
`get_copper_investing`는 `daily_review`·`scouter_core`·`market_dashboard` 3곳에 있다. G/C 비율
캘리브레이션(gold/copper)은 $/톤 기준 그대로라 현물 전환 영향 미미(~3%, 현 레벨에선 점수 동일).

---

## 6. KR 개별종목 stale 바

yfinance가 KRX 당일 일봉을 늦게 게시하는 종목(에스엠·JYP·CJ ENM·알테오젠 등)은 등락률이
전일 값으로 남아 `⚠MM/DD` stale 마킹된다. 급등한 종목에 Short/Sell Sign이 뜨면 stale을 의심할 것.
보정은 `naver_kr_stock(6자리코드)`. 자세한 건 `CLAUDE.md`의 국장 시트 절.

---

## 7. USD/JPY·USD/CNY는 마감 후 배치에서 네이버 데스크(worldDailyQuote)를 쓰면 안 된다

### 규칙
`daily_review`의 USD/JPY·USD/CNY는 **yfinance(JPY=X·CNY=X)의 target_date NY-종가 바**를
1차로 쓴다(`_fx_ny_close(ticker, target_date)`). 네이버 데스크는 라이브 폴백으로만.

### 왜 (2026-07-15 재발)
네이버 데스크톱 `worldDailyQuote`(fxdesk)는 **아시아-세션 클럭**이라, NY 종가 기준인 시트의
나머지(DXY·지수·금리)와 부호까지 어긋난다:

| 대상일 | 지표 | 데스크(아시아 클럭) | NY 종가(진실) |
|---|---|---|---|
| 7/15 | USD/JPY | 162.14 / **+0.11%** | 162.19 / **-0.15%** |

DATA_PITFALLS 항목4(fxlist EURUSD 라이브)를 고칠 때 fxdesk는 "원래 일별 종가라 무관"이라고
넘겼는데, **그 '일별 종가'가 NY가 아닌 아시아 마감**이라는 게 함정이었다. 이제 `naver_quote`의
fxdesk 분기는 `settled=True`면 `None`을 반환하고, 호출부가 yfinance NY-종가로 폴백한다.

### 주의 — target_date 고정
`_fx_ny_close`는 yfinance 일별 바에서 **target_date 바를 명시적으로 선택**한다. 단순 last-2는
FX가 24h 거래라 재실행 시각에 따라 *다음날 바*를 물어와(백필 때 07-15 대신 07-16) 어긋난다.

---

## 8. 구리 현물(CMCU0)은 `/prices` 일별 바가 아니라 라이브가 정산값 — 게시 지연 주의

### 규칙
`settled=True`에서도 **구리 현물(CMCU0)만은 라이브 아이템을 정산값으로 쓴다**
(`naver_macro._SPOT_METALS`). 라이브·일별 바 중 `localTradedAt`이 늦은 쪽 선택.
선물(GOLD/SILVER/WTI/BRENT)은 반대로 라이브가 '다음 세션'이라 `/prices` 일별 바가 맞다.

### 왜 (2026-07-15 재발)
CMCU0는 **LME 캐시(현물)** — 라이브 아이템이 `marketStatus=null`, `localTradedAt`=런던 캐시
픽싱으로 이미 당일 정산값이다. 그런데 `/prices` **일별 바는 게시가 수 시간 지연**돼, 배치
실행 시각(≈18:37 ET)에 아직 D-1 바만 올라와 있을 때가 있다. 옛 코드는 `settled=True`가
`/prices` 최신 바를 무조건 반환해, **07-14 바(13,596/+0.80%)가 시트에 조용히 박혔다**(실제
07-15 현물 -0.45%). 선물(GC/SI/CL cv1)은 같은 시각 라이브가 `marketStatus=OPEN`(다음 세션)
이라 반대로 `/prices`가 맞다 — **현물/선물이 라이브 신선도가 정반대**인 게 핵심.

### 검증
`naver_settled_date("COPPER")`도 현물은 라이브 날짜를 반영하도록 맞췄다. `daily_review`는
각 원자재 정산 바 거래일을 **target_date와 비교**해(서로 비교가 아님 — '전부 하루 밀림'도 잡음)
어긋나면 이름을 찍어 경고한다.
