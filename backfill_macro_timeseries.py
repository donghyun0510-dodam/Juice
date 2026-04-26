"""일일 리뷰 시트 → 매크로 타임시리즈 백필 (one-shot).

새 T-RISK 임계치·가중치 기준으로 4/10 ~ 4/24 트레이딩 일자의
매크로 점수를 재계산해 비어있는 스카우터_매크로_타임시리즈 시트에 append.

사용:
    python backfill_macro_timeseries.py            # dry-run (점수만 출력)
    python backfill_macro_timeseries.py --apply    # 시트에 실제 append
"""
import argparse
import os
import pickle
import re
import sys
from datetime import datetime, timezone, timedelta

import gspread
import yfinance as yf
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scouter_core import (
    compute_t_risk, compute_fx_risk, compute_c_risk, compute_vix_score,
)

FOLDER = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")
TIMESERIES_NAME = "스카우터_매크로_타임시리즈"
HEADERS = ["날짜", "T-RISK", "FX-RISK", "C-RISK", "VIX점수", "매크로종합",
           "매크로종합 변동", "S&P500 종가", "S&P500 변동(%)", "구분"]

# 매핑: 트레이딩 일자 (YYYY-MM-DD) → 일일 리뷰 시트명
# (run date, manually deduped — Sun/Mon-morning manual runs that re-snap Fri close 제외)
SHEET_MAP = [
    ("2026-04-10", "증시 리뷰_260411"),  # Sat 수동 = Fri 종가
    ("2026-04-13", "증시 리뷰_260414"),  # Tue cron = Mon 종가
    ("2026-04-14", "증시 리뷰_260415"),
    ("2026-04-15", "증시 리뷰_260416"),
    ("2026-04-16", "증시 리뷰_260417"),
    ("2026-04-17", "증시 리뷰_260418"),  # Sat 수동 = Fri 종가
    ("2026-04-20", "증시 리뷰_260421"),  # Tue cron = Mon 종가
    ("2026-04-21", "증시 리뷰_260422"),
    ("2026-04-22", "증시 리뷰_260423"),
    ("2026-04-23", "증시 리뷰_260424"),
    ("2026-04-24", "증시 리뷰_260425"),  # Sat 수동 = Fri 종가
]


def _creds():
    with open(os.path.join(BASE_DIR, "token.pickle"), "rb") as f:
        c = pickle.load(f)
    if c and c.expired and c.refresh_token:
        c.refresh(Request())
        with open(os.path.join(BASE_DIR, "token.pickle"), "wb") as f:
            pickle.dump(c, f)
    return c


