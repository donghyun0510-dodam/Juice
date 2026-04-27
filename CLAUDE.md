# 미국/한국 증시 일일 리뷰 자동화

## 프로젝트 개요
매일 아침 `daily_review.py`를 실행하면 전날 미국/한국 증시 데이터를 수집하여 구글 시트(`증시 리뷰_YYMMDD`)를 자동 생성하는 시스템.

## 파일 구조
- `daily_review.py` — 메인 자동화 스크립트
- `client_secret.json` — Google OAuth2 클라이언트 시크릿 (비공개)
- `token.pickle` — 저장된 OAuth2 토큰 (자동 생성, 비공개)
- `credentials.json` — 서비스 계정 키 (사용 안 함, OAuth2로 전환됨)

## 실행 방법
```bash
cd "C:\Users\dongh\Desktop\주식\AI agent"
python daily_review.py
```
- 최초 실행 시 브라우저에서 구글 로그인 필요 (이후 token.pickle에 저장)
- 구글 드라이브 `주식리뷰` 폴더(ID: `1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv`)에 시트 생성

## 데이터 소스
| 소스 | 수집 항목 |
|------|----------|
| **investing.com** 경제 캘린더 API | 전날 발표된 미국 3성급 경제지표 (현수치/예상/이전) |
| **yfinance** (Yahoo Finance) | 금리, 환율, 원자재, VIX, 금, BTC, 지수 등락률, 미국/한국 개별종목 등락률 |

## 구글 시트 구조
### 글로벌 시트 (미국)
1. 시장 뉴스 — 수동
2. 경제 지표 — investing.com 자동 (전날 발표분만)
3. 매크로 동향 — 금리(2Y/10Y/30Y), 환율(DXY/EUR/JPY), 원자재(BRN/WTI/COPPER) + 변동률%
4. 위험 심리 지표 — VIX, GOLD, BITCOIN + 변동률%
5. 지수 동향 — DOW, NASDAQ, S&P500, RUSSELL2000
6. 섹터/종목 — 반도체, 빅테크, 소프트웨어 등 70여개
7. 종합 요약 — 수동
8. 결론 — 수동

### 국장 시트 (한국)
1. Global 증시 — 수동
2. 경제지표 — 수동
3. 뉴스 Flow — 수동
4. 아시아 증시 — 니케이, 대만 가권, 항셍, 상해종합
5. 지수 및 Macro — KOSPI, KOSPI200, KOSDAQ, 원/달러
6. 섹터/종목 — 80여개 한국 종목 (KRX 티커)
7. 종합 요약 — 수동
8. 결론 — 수동

## 기술 스택
- Python 3.14
- `yfinance`, `gspread`, `beautifulsoup4`, `google-api-python-client`, `google-auth-oauthlib`

## 코드 수정 시 주의사항
- `client_secret.json`, `token.pickle`, `credentials.json`은 민감 파일 — 절대 커밋/공유 금지
- investing.com API는 비공식 — User-Agent, X-Requested-With 헤더 필수
- yfinance 일괄 다운로드(`yf.download`) 사용하여 API 호출 최소화
- 구글 시트 API rate limit 방지를 위해 시트 간 `time.sleep(1)` 삽입
- 경제지표는 전날 날짜 기준, 주말이면 금요일로 자동 조정

## 자동 실행 설정 (GitHub Actions)
모든 스케줄 자동화는 **GitHub Actions 워크플로우**(`.github/workflows/*.yml`)로 실행됨. 러너는 `ubuntu-latest`, `TZ=Asia/Seoul`로 설정.

| 워크플로우 파일 | 이름 | Cron (UTC) | KST 환산 | 실행 명령 |
|---------------|------|-----------|---------|----------|
| `daily-global.yml` | Daily Review - Global | `15 20 * * 1-5` | 월~금 05:15 | `python daily_review.py --global-only` |
| `daily-korea.yml` | Daily Review - Korea | `0 7 * * 1-5` | 월~금 16:00 | `python daily_review.py --korea-only` |
| `intraday-global.yml` | Intraday Scan - Global | `*/10 13-21 * * 1-5` | 월~금 22:30~06:00 10분 간격 | `python intraday_scan.py --market global` |
| `intraday-korea.yml` | Intraday Scan - Korea | `*/10 0-6 * * 1-5` | 월~금 09:00~15:30 10분 간격 | `python intraday_scan.py --market korea` |
| `scouter-timeseries.yml` | Scouter Timeseries & Performance | `17 20 * * 1-5` | 화~토 05:17 (미국 종가 직후) | `python scouter_logger.py` |
| `monthly-input-reminder.yml` | Monthly Input Reminder | `0 11 27 * *` | 매월 27일 20:00 (정산 D-2h) | `python monthly_input_reminder.py` |
| `monthly-stock-returns.yml` | Monthly Stock Returns | `0 13 27 * *` | 매월 27일 22:00 | `python monthly_stock_returns.py` |

### 공통 구성
- `concurrency.group: sheet-writer` — 시트 쓰기 워크플로우가 동시에 실행되지 않도록 직렬화
- 공통 셋업: `.github/actions/setup/action.yml` (Python 3.12, pip 캐시, `requirements.txt` 설치, `trendfollow-rules-DH` 프라이빗 레포에서 `signal-judge.md` 주입, SA 자격증명 기록)
- Secrets: `GOOGLE_OAUTH_TOKEN_B64`, `GOOGLE_SA_JSON`, `GSHEET_FOLDER_ID`, `RULES_DEPLOY_KEY`, `GMAIL_APP_PASSWORD`
- 상태 파일(`signal_snapshot.json`, `macro_snapshot.json`, `long_sign_seen.json`, `kr_promotion_tracker.json`, `macro_alert_state.json`)은 실행 후 `github-actions[bot]`이 `[skip ci]` 커밋으로 main에 commit-back

### 수동 실행
- GitHub Actions 탭에서 `workflow_dispatch` 수동 트리거 가능
- 로컬에서 동일한 동작: `python daily_review.py --global-only` / `--korea-only` 직접 실행

### 주의
- GitHub Actions는 **레포 활동이 60일간 없으면 스케줄 cron을 자동 비활성화**함 → 정기적으로 확인 필요
- 구버전 Windows Task Scheduler(`StockReview_Global`/`StockReview_Korea`)와 로컬 `run_*.bat`은 **더 이상 사용하지 않음** (히스토리 용도로만 남아있음)
