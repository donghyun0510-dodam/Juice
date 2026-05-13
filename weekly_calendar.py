"""
주간 경제 캘린더 자동 정리 스크립트.

매주 일요일 실행. Finnhub economic calendar API
(finnhub.io/api/v1/calendar/economic) 에서 다가오는 주(다음 월~일)
미국/유럽/중국/일본/한국 high impact 이벤트만 수집하여
구글 드라이브 `주식리뷰` 폴더에 '주간 경제일정_YYMMDD' 스프레드시트로 저장.

컬럼: 날짜 | 요일 | 시간(KST) | 국가 | 지표명 | 예상 | 이전 | 발표
발표 컬럼은 생성 시 비워두고 fill_event_actuals.py가 발표 후 채움.

환경변수 FINNHUB_API_KEY 필수.
"""

import os
import re
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import time
from datetime import datetime, timedelta, timezone

import gspread
import requests as req
from googleapiclient.discovery import build

from sheet_auth import get_credentials

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")

KST = timezone(timedelta(hours=9))

# Finnhub country 코드 (ISO Alpha-2) → 표시 국가명
COUNTRY_MAP = {
    "US": "미국",
    "EU": "유럽",
    "CN": "중국",
    "JP": "일본",
    "KR": "한국",
}

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"

# 매크로 분석 시 자주 인용하는 핵심 지표 — Finnhub impact가 high 미만이어도 포함.
# YoY/Core 변형이 medium으로 분류되는 경우 (예: PPI YoY가 누락되는 사례) 대비.
CORE_MACRO_KEYWORDS = (
    "CPI", "PPI", "PCE", "INFLATION",
    "NONFARM PAYROLLS", "NON FARM PAYROLLS",
    "UNEMPLOYMENT RATE", "JOLTS", "ADP", "AVERAGE HOURLY EARNINGS",
    "RETAIL SALES", "PERSONAL SPENDING", "PERSONAL INCOME",
    "GDP", "INDUSTRIAL PRODUCTION", "DURABLE GOODS",
    "ISM", "PMI",
    "CONSUMER CONFIDENCE", "MICHIGAN",
    "BUILDING PERMITS", "HOUSING STARTS",
    "EXISTING HOME SALES", "NEW HOME SALES",
    "FOMC", "FED INTEREST RATE", "ECB INTEREST RATE", "BOJ INTEREST RATE", "BOK INTEREST RATE",
)


def _is_relevant_event(item):
    """high-impact 또는 핵심 매크로 지표면 통과."""
    impact = (item.get("impact") or "").lower()
    if impact == "high":
        return True
    name_upper = (item.get("event") or "").upper()
    return any(kw in name_upper for kw in CORE_MACRO_KEYWORDS)


# ── 지표명 한글 변환 ────────────────────────────────────────────────────────
# Finnhub `event` 필드(영문)를 한국 매크로 리포트에서 통상 쓰는 한글명으로 변환.
# 매칭 실패 시 원문 그대로 노출 (정보 손실 방지).

