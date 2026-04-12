"""
시장 위험 진단 대시보드
매크로 동향(금리/환율/원자재/VIX) + 지수 동향 기반 일일 시장 위험 진단
실행: streamlit run market_dashboard.py
"""

import streamlit as st
import yfinance as yf
import requests as req
import re
from datetime import datetime, timedelta
import time

st.set_page_config(page_title="시장 위험 진단", page_icon="📊", layout="wide")

# 장중에만 30초 자동 갱신 (휴장일·장외시간 제외)
def is_market_open():
    now = datetime.now()
    # 주말 제외 (월=0 ~ 금=4)
    if now.weekday() >= 5:
        return False
    # 한국장 09:00~15:30 또는 미국장 22:30~익일 05:00 (KST)
    t = now.time()
    from datetime import time as dtime
    korea_open = dtime(9, 0) <= t <= dtime(15, 30)
    us_open = t >= dtime(22, 30) or t <= dtime(5, 0)
    return korea_open or us_open

MARKET_OPEN = is_market_open()
if MARKET_OPEN:
    st.markdown('<meta http-equiv="refresh" content="600">', unsafe_allow_html=True)


# ══════════════════════════════════════════
# 데이터 수집 함수
# ══════════════════════════════════════════

def get_price_and_change(ticker_symbol):
    try:
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period="5d")
        if len(hist) < 2:
            return "", "", None
        prev_close = hist["Close"].iloc[-2]
        last_close = hist["Close"].iloc[-1]
        pct = (last_close - prev_close) / prev_close * 100
        if last_close >= 1000:
            price_str = f"{last_close:,.0f}"
        elif last_close >= 100:
            price_str = f"{last_close:.2f}"
        else:
            price_str = f"{last_close:.3f}"
        return price_str, f"{pct:+.2f}%", last_close
    except Exception:
        return "", "", None


def get_change_pct(ticker_symbol):
    try:
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period="5d")
        if len(hist) < 2:
            return "", None
        prev_close = hist["Close"].iloc[-2]
        last_close = hist["Close"].iloc[-1]
        pct = (last_close - prev_close) / prev_close * 100
        return f"{pct:+.2f}%", pct
    except Exception:
        return "", None


def get_copper_investing():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        resp = req.get("https://kr.investing.com/commodities/copper?cid=959211", headers=headers, timeout=15)
        text = resp.text
        price_match = re.search(r'data-test="instrument-price-last"[^>]*>([^<]+)', text)
        change_match = re.search(r'data-test="instrument-price-change-percent"[^>]*>([^<]+)', text)
        price_str = price_match.group(1).strip() if price_match else ""
        change_str = change_match.group(1).strip() if change_match else ""
        price_val = float(price_str.replace(",", "")) if price_str else None
        return price_str, change_str, price_val
    except Exception:
        return "", "", None


# ══════════════════════════════════════════
# 위험 진단 함수
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
        if value <= t1:
            return "안정", 0
        elif value <= t2:
            return "주의", 10
        elif value <= t3:
            return "위험", 20
        else:
            return "고위험", 30
    return "N/A", 0


def compute_t_risk(bond_2y, bond_10y, bond_30y):
    score_2y = assess_risk("2Y", bond_2y)[1]
    score_10y = assess_risk("10Y", bond_10y)[1]
    score_30y = assess_risk("30Y", bond_30y)[1]

    spread_score = 0
    if bond_10y is not None and bond_2y is not None:
        spread = bond_10y - bond_2y
        try:
            import pandas as pd
            tk_10y = yf.Ticker("^TNX")
            tk_2y = yf.Ticker("2YY=F")
            h10 = tk_10y.history(period="6mo")["Close"]
            h2 = tk_2y.history(period="6mo")["Close"]
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

    total = score_2y * 0.4 + score_10y * 0.3 + score_30y * 0.1 + spread_score * 0.2
    normalized = total * (100 / 30)
    return total, normalized, spread


def compute_fx_risk(dxy, jpy, cny):
    def fx_score(value, t1, t2, t3):
        if value is None:
            return 0
        if value < t1:
            return 0
        elif value <= t2:
            return 10
        elif value <= t3:
            return 20
        else:
            return 30

    d = fx_score(dxy, 103, 105, 108)
    j = fx_score(jpy, 145, 152, 158)
    c = fx_score(cny, 7.15, 7.25, 7.35)
    total = d * 1.67 + j * 1.0 + c * 0.67
    return min(total, 100)


def compute_c_risk(wti, brent, gold, copper):
    oil_score = 0
    oil_avg = None
    if wti is not None and brent is not None:
        oil_avg = (wti + brent) / 2
        if oil_avg < 85:
            oil_score = 0
        elif oil_avg <= 95:
            oil_score = 10
        elif oil_avg <= 105:
            oil_score = 20
        else:
            oil_score = 30

    gc_score = 0
    gc_ratio = None
    if gold is not None and copper is not None and copper > 0:
        gc_ratio = gold / copper
        if gc_ratio < 0.35:
            gc_score = 0
        elif gc_ratio <= 0.45:
            gc_score = 10
        elif gc_ratio <= 0.55:
            gc_score = 20
        else:
            gc_score = 30

    total = oil_score * 2 + gc_score * 1.33
    return min(total, 100), oil_avg, gc_ratio


def compute_vix_score(vix_val):
    if vix_val is None:
        return 0
    if vix_val <= 20:
        return 0
    elif vix_val <= 25:
        return 33
    elif vix_val <= 30:
        return 67
    else:
        return 100


def risk_grade(score, thresholds=(25, 50, 75)):
    t1, t2, t3 = thresholds
    if score <= t1:
        return "안정"
    elif score <= t2:
        return "주의"
    elif score <= t3:
        return "위험"
    else:
        return "고위험"


def grade_color(grade):
    return {
        "안정": "#2E7D32",
        "주의": "#F9A825",
        "위험": "#EF6C00",
        "고위험": "#C62828",
        "N/A": "#757575",
    }.get(grade, "#757575")


def grade_emoji(grade):
    return {
        "안정": "🟢",
        "주의": "🟡",
        "위험": "🟠",
        "고위험": "🔴",
        "N/A": "⚪",
    }.get(grade, "⚪")


# ══════════════════════════════════════════
# 데이터 수집 (캐시)
# ══════════════════════════════════════════

@st.cache_data(ttl=600)
def collect_all_data():
    data = {}

    # 금리
    _, _, data["2y"] = get_price_and_change("2YY=F")
    _, _, data["10y"] = get_price_and_change("^TNX")
    _, _, data["30y"] = get_price_and_change("^TYX")

    # 환율
    _, _, data["dxy"] = get_price_and_change("DX-Y.NYB")
    _, _, data["usd_jpy"] = get_price_and_change("JPY=X")
    _, _, data["usd_cny"] = get_price_and_change("CNY=X")

    # 원자재
    _, _, data["wti"] = get_price_and_change("CL=F")
    _, _, data["brent"] = get_price_and_change("BZ=F")
    _, data["copper_chg"], data["copper"] = get_copper_investing()
    _, data["gold_chg_str"], data["gold"] = get_price_and_change("GC=F")
    _, data["btc_chg_str"], data["btc"] = get_price_and_change("BTC-USD")

    # VIX
    _, _, data["vix"] = get_price_and_change("^VIX")

    # 지수
    data["dow_chg_str"], data["dow_chg"] = get_change_pct("^DJI")
    data["nasdaq_chg_str"], data["nasdaq_chg"] = get_change_pct("^IXIC")
    data["sp500_chg_str"], data["sp500_chg"] = get_change_pct("^GSPC")
    data["russell_chg_str"], data["russell_chg"] = get_change_pct("^RUT")
    data["nq_chg_str"], data["nq_chg"] = get_change_pct("NQ=F")       # E-mini 나스닥 선물
    data["kospi_night_chg_str"], data["kospi_night_chg"] = get_change_pct("KM=F")  # CME KOSPI 야간선물

    # 종합 점수 계산
    t_raw, data["t_risk"], data["spread"] = compute_t_risk(data["2y"], data["10y"], data["30y"])
    data["fx_risk"] = compute_fx_risk(data["dxy"], data["usd_jpy"], data["usd_cny"])
    data["c_risk"], data["oil_avg"], data["gc_ratio"] = compute_c_risk(
        data["wti"], data["brent"], data["gold"], data["copper"]
    )
    data["vix_score"] = compute_vix_score(data["vix"])

    data["macro_total"] = (
        data["t_risk"] * 0.30
        + data["fx_risk"] * 0.25
        + data["c_risk"] * 0.25
        + data["vix_score"] * 0.20
    )
    data["macro_total"] = min(data["macro_total"], 100)

    return data


