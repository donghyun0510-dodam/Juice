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

## 2. 구글 시트에 등락률 쓸 때 `value_input_option`

`gspread`의 기본값은 `RAW`이고 `daily_review`도 RAW로 쓴다. `USER_ENTERED`로 쓰면
`"+4.37%"`가 **숫자 `0.0437`로 파싱**되어 셀 타입이 바뀐다(표시도 깨진다).
시트 수정 스크립트는 반드시 `value_input_option="RAW"`.

---

## 3. KR 개별종목 stale 바

yfinance가 KRX 당일 일봉을 늦게 게시하는 종목(에스엠·JYP·CJ ENM·알테오젠 등)은 등락률이
전일 값으로 남아 `⚠MM/DD` stale 마킹된다. 급등한 종목에 Short/Sell Sign이 뜨면 stale을 의심할 것.
보정은 `naver_kr_stock(6자리코드)`. 자세한 건 `CLAUDE.md`의 국장 시트 절.
