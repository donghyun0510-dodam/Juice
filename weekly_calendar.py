"""
주간 경제 캘린더 자동 정리 스크립트.

매주 일요일 실행. investing.com 경제 캘린더 API에서 다가오는 주(다음 월~일) 동안
미국/유럽/중국/일본/한국 3성급 이벤트만 수집하여 구글 드라이브 `주식리뷰` 폴더에
'주간 경제일정_YYMMDD'(YYMMDD = 다가오는 월요일) 스프레드시트로 저장한다.

컬럼: 날짜 | 요일 | 시간(KST) | 국가 | 지표명 | 예상 | 이전
"""

import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import time
from datetime import datetime, timedelta

import gspread
import requests as req
from bs4 import BeautifulSoup
from googleapiclient.discovery import build

from sheet_auth import get_credentials

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")

# investing.com 국가 코드
COUNTRY_CODES = {
    "5": "미국",
    "72": "유럽",
    "37": "중국",
    "35": "일본",
    "11": "한국",
}

# flag span title → 표시 국가명 (kr.investing.com 한글 title 매핑)
FLAG_TITLE_MAP = {
    "미국": "미국",
    "유럽 연합": "유럽",
    "유로존": "유럽",
    "중국": "중국",
    "일본": "일본",
    "한국": "한국",
    "대한민국": "한국",
}

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _fetch_country(date_from, date_to, country_code):
    """단일 국가에 대해 investing.com 캘린더 호출 후 HTML 반환."""
    url = "https://kr.investing.com/economic-calendar/Service/getCalendarFilteredData"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://kr.investing.com/economic-calendar/",
    }
    payload = {
        "dateFrom": date_from.strftime("%Y-%m-%d"),
        "dateTo": date_to.strftime("%Y-%m-%d"),
        "country[]": country_code,
        "importance[]": "3",
    }
    try:
        resp = req.post(url, headers=headers, data=payload, timeout=20)
        data = resp.json()
        return data.get("data", "")
    except Exception as e:
        print(f"    investing.com 호출 실패 (country={country_code}): {e} / 응답 일부: {resp.text[:200] if 'resp' in dir() else ''}")
        return ""


def fetch_weekly_events(date_from, date_to):
    """investing.com에서 dateFrom~dateTo 사이 5개국 3성 이벤트 조회 (국가별 순차 호출)."""
    print(f"  investing.com: {date_from.date()} ~ {date_to.date()} 5개국 3성 이벤트 조회...")

    all_html = []
    for code, label in COUNTRY_CODES.items():
        html = _fetch_country(date_from, date_to, code)
        rows_count = html.count('js-event-item') if html else 0
        print(f"    {label}({code}): {rows_count}건")
        if html:
            all_html.append(html)
        time.sleep(1)  # rate limit 회피

    combined_html = "".join(all_html)
    soup = BeautifulSoup(combined_html, "html.parser")
    rows = soup.find_all("tr", class_="js-event-item")

    events = []
    for row in rows:
        # 날짜+시간 — data-event-datetime 가 가장 신뢰 가능
        dt_str = row.get("data-event-datetime", "").strip()
        try:
            event_dt = datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S")
        except ValueError:
            try:
                event_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        time_td = row.find("td", class_="time")
        time_str = time_td.get_text(strip=True) if time_td else event_dt.strftime("%H:%M")

        # 국가 (flag span title)
        flag_td = row.find("td", class_="flagCur")
        country = ""
        if flag_td:
            flag_span = flag_td.find("span", title=True)
            if flag_span:
                country = FLAG_TITLE_MAP.get(flag_span.get("title", "").strip(), flag_span.get("title", "").strip())

        # 이벤트명
        event_td = row.find("td", class_="event")
        name = event_td.get_text(strip=True) if event_td else ""

        # 예상/이전
        fore_td = row.find("td", class_=lambda c: c and "fore" in c)
        prev_td = row.find("td", class_=lambda c: c and "prev" in c)
        forecast = fore_td.get_text(strip=True) if fore_td else ""
        previous = prev_td.get_text(strip=True) if prev_td else ""

        events.append({
            "datetime": event_dt,
            "date": event_dt.strftime("%Y-%m-%d"),
            "weekday": WEEKDAY_KR[event_dt.weekday()],
            "time": time_str if time_str and time_str != "전 일" else "종일",
            "country": country,
            "name": name,
            "forecast": forecast,
            "previous": previous,
        })

    events.sort(key=lambda e: e["datetime"])
    print(f"    수집 완료: {len(events)}건")
    return events


def build_rows(events):
    """헤더 + 이벤트 row 리스트 생성."""
    rows = [["날짜", "요일", "시간(KST)", "국가", "지표명", "예상", "이전"]]
    for e in events:
        rows.append([
            e["date"], e["weekday"], e["time"], e["country"],
            e["name"], e["forecast"], e["previous"],
        ])
    return rows


def create_sheet(gc, drive, title, rows):
    """`주식리뷰` 폴더에 새 스프레드시트 생성하고 데이터 기록."""
    sh = gc.create(title, folder_id=FOLDER_ID)
    print(f"  스프레드시트 생성: {title} ({sh.id})")
    ws = sh.sheet1
    ws.update_title("경제일정")
    ws.update(range_name="A1", values=rows)

    # 헤더 굵게 + 컬럼 너비 자동 조정
    try:
        ws.format("A1:G1", {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"})
        ws.freeze(rows=1)
    except Exception as e:
        print(f"  서식 적용 실패(무시): {e}")
    return sh


def main():
    today = datetime.now()
    # 다가오는 월요일 = today 가 일요일이면 +1, 아니면 다음 주 월요일
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 1  # today=Mon 이면 다음 월요일
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
    drive = build("drive", "v3", credentials=creds)

    create_sheet(gc, drive, title, rows)
    time.sleep(1)
    print("완료.")


if __name__ == "__main__":
    main()
