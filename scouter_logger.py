"""스카우터 타임시리즈/성과자료 누적 기록 — PC·브라우저 무관 자립 실행.

GitHub Actions cron (60분 간격)에서 호출되는 것을 전제로 설계됨.
market_dashboard.py의 점수 계산 로직과 **동일**하게 유지할 것.
대시보드가 변하면 여기도 맞춰 수정해야 함.

흐름:
  1) yfinance로 금리/환율/원자재/VIX 현재값(또는 최근 일봉) 수집
  2) compute_t_risk / fx_risk / c_risk / vix_score → macro_total
  3) notifier.log_timeseries_if_due(scores)  — 60분 가드는 구글 시트 상태로 공유
  4) notifier.check_and_notify_macro(total, scores) — 성과자료 + 이메일 알림
"""
import os
import sys
import re
from datetime import datetime, timezone

import requests
import yfinance as yf
import pandas as pd

# yfinance에 curl_cffi 세션 주입 — Yahoo rate limit 우회 (dashboard와 동일 기법)
try:
    from curl_cffi import requests as cffi_req
    _CFFI_SESSION = cffi_req.Session(impersonate="chrome")
    try:
        _CFFI_SESSION.get("https://kr.investing.com/", timeout=15)  # warmup
    except Exception:
        pass
    _ORIG_YF_TICKER = yf.Ticker
    def _yf_ticker_patched(symbol, *args, **kwargs):
        if "session" not in kwargs:
            kwargs["session"] = _CFFI_SESSION
        return _ORIG_YF_TICKER(symbol, *args, **kwargs)
    yf.Ticker = _yf_ticker_patched
    print("[scouter_logger] curl_cffi session 주입 완료", flush=True)
except Exception as e:
    print(f"[scouter_logger] curl_cffi 주입 실패 (fallback to plain yfinance): {e}", flush=True)

CNBC_YIELD_SYM = {"2Y": "US2Y", "10Y": "US10Y", "30Y": "US30Y"}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────
def _last_and_prev(ticker: str):
    """(last, prev) 반환 — 1분봉 최신가 우선, 없으면 일봉 종가."""
    try:
        tk = yf.Ticker(ticker)
        last = None
        try:
            m = tk.history(period="2d", interval="1m")["Close"].dropna()
            if len(m):
                last = float(m.iloc[-1])
        except Exception:
            pass
        d = tk.history(period="10d")["Close"].dropna()
        if last is None and len(d) >= 1:
            last = float(d.iloc[-1])
        prev = None
        if len(d) >= 2:
            # 일봉 마지막이 오늘이면 그 이전 행이 prev
            try:
                last_date = d.index[-1].date()
                today_utc = datetime.now(timezone.utc).date()
                if last_date == today_utc:
                    prev = float(d.iloc[-2])
                else:
                    prev = float(d.iloc[-1]) if last is not None and last != float(d.iloc[-1]) else float(d.iloc[-2])
            except Exception:
                prev = float(d.iloc[-2])
        return last, prev
    except Exception as e:
        print(f"[scouter_logger] {ticker} fetch fail: {e}", flush=True)
        return None, None


def _last(ticker: str):
    last, _ = _last_and_prev(ticker)
    return last


def _fetch_cnbc_yield(maturity: str):
    """CNBC로 실시간 국채 수익률 fetch (대시보드와 동일 소스)."""
    sym = CNBC_YIELD_SYM.get(maturity)
    if not sym:
        return None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}
        r = requests.get(f"https://www.cnbc.com/quotes/{sym}", headers=headers, timeout=15)
        m = re.search(r'QuoteStrip-lastPrice[^>]*>([0-9.]+)', r.text) or re.search(r'"last":"?([0-9.]+)"?', r.text)
        return float(m.group(1)) if m else None
    except Exception as e:
        print(f"[scouter_logger] cnbc {maturity} fail: {e}", flush=True)
        return None


def _yield(maturity: str, yf_fallback: str):
    v = _fetch_cnbc_yield(maturity)
    if v is not None:
        return v
    return _last(yf_fallback)


def _chg_pct(ticker: str):
    last, prev = _last_and_prev(ticker)
    if last is None or prev is None or prev == 0:
        return None
    return (last - prev) / prev * 100


def _chg_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"([+-]?\d+\.?\d*)", str(v))
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


# ─────────────────────────────────────────────
# 점수 계산 (market_dashboard.py와 동기 유지)
# ─────────────────────────────────────────────
def assess_risk(indicator, value):
    if value is None:
        return 0
    thresholds = {
        "2Y": (4.5, 4.8, 5.2),
        "10Y": (4.2, 4.5, 4.8),
        "30Y": (4.3, 4.6, 5.2),
    }
    if indicator not in thresholds:
        return 0
    t1, _t2, t3 = thresholds[indicator]
    if t3 <= t1:
        return 0
    score = (value - t1) / (t3 - t1) * 30
    return max(0, min(40, score))


def compute_t_risk(bond_2y, bond_10y, bond_30y):
    s2 = assess_risk("2Y", bond_2y)
    s10 = assess_risk("10Y", bond_10y)
    s30 = assess_risk("30Y", bond_30y)

    spread_score = 0
    spread = None
    if bond_10y is not None and bond_2y is not None:
        spread = bond_10y - bond_2y
        try:
            h10 = yf.Ticker("^TNX").history(period="6mo")["Close"]
            h2 = yf.Ticker("2YY=F").history(period="6mo")["Close"]
            df = pd.DataFrame({"10Y": h10, "2Y": h2}).dropna()
            if len(df) > 20:
                df["spread"] = df["10Y"] - df["2Y"]
                recent = df.tail(60)
                was_inv = (recent["spread"] < 0).any()
                if was_inv and spread > 0 and spread - recent["spread"].min() >= 0.5:
                    spread_score = 30
        except Exception:
            pass
        if spread_score == 0:
            if spread <= -0.5:
                spread_score = 20
            elif spread < 0:
                spread_score = 10

    total = s2 * 0.4 + s10 * 0.3 + s30 * 0.1 + spread_score * 0.2
    return total * (100 / 30)


