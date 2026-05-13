"""
오늘 발표된 경제지표 actual 수치를 주간 경제일정 시트의 '발표' 컬럼(H)에 채움.

미장(US PPI/CPI/NFP 등 21:30 KST 전후) 및 한장/아시아 발표 시간대에 매 30분 실행.

매칭 키: (date, time(KST), country, 한글 지표명).
  - Finnhub `event`(영문) → translate_indicator() 적용 후 시트의 '지표명'과 비교
  - 동일 row 발견 시 actual을 H열에 update

구버전 7컬럼 시트(발표 컬럼 없음)는 H1 헤더와 H열을 자동 확장 후 채움.
"""

import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from datetime import datetime, timedelta, timezone

import gspread
import requests as req
from googleapiclient.discovery import build

from sheet_auth import get_credentials
from weekly_calendar import (
    COUNTRY_MAP, FINNHUB_URL, KST, translate_indicator,
)

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")


def fetch_today_actuals():
    """오늘 KST 기준 발표된 경제지표 actual을 (date, time, country, name) 키로 반환."""
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY 환경변수 미설정")

    today_kst = datetime.now(KST).date()
    # KST 하루는 UTC -9h~+15h 범위 — Finnhub UTC 기준이므로 양쪽 하루 여유로 조회
    date_from = (today_kst - timedelta(days=1)).strftime("%Y-%m-%d")
    date_to = (today_kst + timedelta(days=1)).strftime("%Y-%m-%d")

    resp = req.get(FINNHUB_URL, params={
        "from": date_from, "to": date_to, "token": api_key,
    }, timeout=20)
    resp.raise_for_status()
    raw = resp.json().get("economicCalendar", []) or []

    actuals = {}
    for item in raw:
        if (item.get("impact") or "").lower() != "high":
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

        if dt.date() != today_kst:
            continue

        actual = item.get("actual")
        if actual is None or actual == "":
            continue

        unit = (item.get("unit") or "").strip()
        actual_str = f"{actual}{unit}" if unit else str(actual)

        raw_name = (item.get("event") or "").strip()
        name = translate_indicator(raw_name)
        time_kst = dt.strftime("%H:%M") if dt.hour or dt.minute else "종일"

        key = (dt.strftime("%Y-%m-%d"), time_kst, country, name)
        actuals[key] = actual_str

    return actuals


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

    actuals = fetch_today_actuals()
    print(f"  Finnhub actual 수집: {len(actuals)}건")
    if not actuals:
        print("  발표된 actual 없음 — 종료")
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
    if len(rows) < 2:
        print("  시트 비어있음 — 종료")
        return

    updates = []
    matched = 0
    for idx, r in enumerate(rows[1:], start=2):  # row 2부터 (1-based)
        if len(r) < 5:
            continue
        date, _wd, time_kst, country, name = r[0], r[1], r[2], r[3], r[4]
        existing_actual = r[7] if len(r) > 7 else ""
        if existing_actual.strip():
            continue  # 이미 채워진 행은 건너뜀
        key = (date, time_kst, country, name)
        if key in actuals:
            updates.append({"range": f"H{idx}", "values": [[actuals[key]]]})
            matched += 1
            print(f"    → H{idx} [{country}] {name}: {actuals[key]}")

    if not updates:
        print("  매칭된 새 발표 없음")
        return

    ws.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"  {matched}건 갱신 완료")


if __name__ == "__main__":
    main()
