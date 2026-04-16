"""스카우터 타임시리즈/성과자료 누적 기록 — PC·브라우저 무관 자립 실행.

GitHub Actions cron(60분 간격)에서 호출. fetch + compute는 scouter_core에 공유.
여기선 그 결과를 타임시리즈/성과자료 시트에 append하고,
등급 전환/급변 시 이메일 알림 트리거한다.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scouter_core import collect_macro_scores


def main():
    scores = collect_macro_scores()

    # 디버그 로그
    keys = ["t_risk", "fx_risk", "c_risk", "vix_score", "macro_total"]
    print("[scouter_logger] scores={"
          + ", ".join(f"'{k}': {round(scores[k], 2)}" for k in keys)
          + "}", flush=True)
    print(f"[inputs] y2={scores['y2']} y10={scores['y10']} y30={scores['y30']} "
          f"| dxy={scores['dxy']} jpy={scores['jpy']} cny={scores['cny']}", flush=True)
    print(f"[inputs] wti={scores['wti']} brent={scores['brent']} gold={scores['gold']} "
          f"copper={scores['copper']} vix={scores['vix']}", flush=True)
    print(f"[inputs] chgs: wti={scores['wti_chg']} brent={scores['brent_chg']} "
          f"oil_avg={scores['oil_chg_avg']} gold={scores['gold_chg']} "
          f"silver={scores['silver_chg']} copper={scores['copper_chg']} "
          f"btc={scores['btc_chg']}", flush=True)

    if scores.get("macro_total") is None or all(
        (scores.get(k) or 0) == 0 for k in keys[:4]
    ):
        print("[scouter_logger] 모든 점수가 0 — fetch 실패로 간주, 기록 스킵", flush=True)
        return 1

    from notifier import log_timeseries_if_due, check_and_notify_macro
    payload = {k: scores[k] for k in keys}
    log_timeseries_if_due(payload)
    check_and_notify_macro(scores.get("macro_total"), scores=payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