def compute_fx_risk(dxy, jpy, cny):
    def fx_score(v, t1, t3):
        if v is None or t3 <= t1:
            return 0
        return max(0, min(40, (v - t1) / (t3 - t1) * 30))
    d = fx_score(dxy, 103, 108)
    j = fx_score(jpy, 145, 158)
    c = fx_score(cny, 7.15, 7.35)
    return min(d * 1.67 + j * 1.0 + c * 0.67, 100)


def compute_c_risk(wti, brent, gold, copper_ton,
                   silver_chg=None, btc_chg=None,
                   oil_chg=None, gold_chg=None, copper_chg=None):
    oil_score = 0
    if wti is not None and brent is not None:
        oil_avg = (wti + brent) / 2
        oil_score = max(0, min(40, (oil_avg - 85) / 20 * 30))
    elif wti is not None:
        oil_score = max(0, min(40, (wti - 85) / 20 * 30))

    gc_score = 0
    if gold is not None and copper_ton is not None and copper_ton > 0:
        gc_ratio = gold / copper_ton
        gc_score = max(0, min(25, (gc_ratio - 0.35) / 0.20 * 20))

    momentum = 0
    for chg in (oil_chg, gold_chg, silver_chg, copper_chg, btc_chg):
        v = _chg_num(chg)
        if v is None:
            continue
        momentum += max(0, abs(v) - 2) * 5
    momentum = min(momentum, 50)

    return min(oil_score * 2.0 + gc_score * 1.0 + momentum, 100)


def compute_vix_score(vix):
    if vix is None:
        return 0
    return max(0, min(100, (vix - 15) / 20 * 100))


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def collect_scores() -> dict:
    y2 = _yield("2Y", "2YY=F")
    y10 = _yield("10Y", "^TNX")
    y30 = _yield("30Y", "^TYX")
    dxy = _last("DX-Y.NYB")
    jpy = _last("JPY=X")
    cny = _last("CNY=X")
    wti = _last("CL=F")
    brent = _last("BZ=F")
    gold = _last("GC=F")
    copper_lb = _last("HG=F")
    copper_ton = copper_lb * 2204.62 if copper_lb else None
    vix = _last("^VIX")

    wti_chg = _chg_pct("CL=F")
    brent_chg = _chg_pct("BZ=F")
    oil_chg = None
    if wti_chg is not None and brent_chg is not None:
        oil_chg = (wti_chg + brent_chg) / 2
    elif wti_chg is not None:
        oil_chg = wti_chg
    gold_chg = _chg_pct("GC=F")
    silver_chg = _chg_pct("SI=F")
    copper_chg = _chg_pct("HG=F")
    btc_chg = _chg_pct("BTC-USD")

    print(f"[inputs] y2={y2} y10={y10} y30={y30} | dxy={dxy} jpy={jpy} cny={cny}", flush=True)
    print(f"[inputs] wti={wti} brent={brent} gold={gold} copper_lb={copper_lb} copper_ton={copper_ton} vix={vix}", flush=True)
    print(f"[inputs] chgs: wti={wti_chg} brent={brent_chg} oil_avg={oil_chg} gold={gold_chg} silver={silver_chg} copper={copper_chg} btc={btc_chg}", flush=True)

    t_risk = compute_t_risk(y2, y10, y30)
    fx_risk = compute_fx_risk(dxy, jpy, cny)
    c_risk = compute_c_risk(wti, brent, gold, copper_ton,
                            silver_chg=silver_chg, btc_chg=btc_chg,
                            oil_chg=oil_chg, gold_chg=gold_chg,
                            copper_chg=copper_chg)
    vix_score = compute_vix_score(vix)
    macro_total = min(t_risk * 0.30 + fx_risk * 0.25 + c_risk * 0.25 + vix_score * 0.20, 100)

    return {
        "t_risk": t_risk,
        "fx_risk": fx_risk,
        "c_risk": c_risk,
        "vix_score": vix_score,
        "macro_total": macro_total,
    }


def main():
    scores = collect_scores()
    print(f"[scouter_logger] scores={ {k: round(v, 2) if isinstance(v, (int, float)) else v for k, v in scores.items()} }", flush=True)

    # 필수 점수 결손 시 기록 스킵 (전부 0이면 fetch 실패 가능성)
    if scores.get("macro_total") is None or all(
        (scores.get(k) or 0) == 0 for k in ("t_risk", "fx_risk", "c_risk", "vix_score")
    ):
        print("[scouter_logger] 모든 점수가 0 — fetch 실패로 간주, 기록 스킵", flush=True)
        return 1

    from notifier import log_timeseries_if_due, log_perf_if_due, check_and_notify_macro
    log_timeseries_if_due(scores)
    log_perf_if_due(scores)
    # 등급 전환/급변 시에만 이메일 발송 (일반 cron 실행에선 조용함)
    check_and_notify_macro(scores.get("macro_total"), scores=scores)
    return 0


if __name__ == "__main__":
    sys.exit(main())
