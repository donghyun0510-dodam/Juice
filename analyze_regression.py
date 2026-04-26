"""매크로 위험지표 → S&P500 변동(%) 회귀분석.

사용법:
    python analyze_regression.py
    python analyze_regression.py --sheet 스카우터_매크로_타임시리즈
    python analyze_regression.py --lag 1 3 5

타임시리즈 시트(등간격 전수 기록)를 읽어
1) 단순회귀: 각 매크로 지표 → S&P500 변동(%)
2) 다중회귀: T-RISK + FX-RISK + C-RISK + VIX점수 → S&P500 변동(%)
3) 래그 회귀: 매크로종합(t) → S&P500 변동(%)(t+k)
를 numpy OLS로 계산해 콘솔 출력한다.
"""
import argparse
import os
import pickle
import sys

import gspread
import numpy as np
import pandas as pd
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")
DEFAULT_SHEET = "스카우터_매크로_타임시리즈"
SUB_COLS = ["T-RISK", "FX-RISK", "C-RISK", "VIX점수"]
TOTAL_COL = "매크로종합"
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
    for c in SUB_COLS + [TOTAL_COL, Y_COL]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["날짜"] = pd.to_datetime(df["날짜"], errors="coerce")
    df = df.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
    return df


def ols(X: np.ndarray, y: np.ndarray):
    """numpy 기반 OLS — 계수, 표준오차, t값, p값(근사), R², adj R², n, k 반환.

    X: (n, k+1) — 첫 컬럼이 절편(1).
    y: (n,)
    """
    from math import erf, sqrt
    n, kp1 = X.shape
    k = kp1 - 1  # 설명변수 개수 (절편 제외)
    # 정규방정식: beta = (X'X)^-1 X'y
    XtX = X.T @ X
    XtX_inv = np.linalg.pinv(XtX)
    beta = XtX_inv @ X.T @ y
    y_hat = X @ beta
    resid = y - y_hat
    df_resid = max(n - kp1, 1)
    sigma2 = float(resid @ resid) / df_resid
    cov = sigma2 * XtX_inv
    se = np.sqrt(np.diag(cov))
    t_stat = beta / np.where(se == 0, np.nan, se)
    # p값(양측, 정규분포 근사 — n이 작으면 보수적이지 않음)
    p_val = np.array([2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2)))) for t in t_stat])
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    adj_r2 = 1 - (1 - r2) * (n - 1) / df_resid if df_resid > 0 else float("nan")
    return {
        "beta": beta, "se": se, "t": t_stat, "p": p_val,
        "r2": r2, "adj_r2": adj_r2, "n": n, "k": k,
        "rmse": float(np.sqrt(sigma2)),
    }


def fmt_p(p):
    if np.isnan(p):
        return "  nan"
    if p < 0.001:
        return "<.001"
    return f"{p:.3f}"


def simple_reg(df: pd.DataFrame):
    print("\n=== 단순회귀: 각 지표 → " + Y_COL + " ===")
    print(f"{'지표':<10} {'계수':>10} {'SE':>8} {'t':>7} {'p':>7} {'R²':>7} {'n':>4}")
    print("-" * 60)
    y_full = df[Y_COL]
    for c in SUB_COLS + [TOTAL_COL]:
        if c not in df.columns:
            continue
        x = df[c]
        m = x.notna() & y_full.notna()
        if m.sum() < 5:
            continue
        X = np.column_stack([np.ones(m.sum()), x[m].values])
        y = y_full[m].values
        r = ols(X, y)
        b = r["beta"][1]; se = r["se"][1]; t = r["t"][1]; p = r["p"][1]
        print(f"{c:<10} {b:>10.4f} {se:>8.4f} {t:>7.2f} {fmt_p(p):>7} "
              f"{r['r2']:>7.3f} {r['n']:>4d}")


