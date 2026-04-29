"""스카우터_매크로_타임시리즈 시트 정리:
1) KST 일·월요일에 잘못 기록된 행 삭제 (미국 현물 휴장 — 직전 금요일 종가와 중복)
2) H열(S&P500 종가)을 각 행의 KST 시각 기준 직전 미국 현물(^GSPC) 마감 종가로 재기록
3) I열(S&P500 변동(%))을 직전 행 H 대비 % 변동으로 재계산
4) J열(구분)은 항상 빈 값(현물 단일)
"""
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import gspread
import yfinance as yf
from googleapiclient.discovery import build
from sheet_auth import get_credentials
from notifier import TIMESERIES_SHEET_NAME, PERF_FOLDER_ID, PERF_HEADERS

DATE_IDX = 0
H_IDX = 7
I_IDX = 8
J_IDX = 9

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def _parse_kst(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def cash_close_for_kst(dt_kst, hist):
    """dt_kst 시각 기준 가장 최근 완성된 ET 일봉 종가."""
    if dt_kst is None:
        return None
    dt_et = dt_kst.astimezone(ET)
    cutoff_date = dt_et.date()
    if dt_et.hour < 16:
        cutoff_date = cutoff_date - timedelta(days=1)
    eligible = hist[hist.index.date <= cutoff_date]
    if len(eligible) == 0:
        return None
    return float(eligible.iloc[-1])


def main():
    creds = get_credentials()
    gc = gspread.authorize(creds)
    drive = build("drive", "v3", credentials=creds)

    q = (f"name='{TIMESERIES_SHEET_NAME}' and '{PERF_FOLDER_ID}' in parents "
         "and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
    files = drive.files().list(q=q, fields="files(id, name)").execute().get("files", [])
    if not files:
        print(f"[backfill] '{TIMESERIES_SHEET_NAME}' sheet not found")
        return 1
    sh = gc.open_by_key(files[0]["id"])
    ws = sh.sheet1
    rows = ws.get_all_values()
    if len(rows) < 2:
        print("[backfill] no data rows")
        return 0
    if rows[0] != PERF_HEADERS:
        print(f"[backfill] header mismatch: {rows[0]}")
        return 1

    # 1) KST 일·월요일 행 삭제 (역순으로)
    deleted = 0
    for r_idx in range(len(rows) - 1, 0, -1):
        kst_str = rows[r_idx][DATE_IDX] if rows[r_idx] else ""
        dt_kst = _parse_kst(kst_str)
        if dt_kst is None:
            continue
        wd = dt_kst.weekday()  # Mon=0..Sun=6
        if wd in (0, 6):
            print(f"  delete row {r_idx + 1} [{kst_str}] (KST {'월' if wd==0 else '일'}요일)")
            ws.delete_rows(r_idx + 1)
            deleted += 1
    if deleted:
        rows = ws.get_all_values()  # 행 삭제 후 재로드

    # 2~3) H/I/J 재기록
    earliest_kst = rows[1][DATE_IDX] if len(rows) >= 2 else ""
    earliest_dt = _parse_kst(earliest_kst) or (datetime.now(tz=KST) - timedelta(days=180))
    start = (earliest_dt.astimezone(ET) - timedelta(days=14)).strftime("%Y-%m-%d")
    end = (datetime.now(tz=ET) + timedelta(days=2)).strftime("%Y-%m-%d")
    print(f"[backfill] ^GSPC daily download: {start} ~ {end}")
    hist = yf.download("^GSPC", start=start, end=end, progress=False, auto_adjust=False)["Close"].dropna()
    if hasattr(hist, "columns"):
        hist = hist.iloc[:, 0]
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)
    print(f"[backfill] {len(hist)} daily bars loaded ({hist.index[0].date()} ~ {hist.index[-1].date()})")

    new_h_col, new_i_col, new_j_col = [], [], []
    prev_close = None
    h_changed = i_changed = 0
    for r_idx, row in enumerate(rows[1:], start=2):
        kst_str = row[DATE_IDX] if len(row) > DATE_IDX else ""
        dt_kst = _parse_kst(kst_str)
        cur_close = cash_close_for_kst(dt_kst, hist)
        if cur_close is None:
            new_h, new_i = "", ""
        else:
            cur_close = round(cur_close, 2)
            new_h = cur_close
            if prev_close is None or prev_close == 0:
                new_i = ""
            else:
                new_i = round((cur_close - prev_close) / prev_close * 100, 2)
        new_h_col.append([new_h])
        new_i_col.append([new_i])
        new_j_col.append([""])
        old_h = row[H_IDX] if len(row) > H_IDX else ""
        old_i = row[I_IDX] if len(row) > I_IDX else ""
        if str(old_h) != str(new_h):
            h_changed += 1
            print(f"  row {r_idx} [{kst_str}]: H {old_h!r} -> {new_h!r}")
        if str(old_i) != str(new_i):
            i_changed += 1
            print(f"  row {r_idx} [{kst_str}]: I {old_i!r} -> {new_i!r}")
        if cur_close is not None:
            prev_close = cur_close

    last_row = len(rows)
    if last_row >= 2:
        ws.update(range_name=f"H2:H{last_row}", values=new_h_col)
        ws.update(range_name=f"I2:I{last_row}", values=new_i_col)
        ws.update(range_name=f"J2:J{last_row}", values=new_j_col)
    print(f"[backfill] done - deleted: {deleted}, H changed: {h_changed}, I changed: {i_changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