# ══════════════════════════════════════════
# 시장 진단 텍스트 생성
# ══════════════════════════════════════════

def generate_diagnosis(d):
    total = d["macro_total"]
    grade = risk_grade(total)

    lines = []

    # 종합 판단
    grade_text = {
        "안정": "매크로 환경 **안정** 단계 — 추세 우호적, 기존 Long 포지션 유지·확대 유효",
        "주의": "매크로 환경 **주의** 단계 — 일부 리스크 요인 감지, 개별 종목 추세 점검 강화 필요",
        "위험": "매크로 환경 **위험** 단계 — 다수 리스크 경고 수준, Sell sign 발생 종목 분할 축소 원칙 적용",
        "고위험": "매크로 환경 **고위험** 단계 — 복수 리스크 위기 수준, 추세 훼손 종목 기계적 축소·현금 비중 확대 필요",
    }
    lines.append(grade_text.get(grade, ""))

    # 가장 높은/낮은 위험 요인
    factors = {
        "금리": d["t_risk"],
        "환율": d["fx_risk"],
        "원자재": d["c_risk"],
        "VIX": d["vix_score"],
    }
    top = max(factors, key=factors.get)
    bottom = min(factors, key=factors.get)
    lines.append(f"최고 위험 영역: **{top}** ({factors[top]:.0f}점) / 상대적 안정: **{bottom}** ({factors[bottom]:.0f}점)")

    # 금리 세부
    if d["spread"] is not None:
        if d["spread"] < 0:
            lines.append(f"⚠️ 장단기 금리 역전 (10Y-2Y = {d['spread']:+.2f}%p) — 경기 침체 선행 지표")
        elif d["spread"] < 0.3:
            lines.append(f"장단기 금리차 축소 중 ({d['spread']:+.2f}%p) — 역전 가능성 경계")

    # 환율 세부
    if d["dxy"] is not None:
        if d["dxy"] > 105:
            lines.append("달러 강세 지속 — 신흥국·수출주 부정적")
        elif d["dxy"] < 100:
            lines.append("달러 약세 — 위험자산 선호 환경")

    # 원자재 세부
    if d["oil_avg"] is not None and d["oil_avg"] > 95:
        lines.append(f"유가 고가 구간 (WTI/BRN 평균 ${d['oil_avg']:.1f}) — 인플레이션 재점화 리스크")
    if d["gc_ratio"] is not None and d["gc_ratio"] > 0.45:
        lines.append(f"금/구리 비율 상승 ({d['gc_ratio']:.3f}) — 경기 둔화 반영")

    # VIX 세부
    if d["vix"] is not None:
        if d["vix"] > 30:
            lines.append(f"VIX **{d['vix']:.1f}** 공포 구간 — 급격한 변동성 대비")
        elif d["vix"] < 15:
            lines.append(f"VIX **{d['vix']:.1f}** 매우 낮음 — 과도한 안도감 경계")

    # 지수-매크로 괴리
    idx_avg = 0
    cnt = 0
    for k in ["dow_chg", "nasdaq_chg", "sp500_chg", "russell_chg"]:
        if d[k] is not None:
            idx_avg += d[k]
            cnt += 1
    if cnt > 0:
        idx_avg /= cnt
        if total > 50 and idx_avg > 1:
            lines.append("⚠️ **매크로-지수 괴리** — 매크로 위험 높으나 지수 상승 중, 추세 이탈 신호 주시")
        elif total < 25 and idx_avg < -1:
            lines.append("💡 **매크로-지수 괴리** — 매크로 양호하나 지수 하락 중, Long sign 재확립 대기")

    return "\n\n".join(lines)


def generate_strategy(score):
    grade = risk_grade(score)
    strategies = {
        "안정": [
            "지수·개별 종목 추세 유효(200일선·50일선 위) 여부 점검 — Long 상태면 포지션 유지",
            "Long sign 발생 종목에 신규 편입 시 목표 비중을 **40% → 30% → 30%** 3단계 분할 매수",
            "상대강도(RS) 상위 주도주 보유 지속, Sell sign 없으면 익절 목표가 설정 금지",
            "추세가 살아있는 한 홀딩 원칙 — 꼭대기 예측 매도 금지",
        ],
        "주의": [
            "보유 종목별 개별 추세 점검 — Sell sign(50일선 이탈, 추세선 붕괴 등) 발생 여부 확인",
            "Sell sign 발생 종목은 **40% → 30% → 30%** 분할 매도로 단계적 축소",
            "신규 매수는 Long sign 유효한 주도주에 한정, 진입도 40/30/30 분할",
            "지수 약세라도 개별 종목 추세 유효하면 섣불리 매도하지 않음",
        ],
        "위험": [
            "Sell sign 발생 종목은 기계적으로 분할 매도(**40/30/30**) 진행 — 지지선 기대 금지",
            "신규 Long sign 발생이 드문 구간, 신규 매수는 극도로 선별적으로만",
            "아직 Long 상태 유지 종목은 추세 지속되는 한 홀딩, 트레일링 스탑으로 관리",
            "하락 중 저가매수(물타기)·바닥 예측 매수 금지",
        ],
        "고위험": [
            "Sell sign 발생 종목 **40/30/30** 분할 매도 원칙대로 축소, 현금 비중 확대",
            "아직 추세 유효한 소수 종목만 유지, 신규 매수 전면 중단",
            "반등에 short cover 매수 금지 — 새로운 Long sign 재확립까지 관망",
            "하락 추세에서 바닥 예측 매수 절대 금지, 추세 반전 확인 후 40/30/30 분할 진입",
        ],
    }
    return strategies.get(grade, [])


# ══════════════════════════════════════════
# 글로벌 CSS
# ══════════════════════════════════════════

DARK_BG = "#0e1117"
CARD_BG = "#161b22"
CARD_BORDER = "#30363d"
TEXT_PRIMARY = "#e6edf3"
TEXT_SECONDARY = "#8b949e"
ACCENT_GOLD = "#d4a843"

COLOR_SAFE    = "#2ea043"
COLOR_CAUTION = "#d29922"
COLOR_DANGER  = "#e3832a"
COLOR_CRISIS  = "#f85149"

def grade_css_color(g):
    return {"안정": COLOR_SAFE, "주의": COLOR_CAUTION, "위험": COLOR_DANGER, "고위험": COLOR_CRISIS}.get(g, TEXT_SECONDARY)

