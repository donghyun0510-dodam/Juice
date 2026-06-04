"""
Long Sign 풀스캔 헤드리스 러너 (GitHub Actions 전용).

종전 market_dashboard.py 가 앱 프로세스 안에서 장종료 후 1회 수행하던
S&P500 / 한국 전종목 Long sign 스캔을 분리 실행한다. 결과를 디스크 JSON
(us_long_scan_daily.json / kr_long_scan_daily.json)으로 떨어뜨리고 Actions가
commit-back 하면, 대시보드는 이 JSON 만 읽으면 되어 OOM 위험이 사라진다.

사용:
  python long_scan.py --market global   # S&P500
  python long_scan.py --market korea     # KOSPI/KOSDAQ
  python long_scan.py --market both
"""
import argparse
import json
import os
import sys
from datetime import datetime

from long_scan_core import scan_sp500, scan_kr

US_LONG_SCAN_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_long_scan_daily.json")
KR_LONG_SCAN_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kr_long_scan_daily.json")
PROMOTED_KR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "promoted_kr.json")

# 하드코딩 폴백 (시트 접근 실패 시) — 대시보드 STOCK_UNIVERSE 와 동일.
_FALLBACK_TRACKED = [
    "NVDA", "AMD", "AVGO", "MU", "TXN", "ASML", "LRCX", "AMAT", "INTC", "MRVL",
    "AAPL", "MSFT", "GOOG", "AMZN", "META", "NFLX", "ORCL", "CRM", "ADBE", "NOW",
    "PLTR", "CRWD", "DDOG", "SNOW", "ETN", "VRT", "GEV", "SMR", "CEG", "TLN",
    "BWXT", "JPM", "BAC", "GS", "CAT", "DE", "XOM", "COP", "TSLA",
    "005930.KS", "000660.KS", "042700.KS", "373220.KS", "006400.KS", "005380.KS",
    "005490.KS", "051910.KS", "035420.KS", "035720.KS", "028260.KS",
    "105560.KS", "055550.KS",
]


def _load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def build_exclude_set():
    """추적 중인 종목 = 스캔 제외 대상. 시트(source of truth) + 승격 종목.
    시트 접근 실패 시 하드코딩 폴백."""
    tracked = set()
    try:
        from sheet_tickers import load_tracking_tickers_from_sheet
        universe, _names = load_tracking_tickers_from_sheet()
        for lst in (universe or {}).values():
            tracked.update(lst)
    except Exception as e:
        print(f"[exclude] sheet load fail: {e}", flush=True)
    if not tracked:
        tracked.update(_FALLBACK_TRACKED)
    # 자동 승격된 KR 종목도 추적 대상 → 스캔 제외
    tracked.update(_load_json(PROMOTED_KR_PATH).keys())
    return tracked


def run_global(exclude_set, today_str):
    long_only, sector_map, err = scan_sp500(exclude_set)
    if err:
        print(f"[global] scan error: {err}", flush=True)
        return 1
    with open(US_LONG_SCAN_CACHE, "w", encoding="utf-8") as f:
        json.dump({"date": today_str, "long_only": long_only, "sector_map": sector_map},
                  f, ensure_ascii=False)
    print(f"[global] Long sign {len(long_only)}개 → {US_LONG_SCAN_CACHE}", flush=True)
    return 0


def run_korea(exclude_set, today_str):
    long_only, name_map, err = scan_kr(exclude_set)
    if err:
        print(f"[korea] scan error: {err}", flush=True)
        return 1
    with open(KR_LONG_SCAN_CACHE, "w", encoding="utf-8") as f:
        json.dump({"date": today_str, "long_only": long_only, "name_map": name_map},
                  f, ensure_ascii=False)
    print(f"[korea] Long sign {len(long_only)}개 → {KR_LONG_SCAN_CACHE}", flush=True)
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["global", "korea", "both"], required=True)
    args = p.parse_args()

    today_str = datetime.now().strftime("%Y-%m-%d")
    exclude_set = build_exclude_set()
    print(f"제외(추적) 종목 {len(exclude_set)}개", flush=True)

    rc = 0
    if args.market in ("global", "both"):
        rc |= run_global(exclude_set, today_str)
    if args.market in ("korea", "both"):
        rc |= run_korea(exclude_set, today_str)
    return rc


if __name__ == "__main__":
    sys.exit(main())
