"""
주간 경제 캘린더 자동 정리 스크립트.

매주 일요일 실행. Trading Economics guest API
(api.tradingeconomics.com/calendar) 에서 다가오는 주(다음 월~일)
미국/유럽/중국/일본/한국 importance=3 이벤트만 수집하여
구글 드라이브 `주식리뷰` 폴더에 '주간 경제일정_YYMMDD' 스프레드시트로 저장.

컬럼: 날짜 | 요일 | 시간(KST) | 국가 | 지표명 | 예상 | 이전
"""

import os
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

# Trading Economics country 명칭 → 표시 국가명
COUNTRY_MAP = {
    "United States": "미국",
    "Euro Area": "유럽",
    "China": "중국",
    "Japan": "일본",
    "South Korea": "한국",
}

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

TE_BASE = "https://api.tradingeconomics.com/calendar/country"


def fetch_weekly_events(date_from, date_to):
    """Trading Economics guest API에서 dateFrom~dateTo + 5개국 + importance=3 조회.
    Date 필드는 UTC naive 로 가정하고 KST 변환."""
    countries = ",".join(COUNTRY_MAP.keys()).replace(" ", "%20")
    url = (
        f"{TE_BASE}/{countries}"
        f"?c=guest:guest"
        f"&d1={date_from.strftime('%Y-%m-%d')}"
        f"&d2={date_to.strftime('%Y-%m-%d')}"
        f"&importance=3"
        f"&format=json"
    )
    print(f"  Trading Economics: {date_from.date()} ~ {date_to.date()} 5개국 importance=3 이벤트 조회...")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = req.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        body = locals().get("resp")
        body_text = body.text[:200] if body is not None else ""
        print(f"    호출 실패: {e} / 응답: {body_text}")
        return []

    print(f"    전체 {len(raw)}건")

    range_start = datetime.combine(date_from.date(), datetime.min.time(), tzinfo=KST)
    range_end = datetime.combine(date_to.date(), datetime.max.time(), tzinfo=KST)

    events = []
    seen = set()
    for item in raw:
        country = COUNTRY_MAP.get(item.get("Country", "").strip())
        if not country:
            continue

        date_str = (item.get("Date") or "").strip()
        if not date_str:
            continue
        # "2026-04-30T18:00:00" (UTC naive) — Z 또는 offset 가능성 모두 처리
        try:
            if date_str.endswith("Z"):
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            elif "+" in date_str[10:] or "-" in date_str[10:]:
                dt = datetime.fromisoformat(date_str)
            else:
                dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        dt = dt.astimezone(KST)

        if not (range_start <= dt <= range_end):
            continue

        name = (item.get("Event") or item.get("Category") or "").strip()
        forecast = (item.get("Forecast") or item.get("TEForecast") or "").strip()
        previous = (item.get("Previous") or "").strip()

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
    rows = [["날짜", "요일", "시간(KST)", "국가", "지표명", "예상", "이전"]]
    for e in events:
        rows.append([
            e["date"], e["weekday"], e["time"], e["country"],
            e["name"], e["forecast"], e["previous"],
        ])
    return rows


def create_sheet(gc, title, rows):
    sh = gc.create(title, folder_id=FOLDER_ID)
    print(f"  스프레드시트 생성: {title} ({sh.id})")
    ws = sh.sheet1
    ws.update_title("경제일정")
    ws.update(range_name="A1", values=rows)
    try:
        ws.format("A1:G1", {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"})
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
        rows.append(["", "", "", "", "이번 주 3성급 이벤트 없음", "", ""])

    title = f"주간 경제일정_{upcoming_monday.strftime('%y%m%d')}"

    creds = get_credentials()
    gc = gspread.authorize(creds)

    create_sheet(gc, title, rows)
    time.sleep(1)
    print("완료.")


if __name__ == "__main__":
    main()
