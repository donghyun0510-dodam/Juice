"""
장중 신호 변화 스캐너 (헤드리스, Actions용).

흐름:
  1. 시트에서 추적 종목 로드
  2. yfinance로 현재 신호 판정
  3. 스냅샷(아침 baseline)과 비교해 변화 탐지
  4. 변화 있으면 시트 H열에 HH:MM before→after 기록
  5. 스냅샷은 **업데이트하지 않음** (baseline은 아침 리뷰만 갱신)

사용: python intraday_scan.py --market global  | --market korea
"""
import argparse
import sys

from market_common import analyze_trend_signals, get_baseline_signals, detect_changes
from sheet_event_writer import record_intraday_changes
from sheet_tickers import load_tracking_tickers_from_sheet


def run(market: str) -> int:
    universe, _names = load_tracking_tickers_from_sheet()
    if not universe:
        print("추적 종목 로드 실패 — 시트 접근 문제")
        return 1

    is_kr = market == "korea"
    tickers = []
    for label, lst in universe.items():
        is_kr_sector = label.startswith("🇰🇷")
        if is_kr == is_kr_sector:
            tickers.extend(lst)
    tickers = sorted(set(tickers))
    if not tickers:
        print(f"{market} 종목 없음")
        return 0

    print(f"[{market}] {len(tickers)}개 종목 스캔 중…")
    current = analyze_trend_signals(tickers, enrich_realtime=(market == "global"))
    if not current:
        print("신호 계산 실패")
        return 1

    baseline = get_baseline_signals()
    changes = detect_changes(current, baseline)
    if not changes:
        print("변화 없음")
        return 0

    print(f"변화 {len(changes)}개 감지:")
    for t, (b, a) in changes.items():
        print(f"  {t}: {b} → {a}")

    n = record_intraday_changes(changes, market=market)
    print(f"시트 H열 기록: {n}개")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["global", "korea"], required=True)
    args = p.parse_args()
    sys.exit(run(args.market))
