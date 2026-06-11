"""
오늘 발표된 경제지표 actual 수치를 주간 경제일정 시트의 '발표' 컬럼(H)에 채움.

미장(US PPI/CPI/NFP 등 21:30 KST 전후) 및 한장/아시아 발표 시간대에 매 30분 실행.

매칭 키: (date, time(KST), country, 한글 지표명).
  - Finnhub `event`(영문) → translate_indicator() 적용 후 시트의 '지표명'과 비교
  - 동일 row 발견 시 actual을 H열에 update

구버전 7컬럼 시트(발표 컬럼 없음)는 H1 헤더와 H열을 자동 확장 후 채움.
"""

import os
import re
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from datetime import datetime, timedelta, timezone

import gspread
import requests as req
from googleapiclient.discovery import build

from sheet_auth import get_credentials
from weekly_calendar import KST, fetch_investing_calendar

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")

_NUM_RE = re.compile(r"[+\-]?\d+(?:\.\d+)?")


def _norm_time(s: str) -> str:
    """'8:50'/'08:50'/'8:5' → 'HH:MM' 정규화. Google Sheets가 시간 셀의
    앞 0을 자동 제거해 매칭 키가 깨지는 문제 방지."""
    if not s:
        return ""
    parts = s.strip().split(":")
    if len(parts) == 2:
        try:
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        except ValueError:
            return s.strip()
    return s.strip()


def _parse_numeric(s: str):
    """포맷 문자열에서 선두 숫자 추출. '1.4%'→1.4, '-4.306M'→-4.306, '1,200'→1200."""
    if not s:
        return None
    m = _NUM_RE.search(s.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def annotate_actual(actual: str, forecast: str, previous: str) -> str:
    """발표치 옆에 ▲/▼ 부착. 비교 기준: 예상치 우선, 없으면 이전치. 동일·비교 불가 시 화살표 생략."""
    if not actual:
        return ""
    a = _parse_numeric(actual)
    if a is None:
        return actual
    ref = _parse_numeric(forecast)
    if ref is None:
        ref = _parse_numeric(previous)
    if ref is None:
        return actual
    if a > ref:
        return f"{actual} ▲"
    if a < ref:
        return f"{actual} ▼"
    return actual


def fetch_today_events():
    """오늘 KST 기준 핵심 매크로 이벤트 전체 반환.
    각 이벤트: dict(datetime, date, weekday, time, country, name, forecast, previous, actual)
    actual은 미발표면 "". 소스는 investing.com(weekly_calendar.fetch_investing_calendar 공유)."""
    today_kst = datetime.now(KST).date()
    return fetch_investing_calendar(today_kst, today_kst)


def find_latest_calendar_sheet(gc, drive):
    q = (f"name contains '주간 경제일정_' and '{FOLDER_ID}' in parents "
         f"and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
    files = drive.files().list(q=q, orderBy="name desc", pageSize=10,
                               fields="files(id,name)").execute().get("files", [])
    if not files:
        return None
    return gc.open_by_key(files[0]["id"]), files[0]["name"]


def ensure_actual_column(ws):
    """H1 헤더가 없으면 추가. 구버전 7컬럼 시트 호환."""
    headers = ws.row_values(1)
    if len(headers) >= 8 and headers[7].strip() == "발표":
        return
    ws.update_acell("H1", "발표")
    try:
        ws.format("H1", {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"})
    except Exception:
        pass
    print("  H1 '발표' 헤더 추가 (구버전 시트 확장)")


def main():
    print(f"=== fill_event_actuals — {datetime.now(KST).isoformat(timespec='seconds')} ===")

    events = fetch_today_events()
    print(f"  Finnhub 오늘 이벤트(필터 후): {len(events)}건")
    if not events:
        print("  대상 이벤트 없음 — 종료")
        return

    creds = get_credentials()
    gc = gspread.authorize(creds)
    drive = build("drive", "v3", credentials=creds)

    found = find_latest_calendar_sheet(gc, drive)
    if not found:
        print("  주간 경제일정 시트 없음 — 종료")
        return
    sh, sheet_name = found
    ws = sh.sheet1
    print(f"  대상 시트: {sheet_name}")

    ensure_actual_column(ws)

    rows = ws.get_all_values()
    # 기존 row 인덱스: (date, normalized time, country, name) → 1-based row number
    existing = {}
    for idx, r in enumerate(rows[1:], start=2):
        if len(r) < 5:
            continue
        key = (r[0], _norm_time(r[2]), r[3], r[4])
        existing[key] = (idx, r[7].strip() if len(r) > 7 else "")

    updates = []        # 기존 row의 H열 업데이트
    new_rows = []       # 시트에 없던 신규 이벤트
    filled, added = 0, 0
    for e in events:
        actual_annotated = annotate_actual(e["actual"], e["forecast"], e["previous"])
        key = (e["date"], _norm_time(e["time"]), e["country"], e["name"])
        if key in existing:
            row_idx, cur_actual = existing[key]
            if e["actual"] and not cur_actual:
                updates.append({"range": f"H{row_idx}", "values": [[actual_annotated]]})
                filled += 1
                print(f"    → H{row_idx} [{e['country']}] {e['name']}: {actual_annotated}")
        else:
            # 시트에 없던 이벤트 — 새 row로 append
            new_rows.append([
                e["date"], e["weekday"], e["time"], e["country"],
                e["name"], e["forecast"], e["previous"], actual_annotated,
            ])
            added += 1
            print(f"    + 신규 [{e['country']}] {e['name']} (actual={actual_annotated or '미발표'})")

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")

    if not updates and not new_rows:
        print("  변경 없음")
    else:
        print(f"  발표치 채움: {filled}건, 신규 row 추가: {added}건")


if __name__ == "__main__":
    main()
