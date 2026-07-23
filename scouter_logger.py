"""스카우터 타임시리즈/성과자료 누적 기록 — PC·브라우저 무관 자립 실행.

GitHub Actions cron(60분 간격)에서 호출. fetch + compute는 scouter_core에 공유.
여기선 그 결과를 타임시리즈/성과자료 시트에 append하고,
등급 전환/급변 시 이메일 알림 트리거한다.
"""
import json
import os
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from naver_macro import naver_settled_date
from scouter_core import collect_macro_scores

MACRO_SNAPSHOT_PATH = os.path.join(BASE_DIR, "macro_snapshot.json")

# collect_macro_scores(settled=True)가 네이버 정산 일별 바로 읽는 지표.
# 하나라도 대상 세션 바가 미게시면 그 시점 점수는 통째로 D-1이 된다.
SETTLED_KEYS = ("WTI", "BRENT", "GOLD", "SILVER", "COPPER")


def target_session_date(today):
    """settled_backfill.target_session_date와 동일 규칙 — 바뀌면 같이 고칠 것."""
    d = today - timedelta(days=1)
    if d.weekday() == 6:
        d -= timedelta(days=2)
    elif d.weekday() == 5:
        d -= timedelta(days=1)
    return d


def late_settled_bars(target):
    """대상 세션 바가 아직 미게시인 지표 -> ({미게시}, {전체 거래일}).

    미게시를 '휴장'으로 해석하지 않는다(DATA_PITFALLS 항목 2) — 기록을 미룰 뿐이다.
    """
    tgt = target.strftime("%Y-%m-%d")
    dates, late = {}, {}
    for k in SETTLED_KEYS:
        try:
            d = naver_settled_date(k)
        except Exception as e:
            d = f"ERR({e})"
        dates[k] = d
        if d != tgt:
            late[k] = d
    return late, dates


def _update_macro_snapshot(scores: dict) -> None:
    """미국 종가 직후 cron(KST 05:17) 시점의 점수를 yesterday_final로 고정.
    대시보드를 열지 않아도 baseline이 매일 갱신되어 일일 변동치가 누적되지 않게 한다."""
    keys = ["t_risk", "fx_risk", "c_risk", "vix_score", "s_risk", "macro_total"]
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
    # 미 장 마감 후 실행 — 원자재는 정산 일별 바를 써야 직전 세션 종가가 기록된다.
    # 라이브는 18:00 ET 재개장 뒤 다음 세션 값이라 타임시리즈가 오염됨.
    #
    # 게시 가드: 종가 직후엔 정산 바가 아직 D-1까지만 게시된 경우가 많다(LME 현물 구리
    # 상습). 그 시점에 기록하면 행 전체가 D-1로 박히고, 하루 1회 가드(22h) 때문에
    # 뒤늦게 정상값을 얻은 실행이 덮어쓰지도 못한다. 실제 사고: 2026-07-23 05:17 행이
    # WTI·Brent·금·구리·VIX 전부 07-21 값(C-RISK 17.4 vs 실제 29.4).
    # → 대상 세션 바가 다 게시되기 전이면 fetch도 하지 않고 빠진다. 뒤 회차가 기록한다.
    today = datetime.now()
    target = target_session_date(today)
    late, bar_dates = late_settled_bars(target)
    print(f"[scouter_logger] 정산 바 거래일(대상 {target:%Y-%m-%d}): {bar_dates}", flush=True)
    if late:
        if os.environ.get("FORCE_APPEND") == "1":
            print(f"[scouter_logger] ⚠️ 미게시 {late} — FORCE_APPEND=1이라 강행", flush=True)
        else:
            print(f"[scouter_logger] 대상 세션 정산 바 미게시 {late} "
                  f"— 기록 스킵(다음 회차가 기록)", flush=True)
            return 0

    scores = collect_macro_scores(settled=True)

    # 디버그 로그
    keys = ["t_risk", "fx_risk", "c_risk", "vix_score", "s_risk", "macro_total"]
    print("[scouter_logger] scores={"
          + ", ".join(f"'{k}': {round(scores[k], 2)}" for k in keys if scores.get(k) is not None)
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
    payload_keys = keys + ["c_risk_legacy", "macro_total_legacy"]
    payload = {k: scores.get(k) for k in payload_keys if scores.get(k) is not None}
    log_timeseries_if_due(payload)
    check_and_notify_macro(scores.get("macro_total"), scores=payload)
    _update_macro_snapshot(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
