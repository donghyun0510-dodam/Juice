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
    COUNTRY_MAP, FINNHUB_URL, KST, WEEKDAY_KR,
    translate_indicator, _is_relevant_event,
)

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")


def fetch_today_events():
    """오늘 KST 기준 핵심 매크로 이벤트 전체 반환.
    각 이벤트: dict(date, weekday, time, country, name, forecast, previous, actual)
    actual은 미발표면 ""."""
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

        if dt.date() != today_kst:
            continue

        unit = (item.get("unit") or "").strip()

        def _fmt(v):
            if v is None or v == "":
                return ""
            return f"{v}{unit}" if unit else str(v)

        raw_name = (item.get("event") or "").strip()
        name = translate_indicator(raw_name)
        time_kst = dt.strftime("%H:%M") if dt.hour or dt.minute else "종일"

        key = (dt.strftime("%Y-%m-%d"), time_kst, country, name)
        if key in seen:
            continue
        seen.add(key)

        events.append({
            "date": dt.strftime("%Y-%m-%d"),
            "weekday": WEEKDAY_KR[dt.weekday()],
            "time": time_kst,
            "country": country,
            "name": name,
            "forecast": _fmt(item.get("estimate")),
            "previous": _fmt(item.get("prev")),
            "actual": _fmt(item.get("actual")),
        })

    events.sort(key=lambda e: (e["time"], e["country"], e["name"]))
    return events


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
    # 기존 row 인덱스: (date, time, country, name) → 1-based row number
    existing = {}
    for idx, r in enumerate(rows[1:], start=2):
        if len(r) < 5:
            continue
        key = (r[0], r[2], r[3], r[4])
        existing[key] = (idx, r[7].strip() if len(r) > 7 else "")

    updates = []        # 기존 row의 H열 업데이트
    new_rows = []       # 시트에 없던 신규 이벤트
    filled, added = 0, 0
    for e in events:
        key = (e["date"], e["time"], e["country"], e["name"])
        if key in existing:
            row_idx, cur_actual = existing[key]
            if e["actual"] and not cur_actual:
                updates.append({"range": f"H{row_idx}", "values": [[e["actual"]]]})
                filled += 1
                print(f"    → H{row_idx} [{e['country']}] {e['name']}: {e['actual']}")
        else:
            # 시트에 없던 이벤트 — 새 row로 append
            new_rows.append([
                e["date"], e["weekday"], e["time"], e["country"],
                e["name"], e["forecast"], e["previous"], e["actual"],
            ])
            added += 1
            print(f"    + 신규 [{e['country']}] {e['name']} (actual={e['actual'] or '미발표'})")

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
