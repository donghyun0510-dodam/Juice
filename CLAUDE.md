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

## 자동 실행 설정
| 작업 | 스케줄러 이름 | 시간 | 배치 파일 | 로그 |
|------|-------------|------|----------|------|
| 글로벌 시트 | `StockReview_Global` | 매일 05:00 | `run_global.bat` (`--global-only`) | `log_global.txt` |
| 국장 시트 | `StockReview_Korea` | 매일 16:00 | `run_korea.bat` (`--korea-only`) | `log_korea.txt` |

- 공통 설정: 배터리 무관, 네트워크 필요, 놓친 실행 시 다음 로그인 때 즉시 실행, 최대 30분
- 글로벌: 전일 미국 증시 데이터 + 전일 미국 경제지표
- 국장: 당일 한국 증시 데이터 + 당일 한국 경제지표 (오후 8시라 당일 발표분 반영)
