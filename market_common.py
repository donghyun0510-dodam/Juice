"""
공통 모듈: 매크로 위험도 계산 + 4개 신호 판정

daily_review.py, market_dashboard.py 양쪽에서 공유하는 단일 진실 원본.
규칙은 .claude/agents/ 의 signal-judge.md / macro-strategist.md 에 정의됨.
"""

import yfinance as yf
import pandas as pd
import json
import os
from datetime import datetime


# ══════════════════════════════════════════
# 매크로 위험도 계산 (macro-strategist 규칙)
# ══════════════════════════════════════════

def assess_risk(indicator, value):
    if value is None:
        return "N/A", 0
    thresholds = {
        "VIX": (20, 25, 30),
        "DXY": (102, 105, 110),
        "2Y":  (4.5, 4.8, 5.2),
        "10Y": (4.2, 4.5, 4.8),
        "30Y": (4.3, 4.6, 5.2),
        "WTI": (85, 95, 100),
        "BRN": (90, 100, 105),
        "USD/JPY": (145, 152, 158),
        "USD/CNY": (7.15, 7.25, 7.35),
    }
    if indicator in thresholds:
        t1, t2, t3 = thresholds[indicator]
        if value <= t1: return "안정", 0
        elif value <= t2: return "주의", 10
        elif value <= t3: return "위험", 20
        else: return "고위험", 30
    return "N/A", 0


def compute_t_risk(bond_2y, bond_10y, bond_30y):
    score_2y = assess_risk("2Y", bond_2y)[1]
    score_10y = assess_risk("10Y", bond_10y)[1]
    score_30y = assess_risk("30Y", bond_30y)[1]

    spread = None
    spread_score = 0
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
            if spread <= -0.5: spread_score = 20
            elif spread < 0: spread_score = 10

    total = score_2y * 0.4 + score_10y * 0.3 + score_30y * 0.1 + spread_score * 0.2
    normalized = total * (100 / 30)
    return total, normalized, spread


def compute_fx_risk(dxy, jpy, cny):
    def fx_score(value, t1, t2, t3):
        if value is None: return 0
        if value < t1: return 0
        elif value <= t2: return 10
        elif value <= t3: return 20
        else: return 30
    d = fx_score(dxy, 103, 105, 108)
    j = fx_score(jpy, 145, 152, 158)
    c = fx_score(cny, 7.15, 7.25, 7.35)
    return min(d * 1.67 + j * 1.0 + c * 0.67, 100)


def compute_c_risk(wti, brent, gold, copper):
    oil_score = 0
    oil_avg = None
    if wti is not None and brent is not None:
        oil_avg = (wti + brent) / 2
        if oil_avg < 85: oil_score = 0
        elif oil_avg <= 95: oil_score = 10
        elif oil_avg <= 105: oil_score = 20
        else: oil_score = 30

    gc_score = 0
    gc_ratio = None
    if gold is not None and copper is not None and copper > 0:
        gc_ratio = gold / copper
        if gc_ratio < 0.35: gc_score = 0
        elif gc_ratio <= 0.45: gc_score = 10
        elif gc_ratio <= 0.55: gc_score = 20
        else: gc_score = 30

    return min(oil_score * 2 + gc_score * 1.33, 100), oil_avg, gc_ratio


def compute_vix_score(vix_val):
    if vix_val is None: return 0
    if vix_val <= 20: return 0
    elif vix_val <= 25: return 33
    elif vix_val <= 30: return 67
    else: return 100


def risk_grade(score, thresholds=(25, 50, 75)):
    t1, t2, t3 = thresholds
    if score <= t1: return "안정"
    elif score <= t2: return "주의"
    elif score <= t3: return "위험"
    else: return "고위험"


def compute_macro_total(t_risk, fx_risk, c_risk, vix_score):
    """종합 매크로 점수 (macro-strategist 가중치)"""
    total = t_risk * 0.30 + fx_risk * 0.25 + c_risk * 0.25 + vix_score * 0.20
    return min(total, 100)