INDICATOR_KR = {
    # ── 통화정책·중앙은행 ──
    "FOMC Statement": "FOMC 성명서",
    "FOMC Press Conference": "FOMC 기자회견",
    "FOMC Economic Projections": "FOMC 경제전망",
    "FOMC Minutes": "FOMC 의사록",
    "Fed Interest Rate Decision": "Fed 금리결정",
    "Federal Funds Rate": "연방기금금리",
    "Fed Chair Powell Speech": "파월 의장 연설",
    "Fed Chair Powell Testimony": "파월 의장 의회증언",
    "ECB Interest Rate Decision": "ECB 금리결정",
    "ECB Press Conference": "ECB 기자회견",
    "ECB Monetary Policy Statement": "ECB 통화정책 성명",
    "ECB Main Refinancing Rate": "ECB 기준금리",
    "ECB President Lagarde Speech": "라가르드 ECB 총재 연설",
    "BoJ Interest Rate Decision": "BOJ 금리결정",
    "BoJ Press Conference": "BOJ 기자회견",
    "BoJ Monetary Policy Statement": "BOJ 통화정책 성명",
    "BoJ Outlook Report": "BOJ 경제전망 보고서",
    "BoK Interest Rate Decision": "한은 기준금리 결정",
    "PBoC Loan Prime Rate": "PBOC 대출우대금리(LPR)",
    "PBoC Loan Prime Rate 1Y": "PBOC LPR 1년물",
    "PBoC Loan Prime Rate 5Y": "PBOC LPR 5년물",

    # ── 고용 ──
    "Non Farm Payrolls": "비농업 고용",
    "Nonfarm Payrolls": "비농업 고용",
    "Unemployment Rate": "실업률",
    "Average Hourly Earnings": "시간당 평균임금",
    "Participation Rate": "경제활동참가율",
    "Initial Jobless Claims": "신규 실업수당청구",
    "Continuing Jobless Claims": "연속 실업수당청구",
    "ADP Employment Change": "ADP 민간고용",
    "JOLTs Job Openings": "JOLTs 구인건수",
    "Challenger Job Cuts": "챌린저 감원 규모",

    # ── 물가 ──
    "CPI": "소비자물가지수(CPI)",
    "Core CPI": "근원 소비자물가지수",
    "Inflation Rate": "소비자물가 상승률",
    "Core Inflation Rate": "근원 소비자물가 상승률",
    "PPI": "생산자물가지수(PPI)",
    "Core PPI": "근원 생산자물가지수",
    "PCE Price Index": "PCE 물가지수",
    "Core PCE Price Index": "근원 PCE 물가지수",
    "HICP": "조화소비자물가지수(HICP)",
    "Core HICP": "근원 HICP",
    "Tokyo CPI": "도쿄 소비자물가지수",
    "Tokyo Core CPI": "도쿄 근원 CPI",
    "Import Prices": "수입물가",
    "Export Prices": "수출물가",

    # ── 성장·생산 ──
    "GDP Growth Rate": "GDP 성장률",
    "GDP": "GDP",
    "GDP Price Index": "GDP 디플레이터",
    "Industrial Production": "산업생산",
    "Manufacturing Production": "제조업 생산",
    "Capacity Utilization": "설비가동률",

    # ── 소비·소매 ──
    "Retail Sales": "소매판매",
    "Core Retail Sales": "근원 소매판매",
    "Consumer Confidence": "소비자신뢰지수",
    "Michigan Consumer Sentiment": "미시간 소비자심리지수",
    "Michigan Consumer Expectations": "미시간 소비자기대지수",
    "Michigan Inflation Expectations": "미시간 기대인플레이션",
    "Personal Income": "개인소득",
    "Personal Spending": "개인소비지출",

    # ── 주택 ──
    "Building Permits": "건축허가",
    "Housing Starts": "주택착공",
    "Existing Home Sales": "기존주택판매",
    "New Home Sales": "신규주택판매",
    "Pending Home Sales": "잠정주택판매",
    "S&P/CS HPI Composite - 20 n.s.a.": "S&P/케이스-쉴러 주택가격지수(20개 도시)",
    "Case Shiller Home Price Index": "케이스-쉴러 주택가격지수",

    # ── 산업·업황 ──
    "ISM Manufacturing PMI": "ISM 제조업 PMI",
    "ISM Non-Manufacturing PMI": "ISM 서비스업 PMI",
    "ISM Services PMI": "ISM 서비스업 PMI",
    "Manufacturing PMI": "제조업 PMI",
    "Services PMI": "서비스업 PMI",
    "Composite PMI": "종합 PMI",
    "Markit Manufacturing PMI": "Markit 제조업 PMI",
    "Markit Services PMI": "Markit 서비스업 PMI",
    "Markit Composite PMI": "Markit 종합 PMI",
    "S&P Global Manufacturing PMI": "S&P글로벌 제조업 PMI",
    "S&P Global Services PMI": "S&P글로벌 서비스업 PMI",
    "S&P Global Composite PMI": "S&P글로벌 종합 PMI",
    "Caixin Manufacturing PMI": "차이신 제조업 PMI",
    "Caixin Services PMI": "차이신 서비스업 PMI",
    "Caixin Composite PMI": "차이신 종합 PMI",
    "NBS Manufacturing PMI": "국가통계국 제조업 PMI",
    "NBS Non Manufacturing PMI": "국가통계국 비제조업 PMI",
    "Tankan Large Manufacturers Index": "단칸 대기업 제조업지수",
    "Tankan Large Non-Manufacturers Index": "단칸 대기업 비제조업지수",
    "ZEW Economic Sentiment Index": "ZEW 경기전망지수",
    "Ifo Business Climate": "Ifo 기업환경지수",
    "Philadelphia Fed Manufacturing Index": "필라델피아 연은 제조업지수",
    "NY Empire State Manufacturing Index": "엠파이어스테이트 제조업지수",
    "Chicago PMI": "시카고 PMI",

    # ── 대외·재고 ──
    "Trade Balance": "무역수지",
    "Exports": "수출",
    "Imports": "수입",
    "Current Account": "경상수지",
    "Wholesale Inventories": "도매재고",
    "Business Inventories": "기업재고",
    "Factory Orders": "제조업 수주",
    "Durable Goods Orders": "내구재 주문",
    "Core Durable Goods Orders": "근원 내구재 주문",
    "EIA Crude Oil Stocks Change": "EIA 원유재고",
    "API Crude Oil Stock Change": "API 원유재고",
}

