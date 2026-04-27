"""
'투자 수익률 기록' 시트 — 주식 탭 월간 정산 (매월 27일 자동 실행).

사용자가 행에 A(월), B(평가액), C(입출금)를 수동 입력하면,
이 스크립트가 D(월 손익), E(월 수익률), F(누적 수익률)를 정적 값으로 채운다.

Cron 트리거: 매월 27일 KST 22:00 (UTC 13:00).
manual: `python monthly_stock_returns.py [--month YYYY-MM]`
"""
import os, sys, io, argparse, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from zoneinfo import ZoneInfo

import gspread
from sheet_auth import get_credentials

SHEET_ID = os.environ.get("RETURNS_SHEET_ID", "1Jnm55njxzB8NxY0LJAlrum8WPMHuD-EdeMlpHMQfHrY")


def _to_float(x):
    if x in ("", None):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    return float(str(x).replace(",", ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="대상 월 YYYY-MM (기본: 오늘 KST 기준 현재 월)")
    args = ap.parse_args()

    if args.month:
        target_month = args.month
    else:
        target_month = datetime.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m")

    print(f"대상 월: {target_month}")

    gc = gspread.authorize(get_credentials())
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet("주식")

    data = ws.get("A1:F100", value_render_option="UNFORMATTED_VALUE")

    # 대상 행 탐색 (A열 = target_month)
    target_row = None
    for i, row in enumerate(data, start=1):
        if i < 2:
            continue
        if row and len(row) > 0 and str(row[0]).strip() == target_month:
            target_row = i
            break

    if target_row is None:
        print(f"[skip] {target_month} 행이 없음 — A열에 월을 먼저 입력해주세요")
        return

    if target_row == 2:
        print(f"[skip] {target_month} 은 베이스라인 행 — 손익/수익률 계산 없음")
        return

    curr = data[target_row - 1]
    B_curr = _to_float(curr[1] if len(curr) > 1 else None)
    C_curr = _to_float(curr[2] if len(curr) > 2 else None) or 0.0

    if B_curr is None:
        print(f"[skip] B{target_row} (평가액) 비어있음 — 평가액을 먼저 입력해주세요")
        return

    prev = data[target_row - 2]
    B_prev = _to_float(prev[1] if len(prev) > 1 else None)
    if B_prev is None:
        print(f"[skip] B{target_row-1} (전월 평가액) 비어있음")
        return

    B_first = _to_float(data[1][1])

    sum_c = 0.0
    for i in range(1, target_row):
        r = data[i]
        v = _to_float(r[2] if len(r) > 2 else None)
        if v is not None:
            sum_c += v

    monthly_pnl = B_curr - B_prev - C_curr
    denom = B_prev + C_curr
    monthly_ret = (monthly_pnl / denom) if denom != 0 else 0.0
    cum_ret = ((B_curr - sum_c) / B_first - 1) if B_first else 0.0

    ws.update(values=[[monthly_pnl, monthly_ret, cum_ret]],
              range_name=f"D{target_row}:F{target_row}",
              value_input_option="RAW")
    print(f"갱신: D{target_row}={monthly_pnl:,.0f}  "
          f"E{target_row}={monthly_ret*100:.2f}%  "
          f"F{target_row}={cum_ret*100:.2f}%")


if __name__ == "__main__":
    main()