# ══════════════════════════════════════════
# 4개 신호 판정 (signal-judge 규칙)
# ══════════════════════════════════════════

def classify_signal(close: pd.Series) -> dict:
    """단일 종목 Close 시리즈로 신호 분류.

    Returns:
        dict with keys: tag, last, chg, ma50, ma200, high_52w, dd_52w
        tag ∈ {long, sell, short, cover, hold_long, hold_sell, neutral}
        데이터 부족(200일 미만)이면 None 반환.
    """
    if close is None or len(close) < 200:
        return None
    close = close.dropna()
    if len(close) < 200:
        return None

    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    last = close.iloc[-1]
    prev = close.iloc[-2]
    ma50_last = ma50.iloc[-1]
    ma200_last = ma200.iloc[-1]
    high_52w = close.max()

    # === Long Sign ===
    near_52w_high = last >= high_52w * 0.97
    strong_momentum = False
    if len(close) >= 11:
        close_5d = last / close.iloc[-6] - 1
        close_10d = last / close.iloc[-11] - 1
        strong_momentum = close_5d >= 0.05 and close_10d >= 0.10

    cond1 = last >= high_52w * 1.001

    cond2 = False
    if len(close) >= 120:
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        ma120 = close.rolling(120).mean()
        aligned = ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]
        slope_up = ma20.iloc[-1] > ma20.iloc[-5] if len(ma20.dropna()) >= 5 else False
        if aligned and slope_up and near_52w_high and strong_momentum:
            cond2 = True

    cond3 = False
    if len(close) >= 21:
        box_high = close.iloc[-21:-1].max()
        if last > box_high * 1.01 and near_52w_high and strong_momentum:
            cond3 = True

    long_sign = cond1 or cond2 or cond3

    # === Sell Sign (고점권에서 꺾임) ===
    sell_sign = False
    if len(close) >= 30:
        prior = close.iloc[-30:-5]
        recent = close.iloc[-5:]
        if len(prior) >= 10 and prior.iloc[0] > 0 and recent.iloc[0] > 0:
            ps = (prior.iloc[-1] - prior.iloc[0]) / prior.iloc[0] / len(prior)
            rs = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] / len(recent)
            at_high_zone = prior.max() >= high_52w * 0.95
            # 5거래일 전 종가 대비 3% 이상 추가 하락 시에만 Sell (중간 크기 눌림 제외)
            broke_5d_ago = last < close.iloc[-6] * 0.97
            if ps > 0 and rs < 0 and abs(rs) > ps and at_high_zone and broke_5d_ago:
                sell_sign = True

    # === Short Sign ===
    short_sign = False
    if last <= close.min() * 1.001:
        short_sign = True
    if not short_sign and len(close) >= 30:
        prior_s = close.iloc[-30:-5]
        recent_s = close.iloc[-5:]
        if len(prior_s) >= 10 and prior_s.iloc[0] > 0 and recent_s.iloc[0] > 0:
            ps = (prior_s.iloc[-1] - prior_s.iloc[0]) / prior_s.iloc[0] / len(prior_s)
            rs = (recent_s.iloc[-1] - recent_s.iloc[0]) / recent_s.iloc[0] / len(recent_s)
            rec_cum = (recent_s.iloc[-1] / recent_s.iloc[0] - 1) * 100
            # 최소 낙폭 -4% 필터 추가 (소폭 눌림 차단)
            if rs < 0 and rec_cum <= -4 and abs(rs) > abs(ps) and ps <= 0:
                short_sign = True

    # === Short Cover Sign (장기 하락 반전) ===
    cover_sign = False
    if len(close) >= 40:
        prior_c = close.iloc[-30:-5]
        recent_c = close.iloc[-5:]
        if len(prior_c) >= 10 and prior_c.iloc[0] > 0 and recent_c.iloc[0] > 0:
            pcs = (prior_c.iloc[-1] - prior_c.iloc[0]) / prior_c.iloc[0] / len(prior_c)
            rcs = (recent_c.iloc[-1] - recent_c.iloc[0]) / recent_c.iloc[0] / len(recent_c)
            ltd = last < ma200_last and last < high_52w * 0.85
            recent_low = close.iloc[-15:].min()
            prev_low = close.iloc[-40:-15].min()
            hl = recent_low > prev_low
            bounce = recent_low > 0 and (last / recent_low - 1) >= 0.10
            if pcs < 0 and rcs > 0 and abs(rcs) > abs(pcs) and ltd and (hl or bounce):
                cover_sign = True

    # === 분류 (우선순위: Long > Sell > Short > Cover > Hold) ===
    if long_sign: tag = "long"
    elif sell_sign: tag = "sell"
    elif short_sign: tag = "short"
    elif cover_sign: tag = "cover"
    elif last > ma50_last > ma200_last: tag = "hold_long"
    elif last < ma50_last < ma200_last: tag = "hold_sell"
    else: tag = "neutral"

    return {
        "tag": tag,
        "last": float(last),
        "chg": float((last / prev - 1) * 100),
        "ma50": float(ma50_last),
        "ma200": float(ma200_last),
        "high_52w": float(high_52w),
        "dd_52w": float((last / high_52w - 1) * 100),
    }