# 접미사 처리: "CPI YoY (Mar)" 같이 base + 빈도 + 월(괄호) 형태
FREQ_KR = {
    "MoM": "전월대비",
    "YoY": "전년대비",
    "QoQ": "전분기대비",
    "M/M": "전월대비",
    "Y/Y": "전년대비",
    "Q/Q": "전분기대비",
}

QUALIFIER_KR = {
    "Final": "확정",
    "Prelim": "잠정",
    "Preliminary": "잠정",
    "Advance": "속보",
    "Adv": "속보",
    "Flash": "속보",
}

_FREQ_RE = re.compile(r"\b(MoM|YoY|QoQ|M/M|Y/Y|Q/Q)\b", re.IGNORECASE)
_QUAL_RE = re.compile(r"\b(Final|Preliminary|Prelim|Advance|Adv|Flash)\b", re.IGNORECASE)
_PAREN_RE = re.compile(r"\(([^)]+)\)")


def translate_indicator(name: str) -> str:
    """Finnhub 영문 지표명 → 한글. 매칭 실패한 부분은 원문 유지."""
    if not name:
        return ""
    s = " ".join(name.split())  # 공백 정규화

    if s in INDICATOR_KR:
        return INDICATOR_KR[s]

    # 괄호 안 노트(월/연도 등)는 분리 보존
    paren_notes = _PAREN_RE.findall(s)
    s_no_paren = _PAREN_RE.sub("", s).strip()

    # 빈도 / 한정자 추출
    freq_m = _FREQ_RE.search(s_no_paren)
    qual_m = _QUAL_RE.search(s_no_paren)
    freq = freq_m.group(0).upper().replace("/", "") if freq_m else None
    qual = qual_m.group(0).capitalize() if qual_m else None

    base = _FREQ_RE.sub("", s_no_paren)
    base = _QUAL_RE.sub("", base)
    base = " ".join(base.split())

    base_kr = INDICATOR_KR.get(base, base)

    parts = [base_kr]
    if freq:
        parts.append(f"({FREQ_KR.get(freq, freq)})")
    if qual:
        parts.append(QUALIFIER_KR.get(qual, qual))
    if paren_notes:
        # 월 표기(Mar/Q1 등)는 그대로 보존
        parts.append(f"({', '.join(paren_notes)})")
    return " ".join(parts)