def multi_reg(df: pd.DataFrame):
    print("\n=== 다중회귀: 4개 하위지표 → " + Y_COL + " ===")
    cols = [c for c in SUB_COLS if c in df.columns]
    sub = df[cols + [Y_COL]].dropna()
    if len(sub) < len(cols) + 2:
        print(f"표본 부족: n={len(sub)}, 필요 ≥ {len(cols)+2}")
        return
    X = np.column_stack([np.ones(len(sub)), sub[cols].values])
    y = sub[Y_COL].values
    r = ols(X, y)
    print(f"n={r['n']}, R²={r['r2']:.3f}, adj R²={r['adj_r2']:.3f}, RMSE={r['rmse']:.3f}")
    print(f"{'변수':<10} {'계수':>10} {'SE':>8} {'t':>7} {'p':>7}")
    print("-" * 47)
    names = ["(절편)"] + cols
    for i, name in enumerate(names):
        print(f"{name:<10} {r['beta'][i]:>10.4f} {r['se'][i]:>8.4f} "
              f"{r['t'][i]:>7.2f} {fmt_p(r['p'][i]):>7}")


def lag_reg(df: pd.DataFrame, lags: list[int]):
    if not lags or TOTAL_COL not in df.columns:
        return
    print(f"\n=== 래그 회귀: {TOTAL_COL}(t) → {Y_COL}(t+k) ===")
    print(f"{'lag':>4} {'계수':>10} {'SE':>8} {'t':>7} {'p':>7} {'R²':>7} {'n':>4}")
    print("-" * 53)
    x_full = df[TOTAL_COL]
    y_full = df[Y_COL]
    for k in lags:
        y_shift = y_full.shift(-k)
        m = x_full.notna() & y_shift.notna()
        if m.sum() < 5:
            print(f"{k:>4} (표본 부족 n={int(m.sum())})")
            continue
        X = np.column_stack([np.ones(m.sum()), x_full[m].values])
        y = y_shift[m].values
        r = ols(X, y)
        b = r["beta"][1]; se = r["se"][1]; t = r["t"][1]; p = r["p"][1]
        print(f"{k:>4} {b:>10.4f} {se:>8.4f} {t:>7.2f} {fmt_p(p):>7} "
              f"{r['r2']:>7.3f} {r['n']:>4d}")


def diff_reg(df: pd.DataFrame):
    """레벨이 아닌 '변화량' 기반: ΔMacro(t) → SP500 변동(%)(t).

    레벨 회귀는 추세·자기상관에 휘둘리기 쉬워 변화량 회귀를 같이 본다.
    """
    if TOTAL_COL not in df.columns:
        return
    print(f"\n=== 변화량 회귀: Δ{TOTAL_COL} → {Y_COL} ===")
    d = df[[TOTAL_COL, Y_COL]].copy()
    d["dMacro"] = d[TOTAL_COL].diff()
    d = d.dropna(subset=["dMacro", Y_COL])
    if len(d) < 5:
        print(f"표본 부족: n={len(d)}")
        return
    X = np.column_stack([np.ones(len(d)), d["dMacro"].values])
    y = d[Y_COL].values
    r = ols(X, y)
    print(f"n={r['n']}, R²={r['r2']:.3f}, adj R²={r['adj_r2']:.3f}, RMSE={r['rmse']:.3f}")
    print(f"{'변수':<12} {'계수':>10} {'SE':>8} {'t':>7} {'p':>7}")
    print("-" * 49)
    names = ["(절편)", "Δ매크로종합"]
    for i, name in enumerate(names):
        print(f"{name:<12} {r['beta'][i]:>10.4f} {r['se'][i]:>8.4f} "
              f"{r['t'][i]:>7.2f} {fmt_p(r['p'][i]):>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=DEFAULT_SHEET)
    ap.add_argument("--lag", nargs="*", type=int, default=[1, 3, 5])
    args = ap.parse_args()

    print(f"시트 로드 중: {args.sheet}")
    df = load_sheet(args.sheet)
    print(f"표본 크기: n={len(df)} "
          f"(기간: {df['날짜'].iloc[0]} ~ {df['날짜'].iloc[-1]})")
    if len(df) < 10:
        print("[경고] n<10 — 결과의 통계적 의미가 매우 약함.")

    simple_reg(df)
    multi_reg(df)
    diff_reg(df)
    lag_reg(df, args.lag)

    print("\n해석 가이드:")
    print("  · 매크로 지표는 '위험도' → 계수가 음수일수록 모델 의도와 부합")
    print("  · |t| ≥ 2 (대략 p < 0.05) 이면 통계적 유의 신호")
    print("  · 레벨 회귀 R²가 높아도 추세 동조일 수 있음 → 변화량 회귀 같이 본다")


if __name__ == "__main__":
    main()
