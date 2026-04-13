"""
증시 리뷰 스프레드시트에서 추적 종목 리스트 읽기 (source of truth).
"""
import os
import pickle
import re

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import gspread

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "token.pickle")
FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")


def _get_creds():
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    except Exception:
        return None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)
            return creds
        except Exception:
            return None
    return None


def _find_latest_review_sheet(drive):
    query = (f"'{FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.spreadsheet' "
             "and name contains '증시 리뷰' and trashed=false")
    r = drive.files().list(
        q=query,
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=5,
    ).execute()
    files = r.get("files", [])
    return files[0] if files else None


def _parse_section5(ws_values):
    """'5.섹터/종목' 섹션에서 (sector, row) 추출.
    row는 [marker, sector, col_c, chg, ...] 형태 원본 그대로."""
    out = []
    in_sec = False
    cur_sector = ""
    for r in ws_values:
        r = r + [""] * max(0, 10 - len(r))
        a, b = r[0], r[1]
        if a and a.startswith("5."):
            in_sec = True
            if b:
                cur_sector = b
            out.append((cur_sector, r))
            continue
        if not in_sec:
            continue
        if a and re.match(r"^[1-9]\.", a):
            break
        if b:
            cur_sector = b
        if r[2]:
            out.append((cur_sector, r))
    return out


def load_tracking_tickers_from_sheet():
    """최신 증시 리뷰 시트의 글로벌·국장 탭에서 추적 종목 반환.
    반환: (universe_dict, names_dict)
    - universe_dict: {sector_label: [ticker...]}
    - names_dict: {ticker: korean_name}
    실패 시 빈 dict.
    """
    creds = _get_creds()
    if not creds:
        return {}, {}
    try:
        drive = build("drive", "v3", credentials=creds)
        gc = gspread.authorize(creds)
        latest = _find_latest_review_sheet(drive)
        if not latest:
            return {}, {}
        sh = gc.open_by_key(latest["id"])
        universe = {}
        names = {}
        # 글로벌 탭 (미국) — col C에 티커
        try:
            ws = sh.worksheet("글로벌")
            values = ws.get_all_values()
            for sector, row in _parse_section5(values):
                ticker = (row[2] or "").strip()
                if not ticker:
                    continue
                # 지수·특징주(일시) 제외
                if ticker.startswith("^"):
                    continue
                if sector in ("특징주",):
                    continue
                label = f"🇺🇸 {sector}" if sector else "🇺🇸 기타"
                universe.setdefault(label, []).append(ticker)
        except Exception:
            pass
        # 국장 탭 (한국) — col C에 한글명, col F에 티커
        try:
            ws = sh.worksheet("국장")
            values = ws.get_all_values()
            for sector, row in _parse_section5(values):
                if sector in ("특징주",):
                    continue
                ticker = (row[5] if len(row) > 5 else "").strip()
                name = (row[2] or "").strip()
                if not ticker or not re.match(r"^\d{6}\.K[SQ]$", ticker):
                    continue
                label = f"🇰🇷 {sector}" if sector else "🇰🇷 기타"
                universe.setdefault(label, []).append(ticker)
                if name:
                    names[ticker] = name
        except Exception:
            pass
        return universe, names
    except Exception:
        return {}, {}
