"""스카우터 타임시리즈/성과자료 누적 기록 — PC·브라우저 무관 자립 실행.

GitHub Actions cron(60분 간격)에서 호출. fetch + compute는 scouter_core에 공유.
여기선 그 결과를 타임시리즈/성과자료 시트에 append하고,
등급 전환/급변 시 이메일 알림 트리거한다.
"""
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scouter_core import collect_macro_scores

MACRO_SNAPSHOT_PATH = os.path.join(BASE_DIR, "macro_snapshot.json")


def _update_macro_snapshot(scores: dict) -> None:
    """미국 종가 직후 cron(KST 05:17) 시점의 점수를 yesterday_final로 고정.
    대시보드를 열지 않아도 baseline이 매일 갱신되어 일일 변동치가 누적되지 않게 한다."""
    keys = ["t_risk", "fx_risk", "c_risk", "vix_score", "macro_total"]
    final = {k: scores.get(k) for k in keys if scores.get(k) is not None}
    if not final:
        return
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(MACRO_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "yesterday_final": final,
                "today_latest": {**final, "date": today_str},
                "ts": datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)
        print(f"[scouter_logger] macro_snapshot.json yesterday_final 갱신 완료", flush=True)
    except Exception as e:
        print(f"[scouter_logger] macro_snapshot 저장 실패: {e}", flush=True)


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
    _update_macro_snapshot(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