def analyze_trend_signals(tickers: list) -> dict:
    """다수 종목 일괄 분석. yfinance batch download 사용."""
    if not tickers:
        return {}
    try:
        data = yf.download(tickers, period="1y", auto_adjust=True, progress=False, group_by="ticker")
    except Exception:
        return {}
    results = {}
    for t in tickers:
        try:
            if len(tickers) == 1:
                close = data["Close"].dropna()
            else:
                close = data[t]["Close"].dropna()
            r = classify_signal(close)
            if r is not None:
                results[t] = r
        except Exception:
            continue
    return results


# ══════════════════════════════════════════
# 신호 라벨 축약 (이벤트 기록용)
# ══════════════════════════════════════════

SIGNAL_SHORT = {
    "long": "long", "sell": "sell", "short": "short", "cover": "cover",
    "hold_long": "hL", "hold_sell": "hS", "neutral": "N",
}

SIGNAL_LABEL_KR = {
    "long": "Long Sign",
    "sell": "Sell Sign",
    "short": "Short Sign",
    "cover": "Short Cover Sign",
    "hold_long": "추세 유지",
    "hold_sell": "하락 지속",
    "neutral": "비추세",
}


# ══════════════════════════════════════════
# 스냅샷 파일 I/O (data-collector 담당 영역)
# ══════════════════════════════════════════

SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_snapshot.json")


def save_snapshot(signals: dict, market: str = "global") -> None:
    """신호 판정 결과를 스냅샷 파일에 저장.

    Args:
        signals: {ticker: tag} 매핑. tag는 classify_signal의 결과값.
        market: "global" 또는 "korea". 각각 별도 키로 저장되어 덮어쓰기 방지.
    """
    data = {}
    if os.path.exists(SNAPSHOT_PATH):
        try:
            with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[market] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "signals": signals,
    }
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_snapshot() -> dict:
    """스냅샷 전체 로드. 구조: {market: {timestamp, signals: {ticker: tag}}}"""
    if not os.path.exists(SNAPSHOT_PATH):
        return {}
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_baseline_signals() -> dict:
    """모든 market의 signals를 합친 {ticker: tag} 맵 반환."""
    snap = load_snapshot()
    merged = {}
    for market, info in snap.items():
        merged.update(info.get("signals", {}))
    return merged


def detect_changes(current: dict, baseline: dict) -> dict:
    """현재 판정 결과와 스냅샷 비교 → 변화 종목 dict 반환.

    Returns:
        {ticker: (before_tag, after_tag)} — 변화 있는 종목만.
        neutral ↔ hold 같은 요동은 의미 있는 변화로 간주 (필요 시 필터 추가).
    """
    changes = {}
    for t, cur in current.items():
        cur_tag = cur["tag"] if isinstance(cur, dict) else cur
        base_tag = baseline.get(t)
        if base_tag and cur_tag != base_tag:
            changes[t] = (base_tag, cur_tag)
    return changes