st.markdown(f"""
<style>
    /* 전체 배경 */
    .stApp {{ background-color: {DARK_BG}; }}

    /* 기본 텍스트 */
    .stApp, .stApp p, .stApp span, .stApp li {{ color: {TEXT_PRIMARY}; }}
    .stApp h1, .stApp h2, .stApp h3 {{ color: {TEXT_PRIMARY}; font-family: 'Segoe UI', sans-serif; }}

    /* 헤더 바 */
    .header-bar {{
        background: linear-gradient(90deg, {CARD_BG} 0%, #1c2333 100%);
        border-bottom: 1px solid {ACCENT_GOLD};
        padding: 16px 32px;
        margin: -1rem -1rem 1.5rem -1rem;
        display: flex; justify-content: space-between; align-items: center;
    }}
    .header-bar h1 {{
        margin: 0; font-size: 22px; color: {ACCENT_GOLD};
        font-weight: 600; letter-spacing: 1px;
    }}
    .header-bar .ts {{
        font-size: 12px; color: {TEXT_SECONDARY};
        font-family: 'Consolas', monospace;
    }}

    /* 종합 게이지 */
    .gauge-container {{
        text-align: center; padding: 32px 24px;
        background: {CARD_BG};
        border: 1px solid {CARD_BORDER};
        border-radius: 12px; position: relative; overflow: hidden;
    }}
    .gauge-container::before {{
        content: ''; position: absolute; top: 0; left: 0; right: 0;
        height: 3px;
    }}
    .gauge-label {{ font-size: 13px; color: {TEXT_SECONDARY}; letter-spacing: 2px; text-transform: uppercase; margin: 0; }}
    .gauge-score {{ font-size: 80px; font-weight: 700; margin: 8px 0 4px 0; line-height: 1; font-family: 'Segoe UI', sans-serif; }}
    .gauge-score .unit {{ font-size: 20px; font-weight: 400; }}
    .gauge-grade {{ font-size: 20px; font-weight: 600; letter-spacing: 3px; margin: 0; }}
    .gauge-bar {{ height: 6px; border-radius: 3px; background: #21262d; margin-top: 16px; overflow: hidden; }}
    .gauge-bar-fill {{ height: 100%; border-radius: 3px; transition: width 1s ease; }}

    /* 서브 카드 */
    .sub-card {{
        background: {CARD_BG}; border: 1px solid {CARD_BORDER};
        border-radius: 10px; padding: 20px 16px; text-align: center;
        position: relative; overflow: hidden;
    }}
    .sub-card::before {{
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    }}
    .sub-card .sc-label {{ font-size: 11px; color: {TEXT_SECONDARY}; letter-spacing: 1.5px; text-transform: uppercase; margin: 0 0 8px 0; }}
    .sub-card .sc-score {{ font-size: 36px; font-weight: 700; margin: 0; line-height: 1.1; }}
    .sub-card .sc-grade {{ font-size: 13px; font-weight: 500; margin-top: 6px; letter-spacing: 1px; }}
    .sub-card .sc-bar {{ height: 3px; border-radius: 2px; background: #21262d; margin-top: 12px; overflow: hidden; }}
    .sub-card .sc-bar-fill {{ height: 100%; border-radius: 2px; }}

    /* 상세 테이블 */
    .detail-table {{ width: 100%; border-collapse: collapse; font-size: 14px; font-family: 'Segoe UI', sans-serif; }}
    .detail-table th {{
        text-align: left; padding: 10px 16px; color: {TEXT_SECONDARY};
        border-bottom: 1px solid {CARD_BORDER}; font-weight: 500;
        font-size: 11px; letter-spacing: 1px; text-transform: uppercase;
    }}
    .detail-table td {{
        padding: 10px 16px; border-bottom: 1px solid #21262d;
        color: {TEXT_PRIMARY};
    }}
    .detail-table tr:hover td {{ background: #1c2333; }}
    .detail-table .val {{ font-weight: 600; font-family: 'Consolas', monospace; font-size: 14px; }}
    .badge {{
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        font-size: 11px; font-weight: 600; letter-spacing: 0.5px;
    }}
    .badge-safe    {{ background: {COLOR_SAFE}22; color: {COLOR_SAFE}; border: 1px solid {COLOR_SAFE}44; }}
    .badge-caution {{ background: {COLOR_CAUTION}22; color: {COLOR_CAUTION}; border: 1px solid {COLOR_CAUTION}44; }}
    .badge-danger  {{ background: {COLOR_DANGER}22; color: {COLOR_DANGER}; border: 1px solid {COLOR_DANGER}44; }}
    .badge-crisis  {{ background: {COLOR_CRISIS}22; color: {COLOR_CRISIS}44; border: 1px solid {COLOR_CRISIS}44; }}

    /* 지수 카드 */
    .idx-card {{
        background: {CARD_BG}; border: 1px solid {CARD_BORDER};
        border-radius: 10px; padding: 18px 12px; text-align: center;
        position: relative; overflow: hidden;
    }}
    .idx-card::before {{
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    }}
    .idx-card .idx-name {{ font-size: 11px; color: {TEXT_SECONDARY}; letter-spacing: 1px; margin: 0; }}
    .idx-card .idx-val {{ font-size: 28px; font-weight: 700; margin: 6px 0 0 0; font-family: 'Consolas', monospace; }}

    /* 진단 박스 */
    .diagnosis-box {{
        background: {CARD_BG}; border: 1px solid {CARD_BORDER};
        border-radius: 8px; padding: 20px 24px; line-height: 1.8;
        font-size: 14px; color: {TEXT_PRIMARY};
    }}
    .diagnosis-box strong {{ color: {ACCENT_GOLD}; }}

    /* 전략 박스 */
    .strategy-box {{
        background: {CARD_BG}; border: 1px solid {CARD_BORDER};
        border-radius: 8px; padding: 16px 24px;
    }}
    .strategy-box li {{
        padding: 6px 0; font-size: 14px; color: {TEXT_PRIMARY};
        border-bottom: 1px solid #21262d; list-style: none;
    }}
    .strategy-box li:last-child {{ border-bottom: none; }}
    .strategy-box li::before {{ content: '→ '; color: {ACCENT_GOLD}; font-weight: 600; }}

    /* expander 스타일 */
    .streamlit-expanderHeader {{
        background: {CARD_BG} !important;
        border: 1px solid {CARD_BORDER} !important;
        border-radius: 8px !important;
        color: {TEXT_PRIMARY} !important;
        font-size: 14px !important;
    }}
    .streamlit-expanderContent {{
        background: {CARD_BG} !important;
        border: 1px solid {CARD_BORDER} !important;
        border-top: none !important;
    }}

    /* 하단 면책 */
    .footer {{
        text-align: center; padding: 16px;
        color: {TEXT_SECONDARY}; font-size: 11px;
        border-top: 1px solid {CARD_BORDER}; margin-top: 32px;
    }}

    /* 새로고침 버튼 */
    .stButton > button {{
        background: {CARD_BG} !important; color: {ACCENT_GOLD} !important;
        border: 1px solid {ACCENT_GOLD} !important; border-radius: 6px !important;
        font-size: 13px !important; padding: 8px 24px !important;
        letter-spacing: 1px;
    }}
    .stButton > button:hover {{
        background: {ACCENT_GOLD}22 !important;
    }}

    /* spinner */
    .stSpinner > div {{ color: {ACCENT_GOLD} !important; }}

    /* section divider */
    .section-title {{
        font-size: 13px; color: {TEXT_SECONDARY}; letter-spacing: 2px;
        text-transform: uppercase; font-weight: 600;
        border-bottom: 1px solid {CARD_BORDER}; padding-bottom: 8px;
        margin: 24px 0 16px 0;
    }}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════
# 헬퍼: HTML 뱃지
# ══════════════════════════════════════════
def badge_html(g):
    cls = {"안정": "safe", "주의": "caution", "위험": "danger", "고위험": "crisis"}.get(g, "safe")
    return f'<span class="badge badge-{cls}">{g}</span>'

def detail_table(rows):
    """rows: list of (label, value_str, status_html)"""
    html = '<table class="detail-table"><tr><th>체크포인트</th><th>수치</th><th>상태</th></tr>'
    for label, val, status in rows:
        html += f'<tr><td>{label}</td><td class="val">{val}</td><td>{status}</td></tr>'
    html += '</table>'
    return html


# ══════════════════════════════════════════
# UI 렌더링
# ══════════════════════════════════════════

# 헤더
st.markdown(
    f"""<div class="header-bar">
        <h1>주식 스카우터</h1>
        <span class="ts">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST &nbsp;|&nbsp; {"🟢 10분 자동 갱신" if MARKET_OPEN else "⏸ 휴장 중 (수동 새로고침)"} &nbsp;|&nbsp; Yahoo Finance &middot; Investing.com</span>
    </div>""",
    unsafe_allow_html=True,
)

with st.spinner("매크로 데이터 수집 중..."):
    d = collect_all_data()

total = d["macro_total"]
grade = risk_grade(total)
gc = grade_css_color(grade)

# ── 종합 위험도 게이지 ──
c1, c2, c3 = st.columns([1, 2, 1])
with c2:
    st.markdown(
        f"""
        <div class="gauge-container" style="border-color:{gc}40;">
            <div style="position:absolute;top:0;left:0;right:0;height:3px;background:{gc};"></div>
            <p class="gauge-label">매크로 위험 지표</p>
            <p class="gauge-score" style="color:{gc};">{total:.0f}<span class="unit"> / 100</span></p>
            <p class="gauge-grade" style="color:{gc};">{grade}</p>
            <div class="gauge-bar">
                <div class="gauge-bar-fill" style="width:{min(total,100):.0f}%;background:linear-gradient(90deg,{COLOR_SAFE},{COLOR_CAUTION},{COLOR_DANGER},{COLOR_CRISIS});"></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("")

# ── 하위 지수 4개 ──
st.markdown('<p class="section-title">매크로 분석</p>', unsafe_allow_html=True)

sub_indices = [
    ("T-RISK", "금리", d["t_risk"], 100, (25, 50, 75)),
    ("FX-RISK", "환율", d["fx_risk"], 100, (30, 60, 85)),
    ("C-RISK", "원자재", d["c_risk"], 100, (30, 60, 85)),
    ("VIX", "위험심리", d["vix_score"], 100, (25, 50, 75)),
]

cols = st.columns(4)
for col, (code, label, score, max_score, th) in zip(cols, sub_indices):
    g = risk_grade(score, th)
    sc = grade_css_color(g)
    pct = min(score / max_score * 100, 100)
    with col:
        st.markdown(
            f"""
            <div class="sub-card">
                <div style="position:absolute;top:0;left:0;right:0;height:2px;background:{sc};"></div>
                <p class="sc-label">{code}</p>
                <p class="sc-score" style="color:{sc};">{score:.0f}</p>
                <p class="sc-grade" style="color:{sc};">{label} · {g}</p>
                <div class="sc-bar"><div class="sc-bar-fill" style="width:{pct:.0f}%;background:{sc};"></div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("")

# ── 금리 세부 ──
t_g = risk_grade(d["t_risk"], (25, 50, 75))
with st.expander(f"금리 — T-Risk {d['t_risk']:.0f}점 · {t_g}"):
    rows = []
    for label, key, val in [("US 2Y", "2Y", d["2y"]), ("US 10Y", "10Y", d["10y"]), ("US 30Y", "30Y", d["30y"])]:
        if val is not None:
            g, _ = assess_risk(key, val)
            rows.append((label, f"{val:.3f}%", badge_html(g)))
    if d["spread"] is not None:
        sp = d["spread"]
        if sp < 0: sp_b = badge_html("위험")
        elif sp < 0.3: sp_b = badge_html("주의")
        else: sp_b = badge_html("안정")
        rows.append(("Yield Spread (10Y-2Y)", f"{sp:+.2f}%p", sp_b))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 환율 세부 ──
fx_g = risk_grade(d["fx_risk"], (30, 60, 85))
with st.expander(f"환율 — FX-Risk {d['fx_risk']:.0f}점 · {fx_g}"):
    rows = []
    for label, key, val, fmt in [
        ("DXY", "DXY", d["dxy"], "{:.2f}"),
        ("USD/JPY", "USD/JPY", d["usd_jpy"], "{:.2f}"),
        ("USD/CNY", "USD/CNY", d["usd_cny"], "{:.4f}"),
    ]:
        if val is not None:
            v = fmt.format(val)
            b = badge_html(assess_risk(key, val)[0]) if key else "—"
            rows.append((label, v, b))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 원자재 세부 ──
c_g = risk_grade(d["c_risk"], (30, 60, 85))
with st.expander(f"원자재 — C-Risk {d['c_risk']:.0f}점 · {c_g}"):
    rows = []
    for label, key, val, fmt in [
        ("Brent Crude", "BRN", d["brent"], "${:.2f}"),
        ("WTI Crude", "WTI", d["wti"], "${:.2f}"),
        ("Copper (LME)", None, d["copper"], "${:,.0f}/톤"),
    ]:
        if val is not None:
            v = fmt.format(val)
            b = badge_html(assess_risk(key, val)[0]) if key else "—"
            rows.append((label, v, b))
    if d["oil_avg"] is not None:
        rows.append(("Oil Average", f"${d['oil_avg']:.1f}", "—"))
    if d["gc_ratio"] is not None:
        gcr = d["gc_ratio"]
        if gcr < 0.35: gcg = "안정"
        elif gcr <= 0.45: gcg = "주의"
        elif gcr <= 0.55: gcg = "위험"
        else: gcg = "고위험"
        rows.append(("Gold/Copper Ratio", f"{gcr:.3f}", badge_html(gcg)))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 위험 심리 세부 ──
v_g = risk_grade(d["vix_score"], (25, 50, 75))
with st.expander(f"위험 심리 — VIX Score {d['vix_score']:.0f}점 · {v_g}"):
    rows = []
    if d["vix"] is not None:
        g, _ = assess_risk("VIX", d["vix"])
        rows.append(("CBOE VIX", f"{d['vix']:.2f}", badge_html(g)))
    if d["gold"] is not None:
        rows.append(("Gold", f"${d['gold']:,.0f}", d.get("gold_chg_str", "")))
    if d["btc"] is not None:
        rows.append(("Bitcoin", f"${d['btc']:,.0f}", d.get("btc_chg_str", "")))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 지수 동향 ──
st.markdown('<p class="section-title">지수 현황</p>', unsafe_allow_html=True)

indices = [
    ("DOW JONES", d["dow_chg_str"], d["dow_chg"]),
    ("NASDAQ", d["nasdaq_chg_str"], d["nasdaq_chg"]),
    ("S&P 500", d["sp500_chg_str"], d["sp500_chg"]),
    ("RUSSELL 2000", d["russell_chg_str"], d["russell_chg"]),
    ("MINI NASDAQ (NQ=F)", d["nq_chg_str"], d["nq_chg"]),
    ("KOSPI 야간선물", d["kospi_night_chg_str"], d["kospi_night_chg"]),
]
indices = [x for x in indices if x[2] is not None]
idx_cols = st.columns(len(indices)) if indices else []
for col, (name, chg_str, chg_val) in zip(idx_cols, indices):
    with col:
        if chg_val is not None:
            if chg_val >= 2: ic = "#4da6ff"
            elif chg_val <= -2: ic = COLOR_CRISIS
            elif chg_val >= 0: ic = COLOR_SAFE
            else: ic = COLOR_CRISIS
            arrow = "▲" if chg_val >= 0 else "▼"
            st.markdown(
                f"""<div class="idx-card" style="border-color:{ic}40;">
                    <div style="position:absolute;top:0;left:0;right:0;height:2px;background:{ic};"></div>
                    <p class="idx-name">{name}</p>
                    <p class="idx-val" style="color:{ic};">{arrow} {chg_str}</p>
                </div>""",
                unsafe_allow_html=True,
            )

# ── 시장 진단 ──
st.markdown('<p class="section-title">시장 진단</p>', unsafe_allow_html=True)
diagnosis = generate_diagnosis(d)
import re as re2
items = [ln.strip() for ln in diagnosis.split("\n\n") if ln.strip()]
items_html = "".join(
    f"<li>{re2.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', it)}</li>"
    for it in items
)
st.markdown(
    f'<div class="diagnosis-box"><ul style="padding-left:1.2em;margin:0;">{items_html}</ul></div>',
    unsafe_allow_html=True,
)

# ── 대응 전략 ──
st.markdown(f'<p class="section-title">대응 방안 — {grade}</p>', unsafe_allow_html=True)
strategies = generate_strategy(total)
strat_items = "".join([f"<li>{s}</li>" for s in strategies])
st.markdown(f'<div class="strategy-box"><ul style="padding:0;margin:0;">{strat_items}</ul></div>', unsafe_allow_html=True)

# ── 개별주식 1 - 기존 추적 종목 추세 신호 ──
st.markdown('<p class="section-title">개별 주식 1 — 추세 신호 (추적 종목)</p>', unsafe_allow_html=True)

STOCK_UNIVERSE = {
    "🇺🇸 반도체": ["NVDA", "AMD", "AVGO", "MU", "TXN", "ASML", "LRCX", "AMAT", "INTC", "MRVL"],
    "🇺🇸 빅테크/SW": ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NFLX", "ORCL", "CRM", "ADBE", "NOW", "PLTR", "CRWD", "DDOG", "SNOW"],
    "🇺🇸 전력/원전": ["ETN", "VRT", "GEV", "SMR", "CEG", "TLN", "BWXT"],
    "🇺🇸 금융/산업": ["JPM", "BAC", "GS", "CAT", "DE", "XOM", "COP", "TSLA"],
    "🇰🇷 반도체": ["005930.KS", "000660.KS", "042700.KS"],
    "🇰🇷 2차전지": ["373220.KS", "006400.KS"],
    "🇰🇷 자동차": ["005380.KS"],
    "🇰🇷 소재": ["005490.KS", "051910.KS"],
    "🇰🇷 IT/소프트웨어": ["035420.KS", "035720.KS"],
    "🇰🇷 지주사": ["028260.KS"],
    "🇰🇷 금융": ["105560.KS", "055550.KS"],
}
TICKER_NAMES = {
    "005930.KS":"삼성전자", "000660.KS":"SK하이닉스", "042700.KS":"한미반도체",
    "373220.KS":"LG에너지솔루션", "006400.KS":"삼성SDI", "005380.KS":"현대차",
    "035420.KS":"NAVER", "035720.KS":"카카오", "105560.KS":"KB금융",
    "055550.KS":"신한지주", "005490.KS":"POSCO홀딩스", "051910.KS":"LG화학",
    "028260.KS":"삼성물산",
}

@st.cache_data(ttl=3600)
def analyze_trend_signals(all_tickers):
    """종목별 추세 신호 분석 (daily bar 기준, 1시간 캐시)"""
    try:
        data = yf.download(all_tickers, period="1y", auto_adjust=True, progress=False, group_by="ticker")
    except Exception:
        return {}
    results = {}
    for t in all_tickers:
        try:
            if len(all_tickers) == 1:
                close = data["Close"].dropna()
            else:
                close = data[t]["Close"].dropna()
            if len(close) < 200:
                continue
            ma50 = close.rolling(50).mean()
            ma200 = close.rolling(200).mean()
            last = close.iloc[-1]
            prev = close.iloc[-2]
            ma50_last, ma50_prev = ma50.iloc[-1], ma50.iloc[-2]
            ma200_last, ma200_prev = ma200.iloc[-1], ma200.iloc[-2]
            # === 이벤트 감지 (각 이벤트별 lookback 차등) ===
            # 골든/데드크로스: 15일 이내 (드문 이벤트)
            had_gc = had_dc = False
            for i in range(max(1, len(close) - 15), len(close)):
                pd_ma = ma50.iloc[i-1] - ma200.iloc[i-1]
                cd_ma = ma50.iloc[i] - ma200.iloc[i]
                if pd_ma <= 0 and cd_ma > 0: had_gc = True
                if pd_ma >= 0 and cd_ma < 0: had_dc = True

            # 200MA 회복/이탈: 5일 이내 + 회복 전 10일은 주로 200MA 아래에 있었어야 함
            had_ma200_up = had_ma200_dn = False
            for i in range(max(1, len(close) - 5), len(close)):
                pc, cc = close.iloc[i-1], close.iloc[i]
                if pc <= ma200.iloc[i-1] and cc > ma200.iloc[i]:
                    if i >= 10:
                        below_days = (close.iloc[i-10:i] <= ma200.iloc[i-10:i]).sum()
                        if below_days >= 7:
                            had_ma200_up = True
                if pc >= ma200.iloc[i-1] and cc < ma200.iloc[i]:
                    if i >= 10:
                        above_days = (close.iloc[i-10:i] >= ma200.iloc[i-10:i]).sum()
                        if above_days >= 7:
                            had_ma200_dn = True

            # 50MA 이탈 (Sell): 3일 이내 (상승권 조건과 함께 사용)
            had_ma50_dn = False
            for i in range(max(1, len(close) - 3), len(close)):
                if close.iloc[i-1] >= ma50.iloc[i-1] and close.iloc[i] < ma50.iloc[i]:
                    had_ma50_dn = True
            # 50MA 회복 (Cover): 하락권 상태에서 5일 이내
            had_ma50_up = False
            for i in range(max(1, len(close) - 5), len(close)):
                if close.iloc[i-1] <= ma50.iloc[i-1] and close.iloc[i] > ma50.iloc[i]:
                    had_ma50_up = True

            # === Long Sign: 3가지 조건 (52주 신고가 괴리 3% 게이트 + 모멘텀 요구) ===
            high_52w = close.max()
            near_52w_high = last >= high_52w * 0.97  # 3% 이내 (엄격 게이트)

            # 모멘텀 조건 (#2·#3 공통): 5일 +5% AND 10일 +10%
            strong_momentum = False
            if len(close) >= 11:
                close_5d = last / close.iloc[-6] - 1
                close_10d = last / close.iloc[-11] - 1
                strong_momentum = close_5d >= 0.05 and close_10d >= 0.10

            # 조건 1: 52주 신고가 갱신 (단독 인정, 모멘텀·게이트 불필요)
            cond1_break_52w = last >= high_52w * 1.001

            # 조건 2: 정배열 + 20일선 상승 + 52주 근접 + 모멘텀
            cond2_alignment = False
            if len(close) >= 120:
                ma20 = close.rolling(20).mean()
                ma60 = close.rolling(60).mean()
                ma120 = close.rolling(120).mean()
                aligned = ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]
                ma20_slope_up = ma20.iloc[-1] > ma20.iloc[-5] if len(ma20.dropna()) >= 5 else False
                if aligned and ma20_slope_up and near_52w_high and strong_momentum:
                    cond2_alignment = True

            # 조건 3: 박스권 1% 이상 돌파 + 52주 근접 + 모멘텀
            cond3_box_break = False
            if len(close) >= 21:
                box_high = close.iloc[-21:-1].max()
                if last > box_high * 1.01 and near_52w_high and strong_momentum:
                    cond3_box_break = True

            long_sign_new = cond1_break_52w or cond2_alignment or cond3_box_break

            # === Sell Sign: 상승 추세 종료 변곡점 (고점권에서 꺾임) ===
            sell_sign_new = False
            if len(close) >= 30:
                prior = close.iloc[-30:-5]      # 직전 상승 구간 (25일)
                recent = close.iloc[-5:]         # 최근 하락 구간 (5일)
                if len(prior) >= 10 and len(recent) >= 3 and prior.iloc[0] > 0 and recent.iloc[0] > 0:
                    prior_slope = (prior.iloc[-1] - prior.iloc[0]) / prior.iloc[0] / len(prior)
                    recent_slope = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] / len(recent)
                    # 추가 게이트: 직전 구간의 고점이 52주 신고가 5% 이내 (고점권이어야 함)
                    high_52w_local = close.max()
                    at_high_zone = prior.max() >= high_52w_local * 0.95
                    if (prior_slope > 0 and recent_slope < 0
                            and abs(recent_slope) > prior_slope
                            and at_high_zone):
                        sell_sign_new = True

            # === Short Sign: 하락 추세 형성/강화 변곡점 ===
            short_sign_new = False
            # 강화: 52주 신저가 경신
            if last <= close.min() * 1.001:
                short_sign_new = True
            # 형성: 상승 추세가 아닌 상태(직전 기울기 ≤ 0)에서 가파른 하락 변곡점
            if not short_sign_new and len(close) >= 30:
                prior_s = close.iloc[-30:-5]
                recent_s = close.iloc[-5:]
                if len(prior_s) >= 10 and len(recent_s) >= 3 and prior_s.iloc[0] > 0 and recent_s.iloc[0] > 0:
                    ps = (prior_s.iloc[-1] - prior_s.iloc[0]) / prior_s.iloc[0] / len(prior_s)
                    rs = (recent_s.iloc[-1] - recent_s.iloc[0]) / recent_s.iloc[0] / len(recent_s)
                    not_uptrend = ps <= 0   # 직전이 상승 추세가 아님 (횡보 또는 하락)
                    if rs < 0 and abs(rs) > abs(ps) and not_uptrend:
                        short_sign_new = True

            # === Short Cover Sign: 장기 하락 추세의 종료 변곡점 ===
            cover_sign_new = False
            if len(close) >= 40:
                prior_c = close.iloc[-30:-5]
                recent_c = close.iloc[-5:]
                if len(prior_c) >= 10 and len(recent_c) >= 3 and prior_c.iloc[0] > 0 and recent_c.iloc[0] > 0:
                    pc_slope = (prior_c.iloc[-1] - prior_c.iloc[0]) / prior_c.iloc[0] / len(prior_c)
                    rc_slope = (recent_c.iloc[-1] - recent_c.iloc[0]) / recent_c.iloc[0] / len(recent_c)
                    # 장기 하락 추세 게이트
                    high_52w_local_c = close.max()
                    long_term_downtrend = (
                        last < ma200_last and
                        last < high_52w_local_c * 0.85
                    )
                    # 저점 상향 또는 저점에서 반등:
                    #   (a) 최근 15일 저점 > 직전 15일 저점 (strict higher low), 또는
                    #   (b) 최근 15일 저점 대비 +10% 이상 반등 (저점 형성 후 강한 반등)
                    recent_low = close.iloc[-15:].min()
                    prev_low = close.iloc[-40:-15].min()
                    strict_higher_low = recent_low > prev_low
                    strong_bounce_from_low = recent_low > 0 and (last / recent_low - 1) >= 0.10
                    low_pattern_ok = strict_higher_low or strong_bounce_from_low
                    if (pc_slope < 0 and rc_slope > 0
                            and abs(rc_slope) > abs(pc_slope)
                            and long_term_downtrend
                            and low_pattern_ok):
                        cover_sign_new = True

            # === 분류 ===
            if long_sign_new:
                tag = "long"
            elif sell_sign_new:
                tag = "sell"
            elif short_sign_new:
                tag = "short"
            elif cover_sign_new:
                tag = "cover"
            elif last > ma50_last > ma200_last:
                tag = "hold_long"
            elif last < ma50_last < ma200_last:
                tag = "hold_sell"
            else:
                tag = "neutral"
            chg = (last/prev - 1) * 100
            results[t] = {
                "tag": tag, "last": last, "chg": chg,
                "ma50": ma50_last, "ma200": ma200_last,
            }
        except Exception:
            continue
    return results

all_tk = [t for lst in STOCK_UNIVERSE.values() for t in lst]
TICKER_SECTOR = {t: sec for sec, lst in STOCK_UNIVERSE.items() for t in lst}
with st.spinner("개별 종목 추세 분석 중..."):
    sig = analyze_trend_signals(all_tk)

# 장중 변화 감지: 스냅샷(아침/저녁 리뷰 시점)과 비교
from market_common import get_baseline_signals, detect_changes, SIGNAL_LABEL_KR
BASELINE_SIGNALS = get_baseline_signals()
SIGNAL_CHANGES = detect_changes(sig, BASELINE_SIGNALS)  # {ticker: (before, after)}

# 장중 변화 요약 섹션 (개별 주식 1 체크포인트 위에 노출)
if SIGNAL_CHANGES:
    st.markdown('<p class="section-title">🔔 장중 신호 변화</p>', unsafe_allow_html=True)
    with st.expander(f"아침 리뷰 대비 신호 변화 — {len(SIGNAL_CHANGES)}개 종목", expanded=True):
        for t, (before, after) in SIGNAL_CHANGES.items():
            name = TICKER_NAMES.get(t, t)
            before_kr = SIGNAL_LABEL_KR.get(before, before)
            after_kr = SIGNAL_LABEL_KR.get(after, after)
            st.markdown(
                f'<div style="padding:6px 10px;border-bottom:1px solid #2a2a2a;">'
                f'<strong>{name}</strong> <span style="color:#888;font-size:0.85em;">{t}</span>'
                f'&nbsp;&nbsp;<span style="color:#888;">{before_kr}</span>'
                f' → <span style="color:#ffb347;font-weight:600;">{after_kr}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

# 변화 종목 H열 기록 (장중에만, 중복 기록 방지 캐시 활용)
@st.cache_data(ttl=600)
def _record_sheet_events(changes_key: str, changes: dict, market: str):
    """TTL=600초로 동일 changes_key는 10분간 재기록 방지"""
    try:
        from sheet_event_writer import record_intraday_changes
        return record_intraday_changes(changes, market=market)
    except Exception as e:
        return 0

if MARKET_OPEN and SIGNAL_CHANGES:
    # 현재 시장에 따라 기록 (KST 기준 한국장/미국장 판단)
    from datetime import time as _dtime
    now_t = datetime.now().time()
    target_market = "korea" if _dtime(9, 0) <= now_t <= _dtime(15, 30) else "global"
    # 변화 스냅샷을 키로 (같은 변화 조합이면 재호출 안 됨)
    _changes_key = "|".join(sorted(f"{t}:{b}>{a}" for t, (b, a) in SIGNAL_CHANGES.items()))
    _record_sheet_events(_changes_key, SIGNAL_CHANGES, target_market)

def _fmt_row(t, info):
    name = TICKER_NAMES.get(t, t)
    arrow = "▲" if info["chg"] >= 0 else "▼"
    color = COLOR_SAFE if info["chg"] >= 0 else COLOR_CRISIS
    sector = TICKER_SECTOR.get(t)
    sector_html = f'<span style="color:#6ba4ff;font-size:0.8em;background:#1a2540;padding:1px 6px;border-radius:3px;margin-left:6px;">{sector}</span>' if sector else ""
    # 장중 변화 감지 배지
    change_html = ""
    if t in SIGNAL_CHANGES:
        before, after = SIGNAL_CHANGES[t]
        change_html = (
            f'<span style="color:#ffb347;font-size:0.8em;background:#3a2a0a;padding:1px 6px;'
            f'border-radius:3px;margin-left:6px;border:1px solid #d4a843;">🔔 {before}→{after}</span>'
        )
    return (
        f'<div style="display:flex;justify-content:space-between;padding:4px 8px;border-bottom:1px solid #2a2a2a;">'
        f'<span><strong>{name}</strong> <span style="color:#888;font-size:0.85em;">{t}</span>{sector_html}{change_html}</span>'
        f'<span style="color:{color};">{arrow} {info["chg"]:+.2f}% &nbsp; ${info["last"]:,.2f}</span>'
        f'</div>'
    )

long_hits = [(t, info) for t, info in sig.items() if info["tag"] == "long"]
sell_hits = [(t, info) for t, info in sig.items() if info["tag"] == "sell"]
short_hits = [(t, info) for t, info in sig.items() if info["tag"] == "short"]
cover_hits = [(t, info) for t, info in sig.items() if info["tag"] == "cover"]
hold_long = [(t, info) for t, info in sig.items() if info["tag"] == "hold_long"]
hold_sell = [(t, info) for t, info in sig.items() if info["tag"] == "hold_sell"]

SIGN_CRITERIA = {
    "long": """
**📈 Long Sign 판정 기준** (중요도 1 > 2 > 3 순)

**#1 신고가 돌파** (단독 인정, 최강 필터)
- 금일 종가 ≥ 52주 최고가 × 1.001

**#2 정배열 가속** (게이트·모멘텀 충족 시)
- 20일선 > 60일선 > 120일선 (정배열)
- 20일선 기울기 상승 (5거래일 전 대비 현재값 높음)

**#3 직전 고점 돌파** (게이트·모멘텀 충족 시)
- 종가 > 직전 20거래일 박스권 고점 × 1.01 (1% 이상 돌파)

**게이트 & 모멘텀** (#2·#3에 적용):
- **게이트**: 52주 신고가와 괴리 **3% 이내**
- **모멘텀**: 5일 종가 변화 **≥ +5%** AND 10일 종가 변화 **≥ +10%**

**의미**: 강한 상승 신호 — 40/30/30 분할 매수 후보

**제외 예시** (52주 근처지만 모멘텀 약함):
- 한화에어로 (5일 +4% < 5%), GS/엔씨소프트 (10일 < 10%) → hold_long 또는 neutral
""",
    "sell": """
**📉 Sell Sign 판정 기준** (상승 추세 종료 변곡점, 고점권에서 꺾임)

**기준**: 직전 상승 기울기와 최근 하락 기울기 비교 + 고점권 게이트

- **직전 구간** (30일 전 ~ 5일 전): 일평균 상승률 계산
- **최근 구간** (최근 5거래일): 일평균 하락률 계산
- **판정**:
  1. 직전 구간 기울기 > 0 (상승 추세였음)
  2. 최근 구간 기울기 < 0 (하락 중)
  3. 하락 기울기 절대값 > 직전 상승 기울기 (더 가파른 하락)
  4. **직전 구간의 고점이 52주 신고가의 95% 이상** (고점권에서 꺾였어야 함)

**의미**: 상승 추세의 변곡점 — 40/30/30 분할 매도 후보

**Short sign과 차이**: Sell은 "고점에서의 꺾임"(top reversal), Short는 "이미 하락 중 악화"(downtrend acceleration)
""",
    "short": """
**🔻 Short Sign 판정 기준** (하락 추세 형성/강화 변곡점)

**두 가지 모드 중 하나 충족**:

**① 강화 (acceleration)** — 이미 하락 중이 더 깊어짐
- 금일 종가가 **52주 신저가 경신**

**② 형성 (formation)** — 상승 추세가 아닌 상태에서 가파른 하락
- 직전 구간(30~5일 전) 기울기 ≤ 0 (**상승 추세가 아님**, 횡보 또는 하락)
- 최근 5일 하락 기울기 절대값 > 직전 25일 기울기 절대값

**의미**: 주식 투자 최악의 시그널 — 신규 매수 전면 중단

**Sell sign과 차이**: Sell은 "상승 추세 중 고점에서 꺾임", Short는 "상승 추세가 아닌 상태에서 추가 붕괴 또는 신저가"
""",
    "cover": """
**🔺 Short Cover 판정 기준** (장기 하락 추세의 종료 변곡점)

**기본 조건** (변곡점):
- **직전 구간** (30~5일 전): 기울기 < 0 (하락 중)
- **최근 구간** (최근 5거래일): 기울기 > 0 (상승)
- 최근 상승 기울기 절대값 > 직전 하락 기울기 절대값

**장기 하락 게이트** (모두 충족):
- 종가 **< 200일 이동평균** (장기 추세선 아래)
- 52주 신고가 대비 **-15% 이상 하락** (의미 있는 장기 조정)
- **저점 패턴** (둘 중 하나 충족):
  - (a) 최근 15일 저점 > 직전 15일 저점 (Higher Low 성립)
  - (b) 최근 15일 저점 대비 현재가 **+10% 이상 반등** (저점 형성 후 강한 튀어오름)

**의미**: 장기 하락 추세가 반전되어 비추세(횡보)로 전환되는 변곡점

**Long sign과 차이**: Long은 52주 신고가 5% 이내에서의 돌파. Short cover는 52주 신고가와 한참 괴리된 장기 하락권에서의 급등 반등.

**예시**: AMZN 4/8~9 급등, META 4/8 갭상승, NFLX 2/27 갭상승

**제외 예시** (장기 상승 추세이므로 Short cover 아님):
- AVGO, MU, GOOG, JPM, BAC, DE — 200MA 위 + 52w high 근처 → hold_long
""",
    "hold_long": """
**✅ 추세 유지 (Long hold) 판정 기준**

- 금일 종가 > 50일 이동평균 > 200일 이동평균 (**정배열**)
- 단, Long·Sell·Short·Cover 어느 신호도 신규 발생하지 않음

**의미**: 상승 추세 지속 중 — 기존 포지션 보유, 신규 매수 없음
""",
    "hold_sell": """
**⛔ 하락 추세 지속 판정 기준**

- 금일 종가 < 50일 이동평균 < 200일 이동평균 (**역배열**)
- 단, Long·Sell·Short·Cover 어느 신호도 신규 발생하지 않음

**의미**: 하락 추세 지속 — 신규 매수 보류, Long sign 재확립까지 관망
""",
}

def _sign_section(key, title, hits, caption, expanded=False):
    c1, c2 = st.columns([20, 1])
    with c2:
        with st.popover("❓", use_container_width=True):
            st.markdown(SIGN_CRITERIA[key])
    with c1:
        with st.expander(f"{title} ({len(hits)}개)", expanded=expanded):
            if hits:
                st.markdown("".join(_fmt_row(t, i) for t, i in hits), unsafe_allow_html=True)
                st.caption(caption)
            else:
                st.markdown("_해당 종목 없음_")

_sign_section("long", "📈 Long Sign — 진입·분할매수 후보", long_hits,
              "200MA 상향돌파 또는 20일 신고가 + 정배열 — 40/30/30 분할 매수 후보", expanded=False)
_sign_section("sell", "📉 Sell Sign — 분할매도 후보", sell_hits,
              "상승권 50MA 하향이탈 — 40/30/30 분할 매도 후보", expanded=False)
_sign_section("short", "🔻 Short Sign — 대세하락 전환 (참고)", short_hits,
              "200MA 하향이탈 또는 20일 신저가 + 역배열 — 신규 매수 중단 시그널", expanded=False)
_sign_section("cover", "🔺 Short Cover — 하락세 완화 (참고)", cover_hits,
              "하락권 50MA 상향이탈 — 반등 시도, Long sign 재확립 전까지 관망")
_sign_section("hold_long", "✅ 추세 유지 중 (Long hold)", hold_long,
              "정배열 상태 유지 — 신규 신호 없음, 보유 지속")
_sign_section("hold_sell", "⛔ 하락 추세 지속", hold_sell,
              "역배열 지속 — 신규 매수 보류, Long sign 재확립 대기")


# ── 개별주식 2 - 신규 Long Sign 특징주 ──
st.markdown('<p class="section-title">개별 주식 2 — 신규 Long Sign (특징주)</p>', unsafe_allow_html=True)

@st.cache_data(ttl=86400)
def get_market_caps(tickers):
    """티커별 시가총액 조회 (USD, 24h 캐시)"""
    caps = {}
    for t in tickers:
        try:
            mc = yf.Ticker(t).fast_info.get("marketCap")
            if mc:
                caps[t] = mc
        except Exception:
            continue
    return caps

@st.cache_data(ttl=21600)  # 6시간 캐시
def scan_sp500_long_signs(exclude_set):
    """S&P500 중 추적 종목 외에서 Long sign 발생 종목 스캔"""
    try:
        import pandas as pd
        from io import StringIO
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = req.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=headers, timeout=10)
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        sp500_df = tables[0]
        sp500_df["Ticker"] = sp500_df["Symbol"].str.replace(".", "-", regex=False)
        candidates = [t for t in sp500_df["Ticker"].tolist() if t not in exclude_set]
        sector_map = dict(zip(sp500_df["Ticker"], sp500_df["GICS Sector"]))
    except Exception as e:
        return {}, {}, str(e)
    sig_new = analyze_trend_signals(candidates)
    long_only = {t: info for t, info in sig_new.items() if info["tag"] == "long"}
    return long_only, sector_map, None

@st.cache_data(ttl=21600)
def scan_kospi200_long_signs(exclude_set):
    """KOSPI 200 중 추적 종목 외에서 Long sign 발생 종목 스캔 (네이버 금융)"""
    try:
        import pandas as pd
        from io import StringIO
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        tickers, name_map = [], {}
        for page in range(1, 12):  # KOSPI200 약 10페이지
            url = f"https://finance.naver.com/sise/entryJongmok.naver?code=KPI200&page={page}"
            r = req.get(url, headers=headers, timeout=10)
            r.encoding = "euc-kr"
            matches = re.findall(r'code=(\d{6})[^>]*>([^<]+)</a>', r.text)
            if not matches:
                break
            for code, name in matches:
                tickers.append(code)
                name_map[code] = name.strip()
        tickers = list(dict.fromkeys(tickers))  # 중복 제거
    except Exception as e:
        return {}, {}, str(e)
    yf_tickers = [t + ".KS" for t in tickers]
    candidates = [t for t in yf_tickers if t not in exclude_set]
    sig = analyze_trend_signals(candidates)
    long_only = {t: info for t, info in sig.items() if info["tag"] == "long"}
    # code.KS → 한글명 매핑
    name_out = {t: name_map.get(t.replace(".KS", ""), t) for t in long_only}
    return long_only, name_out, None

exclude_set = set(all_tk)

# 미국 S&P 500
with st.spinner("S&P 500 신규 Long Sign 스캔 중 (최초 1회 수십 초 소요)..."):
    us_longs, sector_map, us_err = scan_sp500_long_signs(exclude_set)

# 한국 KOSPI 200
with st.spinner("KOSPI 200 신규 Long Sign 스캔 중..."):
    kr_longs, kr_name_map, kr_err = scan_kospi200_long_signs(exclude_set)

# 한글명 임시 병합 (_fmt_row 에서 조회)
TICKER_NAMES.update(kr_name_map)

# ── 미국 ──
if us_err:
    st.warning(f"S&P 500 스캔 실패: {us_err}")
else:
    mcap_min = 50e9  # $50B+
    mcap_label = "$50B+"

    with st.spinner(f"시가총액 조회 중..."):
        caps = get_market_caps(list(us_longs.keys()))

    us_longs_filtered = {t: info for t, info in us_longs.items()
                         if caps.get(t, 0) >= mcap_min}
    # 시총 정보 주입
    for t, info in us_longs_filtered.items():
        info["mcap"] = caps.get(t, 0)

    by_sector = {}
    for t, info in us_longs_filtered.items():
        sec = sector_map.get(t, "Others")
        by_sector.setdefault(sec, []).append((t, info))

    c1, c2 = st.columns([20, 1])
    with c2:
        with st.popover("❓", use_container_width=True):
            st.markdown(SIGN_CRITERIA["long"])
            st.markdown("---")
            st.markdown(f"**스캔 대상**: S&P 500 (추적 종목 제외, 시총 {mcap_label})\n\n**갱신 주기**: 6시간")
    with c1:
        with st.expander(f"🇺🇸 S&P 500 신규 Long Sign ({len(us_longs_filtered)}개 / 전체 {len(us_longs)}개)", expanded=False):
            if us_longs_filtered:
                for sec in sorted(by_sector.keys()):
                    hits = sorted(by_sector[sec], key=lambda x: -x[1].get("mcap", 0))
                    st.markdown(f"**{sec}** ({len(hits)}개)")
                    for t, i in hits:
                        mc = i.get("mcap", 0) / 1e9
                        mc_str = f"${mc:,.0f}B" if mc >= 1 else ""
                        name = TICKER_NAMES.get(t, t)
                        arrow = "▲" if i["chg"] >= 0 else "▼"
                        color = COLOR_SAFE if i["chg"] >= 0 else COLOR_CRISIS
                        st.markdown(
                            f'<div style="display:flex;justify-content:space-between;padding:4px 8px;border-bottom:1px solid #2a2a2a;">'
                            f'<span><strong>{name}</strong> <span style="color:#888;font-size:0.85em;">{t}</span> '
                            f'<span style="color:#aaa;font-size:0.8em;">· {mc_str}</span></span>'
                            f'<span style="color:{color};">{arrow} {i["chg"]:+.2f}% &nbsp; ${i["last"]:,.2f}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    st.markdown("")
                st.caption("※ 시가총액 $50B(약 70조원) 이상 종목만 표시")
            else:
                st.markdown("_해당 시총 구간에 신규 Long sign 발생 종목 없음_")

# ── 한국 ──
if kr_err:
    st.warning(f"KOSPI 200 스캔 실패: {kr_err}")
else:
    c1, c2 = st.columns([20, 1])
    with c2:
        with st.popover("❓", use_container_width=True):
            st.markdown(SIGN_CRITERIA["long"])
            st.markdown("---")
            st.markdown("**스캔 대상**: KOSPI 200 전체 종목 (추적 종목 제외)\n\n**데이터 소스**: 네이버 금융\n\n**갱신 주기**: 6시간")
    with c1:
        with st.expander(f"🇰🇷 KOSPI 200 신규 Long Sign ({len(kr_longs)}개)", expanded=False):
            if kr_longs:
                sorted_hits = sorted(kr_longs.items(), key=lambda x: -x[1]["chg"])
                st.markdown("".join(_fmt_row(t, i) for t, i in sorted_hits), unsafe_allow_html=True)
            else:
                st.markdown("_신규 Long sign 발생 종목 없음_")


# ── 새로고침 ──
st.markdown("")
c1, c2, c3 = st.columns([4, 1, 4])
with c2:
    if st.button("REFRESH"):
        st.cache_data.clear()
        st.rerun()

# ── 면책 ──
st.markdown(
    f'<div class="footer">본 대시보드는 투자 참고용이며 최종 판단의 책임은 투자자 본인에게 있습니다.<br>'
    f'매크로 지표는 후행·동행 지표를 포함하며, 시장은 예측 불가능한 요인에 의해 변동될 수 있습니다.</div>',
    unsafe_allow_html=True,
)