def fetch_weekly_events(date_from, date_to):
    """Finnhub economic calendar API에서 dateFrom~dateTo 범위 조회 후
    5개국 + impact=high 만 필터링. Time 필드는 UTC naive."""
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY 환경변수 미설정")

    params = {
        "from": date_from.strftime("%Y-%m-%d"),
        "to": date_to.strftime("%Y-%m-%d"),
        "token": api_key,
    }
    print(f"  Finnhub: {date_from.date()} ~ {date_to.date()} 경제 캘린더 조회...")

    resp = None
    try:
        resp = req.get(FINNHUB_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("economicCalendar", []) or []
    except Exception as e:
        body_text = resp.text[:300] if resp is not None else ""
        print(f"    호출 실패: {e} / 응답: {body_text}")
        return []

    print(f"    전체 {len(raw)}건")

    range_start = datetime.combine(date_from.date(), datetime.min.time(), tzinfo=KST)
    range_end = datetime.combine(date_to.date(), datetime.max.time(), tzinfo=KST)

    events = []
    seen = set()
    for item in raw:
        if not _is_relevant_event(item):
            continue
        country = COUNTRY_MAP.get((item.get("country") or "").strip().upper())
        if not country:
            continue

        time_str = (item.get("time") or "").strip()
        if not time_str:
            continue
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        dt = dt.astimezone(KST)

        if not (range_start <= dt <= range_end):
            continue

        raw_name = (item.get("event") or "").strip()
        name = translate_indicator(raw_name)
        unit = (item.get("unit") or "").strip()

        def _fmt(v):
            if v is None or v == "":
                return ""
            return f"{v}{unit}" if unit else str(v)

        forecast = _fmt(item.get("estimate"))
        previous = _fmt(item.get("prev"))

        key = (dt.isoformat(), country, name)
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "datetime": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "weekday": WEEKDAY_KR[dt.weekday()],
            "time": dt.strftime("%H:%M") if dt.hour or dt.minute else "종일",
            "country": country,
            "name": name,
            "forecast": forecast,
            "previous": previous,
        })

    events.sort(key=lambda e: e["datetime"])
    print(f"    필터 후: {len(events)}건")
    return events


def build_rows(events):
    rows = [["날짜", "요일", "시간(KST)", "국가", "지표명", "예상", "이전", "발표"]]
    for e in events:
        rows.append([
            e["date"], e["weekday"], e["time"], e["country"],
            e["name"], e["forecast"], e["previous"], "",
        ])
    return rows


def create_sheet(gc, title, rows):
    sh = gc.create(title, folder_id=FOLDER_ID)
    print(f"  스프레드시트 생성: {title} ({sh.id})")
    ws = sh.sheet1
    ws.update_title("경제일정")
    ws.update(range_name="A1", values=rows)
    try:
        ws.format("A1:H1", {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"})
        ws.freeze(rows=1)
    except Exception as e:
        print(f"  서식 적용 실패(무시): {e}")
    return sh


def main():
    today = datetime.now(KST)
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 1
    upcoming_monday = (today + timedelta(days=days_until_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    upcoming_sunday = upcoming_monday + timedelta(days=6)

    print(f"대상 기간: {upcoming_monday.strftime('%Y-%m-%d (%a)')} ~ {upcoming_sunday.strftime('%Y-%m-%d (%a)')}")

    events = fetch_weekly_events(upcoming_monday, upcoming_sunday)
    rows = build_rows(events)
    if not events:
        print("3성급 이벤트 0건 — placeholder 시트 생성")
        rows.append(["", "", "", "", "이번 주 3성급 이벤트 없음", "", "", ""])

    title = f"주간 경제일정_{upcoming_monday.strftime('%y%m%d')}"

    creds = get_credentials()
    gc = gspread.authorize(creds)

    create_sheet(gc, title, rows)
    time.sleep(1)
    print("완료.")


if __name__ == "__main__":
    main()
