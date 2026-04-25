"""
주간 경제 캘린더 자동 정리 스크립트.

매주 일요일 실행. ForexFactory가 제공하는 공식 JSON 엔드포인트
(nfs.faireconomy.media/ff_calendar_*.json) 에서 다가오는 주(다음 월~일)
미국/유럽/중국/일본/한국 High-impact(=3성) 이벤트만 수집하여
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

# ForexFactory currency 코드 → 국가명
COUNTRY_MAP = {
    "USD": "미국",
    "EUR": "유럽",
    "CNY": "중국",
    "JPY": "일본",
    "KRW": "한국",
}

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

FF_THISWEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXTWEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"


def _fetch(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = req.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_weekly_events(date_from, date_to):
    """ForexFactory JSON 두 엔드포인트(thisweek+nextweek) 모두 가져와 KST 기준
    date_from~date_to(포함) 범위 + 5개국 + High impact 만 필터링."""
    print(f"  ForexFactory: {date_from.date()} ~ {date_to.date()} 5개국 High-impact 이벤트 조회...")

    raw = []
    for url in (FF_THISWEEK, FF_NEXTWEEK):
        try:
            data = _fetch(url)
            print(f"    {url.rsplit('/', 1)[-1]}: 전체 {len(data)}건")
            raw.extend(data)
        except Exception as e:
            print(f"    {url} 호출 실패: {e}")

    if not raw:
        return []

    range_start = datetime.combine(date_from.date(), datetime.min.time(), tzinfo=KST)
    range_end = datetime.combine(date_to.date(), datetime.max.time(), tzinfo=KST)

    events = []
    seen = set()  # (datetime, country, title) 중복 제거
    for item in raw:
        if item.get("impact") != "High":
            continue
        country_code = item.get("country", "")
        if country_code not in COUNTRY_MAP:
            continue

        try:
            dt = datetime.fromisoformat(item["date"]).astimezone(KST)
        except (ValueError, KeyError, TypeError):
            continue

        if not (range_start <= dt <= range_end):
            continue

        key = (dt.isoformat(), country_code, item.get("title", ""))
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "datetime": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "weekday": WEEKDAY_KR[dt.weekday()],
            "time": dt.strftime("%H:%M"),
            "country": COUNTRY_MAP[country_code],
            "name": item.get("title", "").strip(),
            "forecast": (item.get("forecast") or "").strip(),
            "previous": (item.get("previous") or "").strip(),
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
