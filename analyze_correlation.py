"""매크로 위험지표 vs S&P500 상관관계 분석.

사용법:
    python analyze_correlation.py
    python analyze_correlation.py --sheet 스카우터_매크로_타임시리즈
    python analyze_correlation.py --lag 1 3 5

타임시리즈 시트(등간격 전수 기록)를 읽어 Pearson/Spearman 상관,
요인별 상관, 래그 상관을 계산해 콘솔 출력한다.
"""
import argparse
import os
import pickle
import sys

import gspread
import pandas as pd
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")
DEFAULT_SHEET = "스카우터_매크로_타임시리즈"
MACRO_COLS = ["T-RISK", "FX-RISK", "C-RISK", "VIX점수", "매크로종합"]
Y_COL = "S&P500 변동(%)"


def _get_creds():
    token = os.path.join(BASE_DIR, "token.pickle")
    if os.path.exists(token):
        with open(token, "rb") as f:
            creds = pickle.load(f)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token, "wb") as f:
                pickle.dump(creds, f)
        return creds
    from sheet_auth import get_credentials
    return get_credentials()


def load_sheet(name: str) -> pd.DataFrame:
    creds = _get_creds()
    gc = gspread.authorize(creds)
    drive = build("drive", "v3", credentials=creds)
    q = (f"name='{name}' and '{FOLDER_ID}' in parents "
         "and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
    res = drive.files().list(q=q, fields="files(id, name)").execute()
    files = res.get("files", [])
    if not files:
        print(f"시트 '{name}'을(를) 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    ws = gc.open_by_key(files[0]["id"]).sheet1
    rows = ws.get_all_values()
    if len(rows) < 2:
        print("데이터 행이 없습니다.", file=sys.stderr)
        sys.exit(1)
    df = pd.DataFrame(rows[1:], columns=rows[0])
    # 숫자 변환
    for c in MACRO_COLS + [Y_COL]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
    return df


def print_correlations(df: pd.DataFrame, lags: list[int]) -> None:
    n = len(df)
    print(f"\n표본 크기: {n}")
    if n < 10:
        print("[경고] 최소 10개 이상 데이터 필요 (통계적 의미는 30+ 권장)")
    if Y_COL not in df.columns:
        print(f"'{Y_COL}' 컬럼 없음"); return

    y = df[Y_COL]
    print(f"\n=== 동시 상관 (각 지표 vs {Y_COL}) ===")
    print(f"{'지표':<12} {'Pearson':>10} {'Spearman':>10}")
    print("-" * 34)
    for c in MACRO_COLS:
        if c not in df.columns:
            continue
        x = df[c]
        mask = x.notna() & y.notna()
        if mask.sum() < 3:
            print(f"{c:<12} {'-':>10} {'-':>10}")
            continue
        p = x[mask].corr(y[mask], method="pearson")
        s = x[mask].corr(y[mask], method="spearman")
        print(f"{c:<12} {p:>10.3f} {s:>10.3f}")

    if lags:
        print(f"\n=== 래그 상관 (매크로종합(t) vs {Y_COL}(t+k), Pearson) ===")
        print(f"{'lag':>5} {'r':>10} {'n':>6}")
        print("-" * 23)
        x = df["매크로종합"]
        for k in lags:
            y_shift = y.shift(-k)
            mask = x.notna() & y_shift.notna()
            if mask.sum() < 3:
                print(f"{k:>5} {'-':>10} {int(mask.sum()):>6}")
                continue
            r = x[mask].corr(y_shift[mask])
            print(f"{k:>5} {r:>10.3f} {int(mask.sum()):>6}")

    # 해석 힌트
    print("\n해석: 매크로 지표는 위험도이므로 S&P500 변동률과 **음의 상관**이 기대됨.")
    print("      |r| ≥ 0.3 이면 약한 상관, ≥ 0.5 중간, ≥ 0.7 강한 상관.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=DEFAULT_SHEET, help="시트 이름")
    ap.add_argument("--lag", nargs="*", type=int, default=[1, 3, 5], help="래그 기간 (행 단위)")
    args = ap.parse_args()

    print(f"시트 로드 중: {args.sheet}")
    df = load_sheet(args.sheet)
    print_correlations(df, args.lag)


if __name__ == "__main__":
    main()
