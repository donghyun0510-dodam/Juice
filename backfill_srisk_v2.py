# -*- coding: utf-8 -*-
"""스카우터_매크로_타임시리즈 시트의 과거 행에 신공식(C-Risk v2 / S-Risk / 매크로종합 v2)을
yfinance 종가로 소급 재계산해 끝쪽 3개 컬럼(K/L/M)에 채운다. 기존 컬럼은 미변경.

- t_risk / fx_risk 는 신·구 공식이 동일하므로 행에 이미 적재된 값을 재사용.
- C-Risk(신규)·S-Risk 만 yfinance 일봉으로 재구성.
- 행 날짜(KST 타임스탬프)가 가리키는 미국 세션 = KST 날짜 직전 거래일.
"""
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from datetime import datetime, date
import yfinance as yf
import gspread
from googleapiclient.discovery import build
from sheet_auth import get_credentials
from scouter_core import compute_c_risk, compute_s_risk

SHEET_NAME = "스카우터_매크로_타임시리즈"
FID = os.environ.get('GSHEET_FOLDER_ID', '1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv')
TICKERS = ["CL=F", "BZ=F", "GC=F", "HG=F", "SI=F", "^VIX", "DX-Y.NYB", "HYG", "IEF"]


def load_series():
    """티커별 종가 Series(날짜 index) 반환."""
    out = {}
    for t in TICKERS:
        h = yf.Ticker(t).history(start="2026-02-15", auto_adjust=True)["Close"].dropna()
        # tz 제거 후 date 인덱스
        h.index = [d.date() for d in h.index]
        out[t] = h
    return out


def val_prev(series, kst_d):
    """kst_date 직전 미국 세션의 (값, 직전값). 없으면 (None, None)."""
    dates = list(series.index)
    idx = [i for i, d in enumerate(dates) if d < kst_d]
    if not idx:
        return None, None
    pos = idx[-1]
    prev = float(series.iloc[pos - 1]) if pos >= 1 else None
    return float(series.iloc[pos]), prev


def pct(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return (cur / prev - 1.0) * 100.0


def credit_dev_asof(hyg, ief, kst_d, window=20):
    """kst_date 직전 세션까지의 HYG/IEF 20일 SMA 대비 편차%."""
    common = [d for d in hyg.index if d in ief.index and d < kst_d]
    if len(common) < window:
        return None
    ratio = [float(hyg.loc[d]) / float(ief.loc[d]) for d in common]
    sma = sum(ratio[-window:]) / window
    cur = ratio[-1]
    if sma <= 0:
        return None
    return (cur / sma - 1.0) * 100.0


def main():
    creds = get_credentials()
    gc = gspread.authorize(creds)
    drive = build('drive', 'v3', credentials=creds)
    q = f"name='{SHEET_NAME}' and '{FID}' in parents and trashed=false"
    fs = drive.files().list(q=q, fields='files(id,name)').execute().get('files', [])
    if not fs:
        print("시트 없음:", SHEET_NAME); return 1
    ws = gc.open_by_key(fs[0]['id']).sheet1
    vals = ws.get_all_values()
    print(f"행 수(헤더 포함): {len(vals)}")

    print("yfinance 다운로드 중...")
    S = load_series()

    out_rows = []
    for r in vals[1:]:
        ts = (r[0] or "")[:10]
        try:
            kst_d = datetime.strptime(ts, "%Y-%m-%d").date()
        except Exception:
            out_rows.append(["", "", ""]); continue

        wti, wti_p = val_prev(S["CL=F"], kst_d)
        brent, brent_p = val_prev(S["BZ=F"], kst_d)
        gold, gold_p = val_prev(S["GC=F"], kst_d)
        copper, copper_p = val_prev(S["HG=F"], kst_d)
        silver, silver_p = val_prev(S["SI=F"], kst_d)
        vix, _ = val_prev(S["^VIX"], kst_d)
        dxy, dxy_p = val_prev(S["DX-Y.NYB"], kst_d)

        copper_ton = copper * 2204.62 if copper else None
        oil_chg = None
        wc, bc = pct(wti, wti_p), pct(brent, brent_p)
        if wc is not None and bc is not None:
            oil_chg = (wc + bc) / 2
        elif wc is not None:
            oil_chg = wc
        elif bc is not None:
            oil_chg = bc

        c_new, _, _ = compute_c_risk(
            wti, brent, gold, copper_ton,
            silver=silver, oil_chg=oil_chg,
            silver_chg=pct(silver, silver_p), copper_chg=pct(copper, copper_p),
        )
        cdev = credit_dev_asof(S["HYG"], S["IEF"], kst_d)
        s_risk = compute_s_risk(vix, credit_dev_pct=cdev,
                                gold_chg=pct(gold, gold_p), dxy_chg=pct(dxy, dxy_p))

        # t_risk / fx_risk 는 행 기존값 재사용 (신·구 공식 동일)
        try:
            t_risk = float(r[1]); fx_risk = float(r[2])
            macro_v2 = min(t_risk * 0.30 + fx_risk * 0.25 + c_new * 0.25 + s_risk * 0.20, 100)
        except Exception:
            macro_v2 = ""

        out_rows.append([round(c_new, 1), round(s_risk, 1),
                         round(macro_v2, 1) if macro_v2 != "" else ""])
        print(f"{ts}: C v2={round(c_new,1)} S={round(s_risk,1)} 매크로v2={out_rows[-1][2]} "
              f"(구 C={r[3]} VIX={r[4]} 매크로={r[5]})")

    # 헤더 + 데이터 기록 (K:M = 11~13열)
    ws.update(range_name="K1", values=[["C-RISK_v2", "S-RISK", "매크로종합_v2"]])
    ws.update(range_name=f"K2:M{len(out_rows) + 1}", values=out_rows)
    print(f"\n백필 완료: {len(out_rows)}행 → K:M 컬럼")
    return 0


if __name__ == "__main__":
    sys.exit(main())