def _num(s):
    """문자열에서 숫자 추출 — 콤마, %, 괄호, +/- 처리."""
    if s is None or s == "":
        return None
    s = str(s).replace(",", "").replace("%", "").replace("(", "").replace(")", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def parse_review(rows):
    """글로벌 시트 rows에서 매크로 입력값 추출 — 라벨 기반.

    경제지표 개수에 따라 행이 밀리므로 R번호 대신 col C(체크포인트) 라벨로 찾는다.
    값=col D (4번 인덱스), %chg=col E (5번 인덱스).
    """
    # col C(=index 2) → row index 매핑
    label_row = {}
    for i, r in enumerate(rows):
        if len(r) >= 3 and r[2]:
            label_row[r[2].strip()] = i

    def by(label, col):
        i = label_row.get(label)
        if i is None:
            return None
        if col-1 < len(rows[i]):
            return rows[i][col-1]
        return None

    return {
        "y2":         _num(by("2년물", 4)),
        "y10":        _num(by("10년물", 4)),
        "y30":        _num(by("30년물", 4)),
        "dxy":        _num(by("DXY", 4)),
        "jpy":        _num(by("USD/JPY", 4)),
        "cny":        _num(by("USD/CNY", 4)),
        "brent":      _num(by("BRN", 4)),
        "brent_chg":  _num(by("BRN", 5)),
        "wti":        _num(by("WTI", 4)),
        "wti_chg":    _num(by("WTI", 5)),
        "copper":     _num(by("COPPER", 4)),
        "copper_chg": _num(by("COPPER", 5)),
        "silver":     _num(by("SILVER", 4)),
        "silver_chg": _num(by("SILVER", 5)),
        "vix":        _num(by("VIX", 4)),
        "gold":       _num(by("GOLD", 4)),
        "gold_chg":   _num(by("GOLD", 5)),
        "btc":        _num(by("BITCOIN", 4)),
        "btc_chg":    _num(by("BITCOIN", 5)),
        "sp500_chg":  _num(by("S&P500", 4)),
    }


def fetch_sp500_close(date_str: str) -> float | None:
    """주어진 날짜의 ^GSPC 종가."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        # period로 가져와 해당 날짜 row 추출
        h = yf.Ticker("^GSPC").history(start=date_str,
                                        end=(d + timedelta(days=2)).strftime("%Y-%m-%d"))
        if len(h) >= 1:
            return float(h["Close"].iloc[0])
    except Exception as e:
        print(f"  [warn] SP500 종가 조회 실패 {date_str}: {e}")
    return None


def compute_scores(parsed: dict) -> dict:
    """파싱된 입력으로 매크로 점수 계산 (현재 scouter_core 함수 사용)."""
    _, t_risk, _ = compute_t_risk(parsed["y2"], parsed["y10"], parsed["y30"])
    fx_risk = compute_fx_risk(parsed["dxy"], parsed["jpy"], parsed["cny"])
    # oil_chg = (wti+brent)/2 (둘 다 있을 때)
    wti_c, brn_c = parsed["wti_chg"], parsed["brent_chg"]
    oil_chg = ((wti_c + brn_c) / 2) if (wti_c is not None and brn_c is not None) else None
    c_risk, oil_avg, gc_ratio = compute_c_risk(
        parsed["wti"], parsed["brent"], parsed["gold"], parsed["copper"],
        silver=parsed.get("silver"), btc_chg=parsed["btc_chg"],
        oil_chg=oil_chg, gold_chg=parsed["gold_chg"],
        silver_chg=parsed.get("silver_chg"), copper_chg=parsed["copper_chg"],
    )
    vix_score = compute_vix_score(parsed["vix"])
    macro_total = min(
        t_risk * 0.30 + fx_risk * 0.25 + c_risk * 0.25 + vix_score * 0.20, 100
    )
    return {
        "t_risk": round(t_risk, 1),
        "fx_risk": round(fx_risk, 1),
        "c_risk": round(c_risk, 1),
        "vix_score": round(vix_score, 1),
        "macro_total": round(macro_total, 1),
        "oil_avg": oil_avg, "gc_ratio": gc_ratio,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="실제 시트에 append (없으면 dry-run)")
    args = ap.parse_args()

    creds = _creds()
    gc = gspread.authorize(creds)
    drive = build("drive", "v3", credentials=creds)

    # 시트명 → file_id 매핑
    q = (f"'{FOLDER}' in parents and mimeType='application/vnd.google-apps.spreadsheet' "
         "and trashed=false")
    files = drive.files().list(q=q, fields="files(id,name)", pageSize=200).execute().get("files", [])
    by_name = {f["name"]: f["id"] for f in files}

    rows_to_append = []
    prev_macro = None
    for trade_date, sheet_name in SHEET_MAP:
        sid = by_name.get(sheet_name)
        if not sid:
            print(f"[skip] {trade_date}: 시트 '{sheet_name}' 없음")
            continue
        sh = gc.open_by_key(sid)
        ws = sh.worksheets()[0]  # 글로벌 탭 (첫 번째)
        rows = ws.get_all_values()
        parsed = parse_review(rows)
        scores = compute_scores(parsed)
        sp500_close = fetch_sp500_close(trade_date)

        # 라벨: 미국 종가 직후 KST = 다음 영업일 05:00 (간이)
        d = datetime.strptime(trade_date, "%Y-%m-%d")
        label = (d + timedelta(days=1)).strftime("%Y-%m-%d") + " 05:00"

        cur_macro = scores["macro_total"]
        macro_delta = round(cur_macro - prev_macro, 1) if prev_macro is not None else ""
        prev_macro = cur_macro

        row = [
            label,
            scores["t_risk"], scores["fx_risk"], scores["c_risk"],
            scores["vix_score"], cur_macro, macro_delta,
            round(sp500_close, 2) if sp500_close else "",
            round(parsed["sp500_chg"], 2) if parsed["sp500_chg"] is not None else "",
            "백필",
        ]
        rows_to_append.append(row)
        print(f"[{trade_date}] T={scores['t_risk']:5.1f} FX={scores['fx_risk']:5.1f} "
              f"C={scores['c_risk']:5.1f} V={scores['vix_score']:5.1f} "
              f"→ 매크로종합={scores['macro_total']:5.1f}  | SP500={parsed['sp500_chg']}%")

    if not args.apply:
        print(f"\n[dry-run] {len(rows_to_append)}행 준비됨. --apply 옵션으로 실제 append.")
        return

    # apply
    sid = by_name.get(TIMESERIES_NAME)
    if not sid:
        print(f"[error] {TIMESERIES_NAME} 시트 없음", file=sys.stderr)
        sys.exit(1)
    ws = gc.open_by_key(sid).sheet1
    existing = ws.get_all_values()
    if not existing or existing[0] != HEADERS:
        ws.update(range_name="A1", values=[HEADERS])
    ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
    print(f"\n[apply] {len(rows_to_append)}행 append 완료 → {TIMESERIES_NAME}")


if __name__ == "__main__":
    main()
