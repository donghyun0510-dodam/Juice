"""
시장 위험 진단 대시보드
매크로 동향(금리/환율/원자재/VIX) + 지수 동향 기반 일일 시장 위험 진단
실행: streamlit run market_dashboard.py
"""

import os as _os

# 컨테이너 타임존 강제 KST — Streamlit/Tornado 로그 타임스탬프까지 반영
_os.environ["TZ"] = "Asia/Seoul"
try:
    import time as _time
    _time.tzset()
except Exception:
    pass

import streamlit as st

# Streamlit Cloud Secrets → 환경변수 브릿지 (notifier/sheet_auth는 os.environ을 읽음)
try:
    for _k in ("GOOGLE_SA_JSON", "GMAIL_APP_PASSWORD", "GSHEET_FOLDER_ID"):
        if _k in st.secrets and _k not in _os.environ:
            _os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

import yfinance as yf
import requests as req
import re

# yfinance 호출에 curl_cffi 세션을 주입해 Streamlit Cloud 공유 IP의 YFRateLimitError 완화.
# 브라우저 TLS 지문(impersonate=chrome)으로 Yahoo rate limit를 대폭 줄임.
_ORIG_YF_TICKER = yf.Ticker
_ORIG_YF_DOWNLOAD = yf.download


def _yf_ticker_with_session(symbol, *args, **kwargs):
    if "session" not in kwargs:
        try:
            s = _get_cffi_session()
            if s is not None:
                kwargs["session"] = s
        except Exception:
            pass
    return _ORIG_YF_TICKER(symbol, *args, **kwargs)


def _yf_download_with_session(*args, **kwargs):
    if "session" not in kwargs:
        try:
            s = _get_cffi_session()
            if s is not None:
                kwargs["session"] = s
        except Exception:
            pass
    return _ORIG_YF_DOWNLOAD(*args, **kwargs)


yf.Ticker = _yf_ticker_with_session
yf.download = _yf_download_with_session
try:
    from curl_cffi import requests as cffi_req
except Exception:
    cffi_req = None

import threading
_CFFI_SESSION = None
_CFFI_WARMED = False
_CFFI_LOCK = threading.Lock()

def _get_cffi_session():
    global _CFFI_SESSION, _CFFI_WARMED
    if cffi_req is None:
        return None
    with _CFFI_LOCK:
        if _CFFI_SESSION is None:
            _CFFI_SESSION = cffi_req.Session(impersonate="chrome")
        if not _CFFI_WARMED:
            try:
                _CFFI_SESSION.get("https://kr.investing.com/", timeout=15)
                _CFFI_WARMED = True
            except Exception:
                pass
    return _CFFI_SESSION
import json
import os
from datetime import datetime, timedelta
import time
from concurrent.futures import ThreadPoolExecutor

MACRO_SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_snapshot.json")
PROMOTED_KR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "promoted_kr.json")
KR_PROMO_TRACKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kr_promotion_tracker.json")
LONG_SIGN_SEEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "long_sign_seen.json")


def load_json_safe(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_json_safe(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

st.set_page_config(page_title="시장 위험 진단", page_icon="📊", layout="wide")

# 장중에만 30초 자동 갱신 (휴장일·장외시간 제외)
def _kst_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Seoul"))


def is_market_open():
    now = _kst_now()
    # 주말 제외 (월=0 ~ 금=4)
    if now.weekday() >= 5:
        return False
    # 한국장 09:00~15:30 또는 미국장 22:30~익일 05:00 (KST)
    t = now.time()
    from datetime import time as dtime
    korea_open = dtime(9, 0) <= t <= dtime(15, 30)
    us_open = t >= dtime(22, 30) or t <= dtime(5, 0)
    # 월요일은 04:00부터 매크로 지표 확인을 위해 자동갱신
    monday_early = now.weekday() == 0 and t >= dtime(4, 0)
    return korea_open or us_open or monday_early

def is_us_cash_open():
    # 미국 현물장 (KST 월~금 22:30~05:00, 요일 경계 처리)
    now = _kst_now()
    from datetime import time as dtime
    t = now.time()
    wd = now.weekday()
    # 22:30~자정 구간은 월~금(0~4), 자정~05:00 구간은 화~토(1~5)
    if t >= dtime(22, 30) and wd <= 4:
        return True
    if t <= dtime(5, 0) and 1 <= wd <= 5:
        return True
    return False

MARKET_OPEN = is_market_open()
US_CASH_OPEN = is_us_cash_open()  # 모듈 로드 시 초기값 — collect_all_data() 호출 시마다 재평가됨
if MARKET_OPEN or US_CASH_OPEN:
    st.markdown('<meta http-equiv="refresh" content="600">', unsafe_allow_html=True)


# ══════════════════════════════════════════
# 데이터 수집 함수
# ══════════════════════════════════════════

def _live_and_prev(tk):
    """1분봉의 최신가(라이브)와 일봉 전일 종가를 함께 반환"""
    live = None
    try:
        m = tk.history(period="2d", interval="1m")
        if len(m):
            live = float(m["Close"].dropna().iloc[-1])
    except Exception:
        pass
    daily = tk.history(period="5d")
    if len(daily) < 1:
        return live, None
    # prev는 "오늘 이전의 마지막 일봉 종가"여야 함.
    # 일봉 마지막 행 날짜가 오늘이면 그 행은 미완성 candle → iloc[-2]가 prev.
    # 오늘이 아니면 iloc[-1]이 어제 종가 = prev.
    try:
        from datetime import datetime as _dt
        last_idx_date = daily.index[-1].date() if len(daily) else None
        today_utc = _dt.utcnow().date()
    except Exception:
        last_idx_date = None
        today_utc = None

    def _prev_close():
        if last_idx_date is not None and today_utc is not None and last_idx_date == today_utc:
            if len(daily) >= 2:
                return float(daily["Close"].iloc[-2])
            return None
        return float(daily["Close"].iloc[-1]) if len(daily) >= 1 else None

    prev = _prev_close()
    if live is not None:
        return live, prev
    # live 없으면 가용한 가장 최근 일봉 종가를 last로, 그 이전 일봉을 prev로
    if len(daily) >= 2:
        return float(daily["Close"].iloc[-1]), float(daily["Close"].iloc[-2])
    return None, None


def get_price_and_change(ticker_symbol):
    try:
        tk = yf.Ticker(ticker_symbol)
        last_close, prev_close = _live_and_prev(tk)
        if last_close is None or prev_close is None:
            return "", "", None
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
        last_close, prev_close = _live_and_prev(tk)
        if last_close is None or prev_close is None:
            print(f"[get_change_pct] {ticker_symbol}: last={last_close} prev={prev_close} — empty", flush=True)
            return "", None
        pct = (last_close - prev_close) / prev_close * 100
        return f"{pct:+.2f}%", pct
    except Exception as e:
        print(f"[get_change_pct] {ticker_symbol}: EXC {type(e).__name__}: {e}", flush=True)
        return "", None


def _scrape_investing(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://kr.investing.com/",
        }
        sess = _get_cffi_session()
        def _do():
            if sess is not None:
                return sess.get(url, headers=headers, timeout=15)
            return req.get(url, headers=headers, timeout=15)
        resp = _do()
        # 403 (Cloudflare burst) → short backoff & retry up to 2 times
        for i in range(2):
            if resp.status_code != 403:
                break
            time.sleep(0.8 + i * 0.7)
            resp = _do()
        text = resp.text
        price_match = re.search(r'data-test="instrument-price-last"[^>]*>([^<]+)', text)
        change_match = re.search(r'data-test="instrument-price-change-percent"[^>]*>([^<]+)', text)
        price_str = price_match.group(1).strip() if price_match else ""
        change_str = change_match.group(1).strip() if change_match else ""
        price_val = float(price_str.replace(",", "")) if price_str else None
        if resp.status_code != 200 or price_val is None:
            print(f"[scrape_investing] FAIL url={url} status={resp.status_code} len={len(text)} match={price_str!r}", flush=True)
        return price_str, change_str, price_val
    except Exception as e:
        print(f"[scrape_investing] url={url} EXC {type(e).__name__}: {e}", flush=True)
        return "", "", None


# ── 원자재: yfinance 선물 primary, investing.com 폴백 ──
def _yf_commodity(ticker_symbol):
    price_str, chg_str, val = get_price_and_change(ticker_symbol)
    if val is not None:
        return price_str, chg_str, val
    return "", "", None


def get_copper_investing():
    # yfinance HG=F ($/lb) × 2204.62 → $/톤
    y = _yf_commodity("HG=F")
    if y[2] is not None:
        ton = y[2] * 2204.62
        return f"{ton:,.0f}", y[1], ton
    return "", "", None


def get_wti_investing():
    return _yf_commodity("CL=F")


def get_brent_investing():
    return _yf_commodity("BZ=F")


def get_gold_investing():
    return _yf_commodity("GC=F")


def get_vix_futures_investing():
    return _yf_commodity("^VIX")


def get_silver_investing():
    return _yf_commodity("SI=F")


# ── 미국 채권: CNBC primary (실시간), yfinance 폴백 (^TNX/^TYX, 15분 지연) ──
YIELD_YF = {"2Y": "2YY=F", "10Y": "^TNX", "30Y": "^TYX"}
CNBC_YIELD_SYM = {"2Y": "US2Y", "10Y": "US10Y", "30Y": "US30Y"}


def _fetch_cnbc_yield(sym):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = req.get(f"https://www.cnbc.com/quotes/{sym}", headers=headers, timeout=15)
        text = resp.text
        m_last = re.search(r'QuoteStrip-lastPrice[^>]*>([0-9.]+)', text) or re.search(r'"last":"?([0-9.]+)"?', text)
        m_chg = re.search(r'QuoteStrip-changePercent[^>]*>\(?(-?[0-9.]+%?)', text) or re.search(r'"change_pct":"([^"]+)"', text)
        if not m_last:
            print(f"[cnbc_yield] FAIL sym={sym} status={resp.status_code}", flush=True)
            return "", "", None
        val = float(m_last.group(1))
        return f"{val:.3f}", (m_chg.group(1) if m_chg else ""), val
    except Exception as e:
        print(f"[cnbc_yield] EXC sym={sym} {type(e).__name__}: {e}", flush=True)
        return "", "", None


def get_yield_investing(maturity):
    return _fetch_cnbc_yield(CNBC_YIELD_SYM.get(maturity, ""))


def _yf_yield(yf_ticker):
    """yfinance 일봉 종가로 수익률 반환 (실시간 1분봉 우선, 없으면 일봉)."""
    try:
        tk = yf.Ticker(yf_ticker)
        try:
            m = tk.history(period="2d", interval="1m")["Close"].dropna()
            if len(m):
                last = float(m.iloc[-1])
                prev = _yf_prev_close(yf_ticker)
                chg_str = f"{(last-prev)/prev*100:+.2f}%" if prev else ""
                return f"{last:.3f}", chg_str, last
        except Exception:
            pass
        d = tk.history(period="10d")["Close"].dropna()
        if len(d) >= 2:
            last = float(d.iloc[-1]); prev = float(d.iloc[-2])
            return f"{last:.3f}", f"{(last-prev)/prev*100:+.2f}%", last
    except Exception:
        pass
    return "", "", None


def get_yield_live(maturity, yf_ticker):
    """CNBC primary (실시간), yfinance 폴백 (15분 지연)."""
    r = _fetch_cnbc_yield(CNBC_YIELD_SYM.get(maturity, ""))
    if r[2] is not None:
        return r
    return _yf_yield(yf_ticker or YIELD_YF.get(maturity, ""))


def scrape_yahoo_quote(url, symbol=None):
    """Yahoo Finance quote 페이지에서 현재가와 등락률 파싱.
    symbol 지정 시 해당 심볼의 fin-streamer만 매칭 (관련종목과 혼동 방지).
    반환: (price_str, pct_str, price_val, pct_val)
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = req.get(url, headers=headers, timeout=15)
        text = resp.text
        if symbol:
            sym_esc = re.escape(symbol)
            # fin-streamer 태그의 속성 순서는 다양 → data-symbol과 data-field 둘 다 포함하는 태그 찾기
            price_val = None
            pct_val = None
            for m in re.finditer(r'<fin-streamer\b([^>]*)>', text):
                attrs = m.group(1)
                if f'data-symbol="{symbol}"' not in attrs:
                    continue
                if 'data-field="regularMarketPrice"' in attrs and price_val is None:
                    v = re.search(r'value="([-\d.]+)"', attrs)
                    if v: price_val = float(v.group(1))
                elif 'data-field="regularMarketChangePercent"' in attrs and pct_val is None:
                    v = re.search(r'value="([-\d.]+)"', attrs)
                    if v: pct_val = float(v.group(1))
                if price_val is not None and pct_val is not None:
                    break
        else:
            pm = re.search(r'data-field="regularMarketPrice"[^>]*value="([-\d.]+)', text)
            cm = re.search(r'data-field="regularMarketChangePercent"[^>]*value="([-\d.]+)', text)
            price_val = float(pm.group(1)) if pm else None
            pct_val = float(cm.group(1)) if cm else None
        # fin-streamer에 없으면 임베드된 이스케이프 JSON에서 시도 (symbol 필요)
        if symbol and (price_val is None or pct_val is None):
            sym_esc = re.escape(symbol)
            pj = re.search(
                rf'\\"symbol\\":\\"{sym_esc}\\".{{0,3000}}?\\"regularMarketPrice\\":\{{\\"raw\\":([-\d.eE]+)',
                text, re.DOTALL,
            )
            cj = re.search(
                rf'\\"symbol\\":\\"{sym_esc}\\".{{0,3000}}?\\"regularMarketChangePercent\\":\{{\\"raw\\":([-\d.eE]+)',
                text, re.DOTALL,
            )
            if price_val is None and pj:
                price_val = float(pj.group(1))
            if pct_val is None and cj:
                pct_val = float(cj.group(1))
        price_str = f"{price_val:,.2f}" if price_val is not None else ""
        pct_str = f"{pct_val:+.2f}%" if pct_val is not None else ""
        return price_str, pct_str, price_val, pct_val
    except Exception:
        return "", "", None, None


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
        # 선형 점수: t1에서 0, t3에서 30, 그 이상은 선형 외삽(캡 40)
        if t3 > t1:
            score = (value - t1) / (t3 - t1) * 30
        else:
            score = 0
        score = max(0, min(40, score))
        # 등급 버킷 (기존과 동일)
        if value <= t1:
            grade = "안정"
        elif value <= t2:
            grade = "주의"
        elif value <= t3:
            grade = "위험"
        else:
            grade = "고위험"
        return grade, score
    return "N/A", 0


def compute_t_risk(bond_2y, bond_10y, bond_30y):
    score_2y = assess_risk("2Y", bond_2y)[1]
    score_10y = assess_risk("10Y", bond_10y)[1]
    score_30y = assess_risk("30Y", bond_30y)[1]

    spread_score = 0
    spread = None
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
    def fx_score(value, t1, t3):
        if value is None:
            return 0
        if t3 <= t1:
            return 0
        s = (value - t1) / (t3 - t1) * 30
        return max(0, min(40, s))

    d = fx_score(dxy, 103, 108)
    j = fx_score(jpy, 145, 158)
    c = fx_score(cny, 7.15, 7.35)
    total = d * 1.67 + j * 1.0 + c * 0.67
    return min(total, 100)


def compute_c_risk(wti, brent, gold, copper, silver=None, btc_chg=None,
                   oil_chg=None, gold_chg=None, silver_chg=None, copper_chg=None):
    # 1) 유가 수준: 85→0, 105→30 선형, 이후 외삽 (캡 40)
    oil_score = 0
    oil_avg = None
    if wti is not None and brent is not None:
        oil_avg = (wti + brent) / 2
        oil_score = max(0, min(40, (oil_avg - 85) / 20 * 30))
    elif wti is not None:
        oil_avg = wti
        oil_score = max(0, min(40, (oil_avg - 85) / 20 * 30))

    # 2) G/C Ratio 수준: 선형 (regime 지표, 비중 축소)
    # Gold($/oz) ÷ Copper($/톤): 정상 0.35~0.55, 높을수록 경기 둔화 신호
    gc_score = 0
    gc_ratio = None
    if gold is not None and copper is not None and copper > 0:
        gc_ratio = gold / copper
        # 0.35→0, 0.55→20 선형, 극단값 캡
        gc_score = max(0, min(25, (gc_ratio - 0.35) / 0.20 * 20))

    # 3) 단기 모멘텀 — 방향성 반영
    # 경기 해석 변수(유가·구리)는 signed, 안전/투기 변수(금·은·BTC)는 변동성 자체를 불안 신호로 간주
    momentum = 0

    # 유가: 상승만 가산(인플레 재점화). 중립선(85) 위에서 급락은 oil_score 상쇄(인플레 완화 신호).
    # 단, 하루 움직임은 구조적 유가 리스크의 최대 50%까지만 해갈 — 구조 수준까지 하루에 뒤집지 않도록 캡.
    v = chg_num(oil_chg)
    if v is not None:
        if v > 2:
            momentum += (v - 2) * 5
        elif v < -2 and oil_score > 0:
            relief = min(oil_score * 0.5, (abs(v) - 2) * 3)
            oil_score -= relief
            momentum += (abs(v) - 2) * 2.5  # 변동성 자체도 리스크 — 상승의 절반 가중치

    # 구리: 하락만 가산(경기침체 우려). 상승은 경기호조 → 비가산
    v = chg_num(copper_chg)
    if v is not None and v < -2:
        momentum += (abs(v) - 2) * 5

    # 금: threshold 2% (안전자산 쏠림 민감)
    v = chg_num(gold_chg)
    if v is not None:
        momentum += max(0, abs(v) - 2) * 5

    # 은·BTC: 자체 변동성 크므로 threshold 4% (노이즈 제거)
    for chg in (silver_chg, btc_chg):
        v = chg_num(chg)
        if v is None:
            continue
        momentum += max(0, abs(v) - 4) * 5

    momentum = min(momentum, 50)

    # oil(가중2.0) + gc(가중1.0) + momentum(그대로) — 총합 캡 100
    total = oil_score * 2.0 + gc_score * 1.0 + momentum
    return min(total, 100), oil_avg, gc_ratio


def compute_vix_score(vix_val):
    # 선형: 15 이하 0, 35 이상 100
    if vix_val is None:
        return 0
    return max(0, min(100, (vix_val - 15) / 20 * 100))


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


def chg_num(chg):
    """등락률 문자열/숫자에서 float 추출 (실패 시 None)"""
    if chg is None or chg == "":
        return None
    if isinstance(chg, (int, float)):
        return float(chg)
    m = re.search(r"([+-]?\d+\.?\d*)", str(chg))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def avg_chg(*vals):
    nums = [chg_num(v) for v in vals]
    nums = [n for n in nums if n is not None]
    return sum(nums) / len(nums) if nums else None


def trend_arrow(chg):
    """등락률(문자열 또는 숫자)을 받아 방향 화살표 HTML 반환"""
    if chg is None or chg == "":
        return ""
    if isinstance(chg, str):
        m = re.search(r"([+-]?\d+\.?\d*)", chg)
        if not m:
            return ""
        try:
            v = float(m.group(1))
        except ValueError:
            return ""
    else:
        v = float(chg)
    if v > 0:
        return f' <span style="color:#e04b4b;font-size:0.85em;">▲{v:.2f}%</span>'
    elif v < 0:
        return f' <span style="color:#3d7fe0;font-size:0.85em;">▼{abs(v):.2f}%</span>'
    return ' <span style="color:#888;font-size:0.85em;">―</span>'


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

def _yf_prev_close(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d")
        if len(h) >= 2:
            return float(h["Close"].iloc[-2])
    except Exception:
        pass
    return None


def _yf_daily_pct(ticker):
    """최근 2개 일봉 종가 간 %변동. compute_c_risk 모멘텀 입력 전용 — 실시간 티커의
    슬라이딩 1-day %change와 달리 세션 휴지기에도 값이 고정(주말 Fri settle 유지).
    단 BTC 등 24/7 종목은 UTC 00:00에 캔들이 롤오버되므로 완전 고정은 아님."""
    try:
        h = yf.Ticker(ticker).history(period="5d")["Close"].dropna()
        if len(h) >= 2:
            prev, last = float(h.iloc[-2]), float(h.iloc[-1])
            if prev > 0:
                return (last - prev) / prev * 100
    except Exception:
        pass
    return None


def _compute_yesterday_baseline():
    """yfinance 일봉 전일 종가로부터 어제의 4대 risk 점수 재구성"""
    try:
        y2 = _yf_prev_close("2YY=F")
        y10 = _yf_prev_close("^TNX")
        y30 = _yf_prev_close("^TYX")
        dxy = _yf_prev_close("DX-Y.NYB")
        jpy = _yf_prev_close("JPY=X")
        cny = _yf_prev_close("CNY=X")
        wti = _yf_prev_close("CL=F")
        brent = _yf_prev_close("BZ=F")
        gold = _yf_prev_close("GC=F")
        copper = _yf_prev_close("HG=F")  # USD/lb
        # 구리 단위: Investing의 USD/ton 대비 스케일링 (1톤≈2204.62lb)
        copper_ton = copper * 2204.62 if copper else None
        vix = _yf_prev_close("^VIX")

        _, t_risk, _ = compute_t_risk(y2, y10, y30)
        fx_risk = compute_fx_risk(dxy, jpy, cny)
        c_risk, _, _ = compute_c_risk(wti, brent, gold, copper_ton)
        vix_score = compute_vix_score(vix)
        macro_total = min(t_risk * 0.30 + fx_risk * 0.25 + c_risk * 0.25 + vix_score * 0.20, 100)
        return {
            "t_risk": t_risk, "fx_risk": fx_risk, "c_risk": c_risk,
            "vix_score": vix_score, "macro_total": macro_total,
        }
    except Exception:
        return {}


@st.cache_data(ttl=300)
def collect_all_data():
    global US_CASH_OPEN
    US_CASH_OPEN = is_us_cash_open()
    data = {}

    # 금리
    # 채권 수익률: 미국 현물장 중에는 yfinance 1분봉, 그 외에는 investing.com (2Y는 yfinance 데이터 불안정으로 항상 investing.com)
    _, data["2y_chg"], data["2y"] = get_yield_live("2Y", None)
    _, data["10y_chg"], data["10y"] = get_yield_live("10Y", "^TNX")
    _, data["30y_chg"], data["30y"] = get_yield_live("30Y", "^TYX")

    # 환율
    _, data["dxy_chg"], data["dxy"] = get_price_and_change("DX-Y.NYB")
    _, data["usd_jpy_chg"], data["usd_jpy"] = get_price_and_change("JPY=X")
    _, data["usd_cny_chg"], data["usd_cny"] = get_price_and_change("CNY=X")

    # 원자재 — WTI/Gold/Copper/Silver는 Investing.com (yfinance Globex 지연 회피, 병렬 수집)
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_wti = ex.submit(get_wti_investing)
        f_copper = ex.submit(get_copper_investing)
        f_gold = ex.submit(get_gold_investing)
        f_silver = ex.submit(get_silver_investing)
        f_brent = ex.submit(get_brent_investing)
    _, data["wti_chg"], data["wti"] = f_wti.result()
    _, data["brent_chg"], data["brent"] = f_brent.result()
    _, data["copper_chg"], data["copper"] = f_copper.result()
    _, data["gold_chg_str"], data["gold"] = f_gold.result()
    _, data["silver_chg"], data["silver"] = f_silver.result()
    _, data["btc_chg_str"], data["btc"] = get_price_and_change("BTC-USD")

    # VIX — 현물장 중이면 ^VIX(yfinance), 폐장 중이면 VIX 선물(Investing.com)
    if US_CASH_OPEN:
        _, data["vix_chg"], data["vix"] = get_price_and_change("^VIX")
        data["vix_is_futures"] = False
    else:
        _, data["vix_chg"], data["vix"] = get_vix_futures_investing()
        data["vix_is_futures"] = True
        # 실패 시 ^VIX 마지막 종가로 폴백
        if data["vix"] is None:
            _, data["vix_chg"], data["vix"] = get_price_and_change("^VIX")
            data["vix_is_futures"] = False

    # 지수 — 현물장 중이면 현물(yfinance), 폐장 중이면 E-mini 선물(Yahoo 페이지 직접 스크랩)
    idx_map_cash = {
        "dow": "^DJI", "nasdaq": "^IXIC", "sp500": "^GSPC", "russell": "^RUT",
    }
    idx_map_fut = {
        "dow":     ("https://finance.yahoo.com/quote/YM%3DF/",  "YM=F"),
        "nasdaq":  ("https://finance.yahoo.com/quote/NQ%3DF/",  "NQ=F"),
        "sp500":   ("https://finance.yahoo.com/quote/ES%3DF/",  "ES=F"),
        "russell": ("https://finance.yahoo.com/quote/RTY%3DF/", "RTY=F"),
    }
    if US_CASH_OPEN:
        for key, tkr in idx_map_cash.items():
            price_str, chg_str, _ = get_price_and_change(tkr)
            data[f"{key}_price_str"] = price_str
            data[f"{key}_chg_str"] = chg_str
            data[f"{key}_chg"] = chg_num(chg_str)
    else:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {
                key: ex.submit(scrape_yahoo_quote, url, sym)
                for key, (url, sym) in idx_map_fut.items()
            }
        for key, f in futs.items():
            price_str, pct_str, _, pct_val = f.result()
            data[f"{key}_price_str"] = price_str
            data[f"{key}_chg_str"] = pct_str
            data[f"{key}_chg"] = pct_val
    data["indices_is_futures"] = not US_CASH_OPEN
    # Micro E-mini 나스닥 선물 (Yahoo 페이지 직접 스크랩)
    _nq_price, _nq_pct_str, _, _nq_pct = scrape_yahoo_quote(
        "https://finance.yahoo.com/quote/MNQ=F/", symbol="MNQ=F"
    )
    data["nq_price_str"] = _nq_price
    data["nq_chg_str"] = _nq_pct_str
    data["nq_chg"] = _nq_pct
    # iShares MSCI Korea ETF (미국 시간대 한국 익스포저 대리)
    _kn_price, _kn_chg_str, _ = get_price_and_change("EWY")
    data["kospi_night_price_str"] = _kn_price
    data["kospi_night_chg_str"] = _kn_chg_str
    data["kospi_night_chg"] = chg_num(_kn_chg_str)

    # 종합 점수 계산
    t_raw, data["t_risk"], data["spread"] = compute_t_risk(data["2y"], data["10y"], data["30y"])
    data["fx_risk"] = compute_fx_risk(data["dxy"], data["usd_jpy"], data["usd_cny"])
    # 모멘텀 입력은 yfinance 일봉 간 %변동을 사용 — 실시간 슬라이딩 %change는 세션
    # 마감 후에도 값이 계속 바뀌어 daily_review 스냅샷과 대시보드 점수가 어긋남.
    with ThreadPoolExecutor(max_workers=6) as ex:
        f_wti_d = ex.submit(_yf_daily_pct, "CL=F")
        f_brent_d = ex.submit(_yf_daily_pct, "BZ=F")
        f_gold_d = ex.submit(_yf_daily_pct, "GC=F")
        f_silver_d = ex.submit(_yf_daily_pct, "SI=F")
        f_copper_d = ex.submit(_yf_daily_pct, "HG=F")
        f_btc_d = ex.submit(_yf_daily_pct, "BTC-USD")
    oil_chg_avg = avg_chg(f_wti_d.result(), f_brent_d.result())
    data["c_risk"], data["oil_avg"], data["gc_ratio"] = compute_c_risk(
        data["wti"], data["brent"], data["gold"], data["copper"],
        silver=data.get("silver"),
        btc_chg=f_btc_d.result(),
        oil_chg=oil_chg_avg,
        gold_chg=f_gold_d.result(),
        silver_chg=f_silver_d.result(),
        copper_chg=f_copper_d.result(),
    )
    data["vix_score"] = compute_vix_score(data["vix"])

    data["macro_total"] = (
        data["t_risk"] * 0.30
        + data["fx_risk"] * 0.25
        + data["c_risk"] * 0.25
        + data["vix_score"] * 0.20
    )
    data["macro_total"] = min(data["macro_total"], 100)

    # 하위 지수 점수 변화 (어제 마지막 값 대비)
    today_str = datetime.now().strftime("%Y-%m-%d")
    stored = {}
    try:
        if os.path.exists(MACRO_SNAPSHOT_PATH):
            with open(MACRO_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
    except Exception:
        stored = {}

    current_scores = {
        "t_risk": data["t_risk"],
        "fx_risk": data["fx_risk"],
        "c_risk": data["c_risk"],
        "vix_score": data["vix_score"],
        "macro_total": data["macro_total"],
    }

    # 구 포맷 호환: {"t_risk":..,"ts":..} → ts 날짜가 오늘이 아니면 baseline으로
    if "today_latest" not in stored and "t_risk" in stored:
        ts_date = (stored.get("ts") or "")[:10]
        if ts_date and ts_date != today_str:
            stored = {"yesterday_final": {k: stored[k] for k in current_scores if k in stored}}

    # 저장된 today_latest의 날짜가 오늘이 아니면 → 그 값이 어제의 마지막 값
    baseline = stored.get("yesterday_final") or {}
    today_latest = stored.get("today_latest") or {}
    if today_latest.get("date") and today_latest.get("date") != today_str:
        baseline = {k: v for k, v in today_latest.items() if k != "date"}
    # baseline 없으면 yfinance 일봉 전일 종가로부터 어제 점수 산출
    if not baseline:
        baseline = _compute_yesterday_baseline()

    for k, v in current_scores.items():
        bv = baseline.get(k)
        data[f"{k}_delta"] = (v - bv) if (bv is not None and v is not None) else None

    new_today = {**current_scores, "date": today_str}
    try:
        with open(MACRO_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "yesterday_final": baseline,
                "today_latest": new_today,
                "ts": datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    try:
        from notifier import check_and_notify_macro, log_timeseries_if_due
        check_and_notify_macro(data.get("macro_total"), scores=data)
        log_timeseries_if_due(data)
    except Exception as e:
        print(f"[macro alert] skip: {e}")

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
        text-align: right; line-height: 1.5;
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
    .sub-card .sc-label {{ font-size: 17px; color: {TEXT_PRIMARY}; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; margin: 0 0 6px 0; }}
    .sub-card .sc-score {{ font-size: 24px; font-weight: 600; margin: 0; line-height: 1.1; }}
    .sub-card .sc-grade {{ font-size: 12px; font-weight: 500; margin-top: 4px; letter-spacing: 0.5px; }}
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
        position: relative; overflow: visible;
    }}
    .idx-card::before {{
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    }}
    .idx-card .idx-name {{ font-size: 15px; font-weight: 700; color: {TEXT_PRIMARY}; letter-spacing: 0.5px; margin: 0 0 4px 0; }}
    .idx-card .idx-info {{
        display: inline-block; margin-left: 4px; color: {TEXT_SECONDARY};
        opacity: 0.55; cursor: help; font-size: 11px; font-weight: normal;
    }}
    .idx-card .idx-info:hover {{ opacity: 1; color: #4da6ff; }}

    /* 클릭/탭 가능 툴팁 (모바일 대응) */
    .tt {{
        display: inline-block; position: relative; cursor: pointer;
        margin-left: 4px; opacity: 0.55; font-size: 11px; color: {TEXT_SECONDARY};
        outline: none;
    }}
    .tt:hover, .tt:focus {{ opacity: 1; color: #4da6ff; }}
    .tt-box {{
        display: none; position: absolute; z-index: 100;
        top: 100%; left: 50%; transform: translateX(-50%); margin-top: 6px;
        background: #1a1a1a; color: #e0e0e0; border: 1px solid {CARD_BORDER};
        border-radius: 6px; padding: 8px 12px; font-size: 11px; font-weight: normal;
        white-space: nowrap; line-height: 1.5; text-align: left;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    }}
    .tt:hover .tt-box, .tt:focus .tt-box, .tt:active .tt-box {{ display: block; }}
    .idx-card .idx-val {{ font-size: 18px; font-weight: 600; margin: 4px 0 0 0; font-family: 'Consolas', monospace; }}

    /* 지수 카드 툴팁이 부모 컨테이너에서 잘리지 않도록 overflow 전파 (:has 없이 전역 적용) */
    [data-testid="stHorizontalBlock"],
    [data-testid="stHorizontalBlock"] > div,
    [data-testid="stColumn"],
    [data-testid="stVerticalBlock"],
    [data-testid="stMarkdownContainer"],
    [data-testid="stMarkdown"],
    [data-testid="element-container"] {{
        overflow: visible !important;
    }}
    .idx-card, .idx-card .idx-name, .idx-card p {{ overflow: visible !important; }}

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

    /* expander 스타일 (구버전 + 신버전 Streamlit 모두 대응) */
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
    [data-testid="stExpander"] details {{
        background: {CARD_BG} !important;
        border: 1px solid {CARD_BORDER} !important;
        border-radius: 8px !important;
    }}
    [data-testid="stExpander"] details summary {{
        background: {CARD_BG} !important;
        color: {TEXT_PRIMARY} !important;
    }}
    [data-testid="stExpander"] details[open] summary {{
        background: {CARD_BG} !important;
        color: {TEXT_PRIMARY} !important;
        border-bottom: 1px solid {CARD_BORDER} !important;
    }}
    [data-testid="stExpander"] details > div,
    [data-testid="stExpanderDetails"] {{
        background: {CARD_BG} !important;
        color: {TEXT_PRIMARY} !important;
    }}
    [data-testid="stExpander"] * {{
        color: {TEXT_PRIMARY};
    }}

    /* popover (❓ 설명창) — 흰 배경 → 카드 배경으로 강제 */
    [data-testid="stPopover"] button {{
        background: {CARD_BG} !important;
        color: {TEXT_PRIMARY} !important;
        border: 1px solid {CARD_BORDER} !important;
    }}
    [data-baseweb="popover"] [data-testid="stPopoverBody"],
    [data-testid="stPopoverBody"],
    [data-baseweb="popover"] > div,
    [data-baseweb="popover"] [role="dialog"] {{
        background: {CARD_BG} !important;
        color: {TEXT_PRIMARY} !important;
        border: 1px solid {CARD_BORDER} !important;
    }}
    [data-baseweb="popover"] * {{
        color: {TEXT_PRIMARY} !important;
        background-color: transparent !important;
    }}
    [data-baseweb="popover"] strong, [data-baseweb="popover"] code {{
        color: {ACCENT_GOLD} !important;
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
        margin: 20px 0 14px 0 !important;
    }}
    /* Streamlit element-container도 함께 띄움 (gap: 1rem 기본값 보정) */
    [data-testid="stMarkdownContainer"]:has(> .section-title),
    [data-testid="element-container"]:has(.section-title) {{
        margin-top: 12px !important;
    }}

    /* 모바일 반응형 */
    @media (max-width: 768px) {{
        /* Streamlit 컬럼 래퍼 — 여러 버전 DOM 대응 */
        [data-testid="stHorizontalBlock"],
        div[data-testid="stHorizontalBlock"],
        .stHorizontalBlock {{
            flex-wrap: wrap !important;
            gap: 8px !important;
        }}
        [data-testid="stColumn"],
        [data-testid="column"],
        div[data-testid="stColumn"],
        .stColumn,
        [data-testid="stHorizontalBlock"] > div {{
            min-width: calc(50% - 8px) !important;
            max-width: 100% !important;
            flex: 1 1 calc(50% - 8px) !important;
            width: calc(50% - 8px) !important;
        }}
        /* 카드 컴팩트화 */
        .idx-card {{ padding: 10px 6px; }}
        .idx-card .idx-val {{ font-size: 14px; }}
        .idx-card .idx-name {{ font-size: 13px; letter-spacing: 0.3px; }}
        .section-title {{ font-size: 12px; margin: 14px 0 10px 0 !important; letter-spacing: 1px; }}
        [data-testid="stMarkdownContainer"]:has(> .section-title),
        [data-testid="element-container"]:has(.section-title) {{ margin-top: 8px !important; }}
        .diagnosis-box, .strategy-box {{ padding: 12px 14px; font-size: 13px; }}
        .header-bar h1 {{ font-size: 20px !important; }}
        .header-bar .ts {{ font-size: 10px !important; }}
        section.main > div.block-container {{ padding: 1rem 0.8rem !important; max-width: 100% !important; }}
        .detail-table {{ font-size: 12px; }}
        .detail-table th, .detail-table td {{ padding: 6px 4px !important; }}
    }}

</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════
# 헬퍼: HTML 뱃지
# ══════════════════════════════════════════
def badge_html(g):
    cls = {"안정": "safe", "주의": "caution", "위험": "danger", "고위험": "crisis"}.get(g, "safe")
    return f'<span class="badge badge-{cls}">{g}</span>'

def _tt(name, tkr, source, desc):
    tooltip_text = f"티커: {tkr}<br>출처: {source}<br>{desc}"
    return f'{name} <span class="tt" tabindex="0">ⓘ<span class="tt-box">{tooltip_text}</span></span>'

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
        <h1>JUICE 주식 스카우터</h1>
        <span class="ts">{_kst_now().strftime('%Y-%m-%d %H:%M:%S')} KST<br>{"🟢 10분 자동 갱신" if MARKET_OPEN else "⏸ 휴장 중 (수동 새로고침)"}<br>Yahoo Finance &middot; Investing.com</span>
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
                <div class="gauge-bar-fill" style="width:{min(total,100):.0f}%;background:linear-gradient(to right,{COLOR_SAFE} 0%,{COLOR_CAUTION} 25%,{COLOR_DANGER} 50%,{COLOR_CRISIS} 75%,{COLOR_CRISIS} 100%);background-size:{(100/min(total,100)*100) if total>0 else 100:.1f}% 100%;background-position:0 0;"></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("")

# ── 하위 지수 4개 ──
st.markdown('<p class="section-title">매크로 분석</p>', unsafe_allow_html=True)

sub_indices = [
    ("T-RISK", "금리", d["t_risk"], 100, (25, 50, 75), d.get("t_risk_delta")),
    ("FX-RISK", "환율", d["fx_risk"], 100, (30, 60, 85), d.get("fx_risk_delta")),
    ("C-RISK", "원자재", d["c_risk"], 100, (30, 60, 85), d.get("c_risk_delta")),
    ("VIX", "위험심리", d["vix_score"], 100, (25, 50, 75), d.get("vix_score_delta")),
]

def _delta_html(delta):
    if delta is None:
        return ""
    if abs(delta) < 0.5:
        return ' <span style="color:#888;font-size:0.6em;font-weight:normal;">(0)</span>'
    color = "#e04b4b" if delta > 0 else "#3d7fe0"
    sign = "+" if delta > 0 else ""
    return f' <span style="color:{color};font-size:0.6em;font-weight:normal;">({sign}{delta:.0f})</span>'

def _risk_gradient(th):
    """각 임계값에서 다음 색으로 연속 블렌딩 (경계까지 가까워질수록 점진적으로 섞임)."""
    t1, t2, t3 = th
    return (
        f"linear-gradient(to right,"
        f"{COLOR_SAFE} 0%,"
        f"{COLOR_CAUTION} {t1}%,"
        f"{COLOR_DANGER} {t2}%,"
        f"{COLOR_CRISIS} {t3}%,"
        f"{COLOR_CRISIS} 100%)"
    )


cols = st.columns(4)
for col, (code, label, score, max_score, th, delta) in zip(cols, sub_indices):
    g = risk_grade(score, th)
    sc = grade_css_color(g)
    pct = min(score / max_score * 100, 100)
    # 그라데이션을 max 구간(0~100)으로 펼친 뒤 fill 너비(pct)에 비례해 잘라 보이게 함
    bar_bg_size = f"{(100/pct*100):.1f}% 100%" if pct > 0 else "100% 100%"
    bar_grad = _risk_gradient(th)
    with col:
        st.markdown(
            f"""
            <div class="sub-card">
                <div style="position:absolute;top:0;left:0;right:0;height:2px;background:{sc};"></div>
                <p class="sc-label">{code}</p>
                <p class="sc-score" style="color:{sc};">{score:.0f}{_delta_html(delta)}</p>
                <p class="sc-grade" style="color:{sc};">{label} · {g}</p>
                <div class="sc-bar"><div class="sc-bar-fill" style="width:{pct:.0f}%;background:{bar_grad};background-size:{bar_bg_size};background-position:0 0;"></div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("")

# ── 금리 세부 ──
t_g = risk_grade(d["t_risk"], (25, 50, 75))
with st.expander(f"금리 — T-Risk {d['t_risk']:.0f}점 · {t_g}"):
    rows = []
    _yld_src = "CNBC (primary) / yfinance (fallback)"
    for label, key, val, chg in [
        (_tt("US 2Y",  "US2Y / 2YY=F",  _yld_src, "미국 2년 국채 수익률"),  "2Y",  d["2y"],  d.get("2y_chg")),
        (_tt("US 10Y", "US10Y / ^TNX",  _yld_src, "미국 10년 국채 수익률"), "10Y", d["10y"], d.get("10y_chg")),
        (_tt("US 30Y", "US30Y / ^TYX",  _yld_src, "미국 30년 국채 수익률"), "30Y", d["30y"], d.get("30y_chg")),
    ]:
        if val is not None:
            g, _ = assess_risk(key, val)
            rows.append((label, f"{val:.3f}%{trend_arrow(chg)}", badge_html(g)))
    if d["spread"] is not None:
        sp = d["spread"]
        if sp < 0: sp_b = badge_html("위험")
        elif sp < 0.3: sp_b = badge_html("주의")
        else: sp_b = badge_html("안정")
        def _num2(v):
            if v is None or v == "": return None
            try:
                m = re.search(r"([+-]?\d+\.?\d*)", str(v))
                return float(m.group(1)) if m else None
            except Exception:
                return None
        c10 = _num2(d.get("10y_chg")); c2 = _num2(d.get("2y_chg"))
        sp_chg = (c10 - c2) if (c10 is not None and c2 is not None) else None
        rows.append((_tt("Yield Spread (10Y-2Y)", "^TNX − 2YY=F", "계산값", "장단기 금리차 (음수 = 역전)"), f"{sp:+.2f}%p{trend_arrow(sp_chg)}", sp_b))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 환율 세부 ──
fx_g = risk_grade(d["fx_risk"], (30, 60, 85))
with st.expander(f"환율 — FX-Risk {d['fx_risk']:.0f}점 · {fx_g}"):
    rows = []
    for label, key, val, fmt, chg in [
        (_tt("DXY",     "DX-Y.NYB", "yfinance", "달러 인덱스 (6개 통화 바스켓)"),    "DXY",     d["dxy"],     "{:.2f}", d.get("dxy_chg")),
        (_tt("USD/JPY", "JPY=X",    "yfinance", "달러/엔 환율"),                        "USD/JPY", d["usd_jpy"], "{:.2f}", d.get("usd_jpy_chg")),
        (_tt("USD/CNY", "CNY=X",    "yfinance", "달러/위안 환율"),                      "USD/CNY", d["usd_cny"], "{:.4f}", d.get("usd_cny_chg")),
    ]:
        if val is not None:
            v = fmt.format(val) + trend_arrow(chg)
            b = badge_html(assess_risk(key, val)[0]) if key else "—"
            rows.append((label, v, b))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 원자재 세부 ──
c_g = risk_grade(d["c_risk"], (30, 60, 85))
with st.expander(f"원자재 — C-Risk {d['c_risk']:.0f}점 · {c_g}"):
    rows = []
    _com_src = "yfinance (15분 지연)"
    for label, key, val, fmt, chg in [
        (_tt("Brent Crude", "BZ=F", _com_src, "브렌트유 선물"),         "BRN", d["brent"],      "${:.2f}",      d.get("brent_chg")),
        (_tt("WTI Crude",   "CL=F", _com_src, "서부 텍사스산 원유 선물"), "WTI", d["wti"],        "${:.2f}",      d.get("wti_chg")),
        (_tt("Copper",      "HG=F × 2204.62", _com_src, "구리 선물 ($/톤)"), None, d["copper"], "${:,.0f}/톤",  d.get("copper_chg")),
        (_tt("Silver",      "SI=F", _com_src, "은 선물"),                None,  d.get("silver"), "${:.2f}",      d.get("silver_chg")),
    ]:
        if val is not None:
            v = "" + fmt.format(val) + trend_arrow(chg)
            b = badge_html(assess_risk(key, val)[0]) if key else "—"
            rows.append((label, v, b))
    if d["oil_avg"] is not None:
        _oil_chg_avg = avg_chg(d.get("wti_chg"), d.get("brent_chg"))
        rows.append((_tt("Oil Average", "(WTI + Brent) / 2", "계산값", "유가 평균"), f"${d['oil_avg']:.1f}{trend_arrow(_oil_chg_avg)}", "—"))
    if d["gc_ratio"] is not None:
        gcr = d["gc_ratio"]
        if gcr < 0.35: gcg = "안정"
        elif gcr <= 0.45: gcg = "주의"
        elif gcr <= 0.55: gcg = "위험"
        else: gcg = "고위험"
        # Gold/Copper 비율 변동률 ≈ gold_chg% − copper_chg% (1차 근사)
        def _num(v):
            if v is None or v == "": return None
            try:
                m = re.search(r"([+-]?\d+\.?\d*)", str(v))
                return float(m.group(1)) if m else None
            except Exception:
                return None
        g_c = _num(d.get("gold_chg_str")); c_c = _num(d.get("copper_chg"))
        gcr_chg = (g_c - c_c) if (g_c is not None and c_c is not None) else None
        rows.append((_tt("Gold/Copper Ratio", "Gold ÷ Copper", "계산값", "금/구리 비율 (높을수록 경기 둔화 시그널)"), f"{gcr:.3f}{trend_arrow(gcr_chg)}", badge_html(gcg)))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 위험 심리 세부 ──
v_g = risk_grade(d["vix_score"], (25, 50, 75))
with st.expander(f"위험 심리 — VIX Score {d['vix_score']:.0f}점 · {v_g}"):
    rows = []
    if d["vix"] is not None:
        g, _ = assess_risk("VIX", d["vix"])
        if d.get("vix_is_futures"):
            vix_label = _tt("CBOE VIX (선물)", "^VIX", "yfinance", "VIX 선물 (현물 폐장 시)")
        else:
            vix_label = _tt("CBOE VIX", "^VIX", "yfinance", "CBOE 변동성 지수 (공포지수)")
        rows.append((vix_label, f"{d['vix']:.2f}{trend_arrow(d.get('vix_chg'))}", badge_html(g)))
    if d["gold"] is not None:
        rows.append((_tt("Gold", "GC=F", "yfinance (15분 지연)", "금 선물"), f"${d['gold']:,.0f}{trend_arrow(d.get('gold_chg_str'))}", d.get("gold_chg_str", "")))
    if d["btc"] is not None:
        rows.append((_tt("Bitcoin", "BTC-USD", "yfinance", "비트코인 가격"), f"${d['btc']:,.0f}{trend_arrow(d.get('btc_chg_str'))}", d.get("btc_chg_str", "")))
    st.markdown(detail_table(rows), unsafe_allow_html=True)

# ── 지수 동향 ──
st.markdown('<p class="section-title">지수 현황</p>', unsafe_allow_html=True)

_fut_sfx = " (선물)" if d.get("indices_is_futures") else ""
_fut = d.get("indices_is_futures")
_idx_src = {
    "dow":     ("^DJI", "Dow Jones Industrial Average 현물", "yfinance")     if not _fut else ("YM=F",  "E-mini Dow 선물 (CBOT)", "yfinance"),
    "nasdaq":  ("^IXIC", "NASDAQ Composite 현물", "yfinance")                if not _fut else ("NQ=F",  "E-mini NASDAQ-100 선물 (CME)", "yfinance"),
    "sp500":   ("^GSPC", "S&P 500 현물", "yfinance")                         if not _fut else ("ES=F",  "E-mini S&P 500 선물 (CME)", "yfinance"),
    "russell": ("^RUT", "Russell 2000 현물", "yfinance")                     if not _fut else ("RTY=F", "E-mini Russell 2000 선물 (CME)", "yfinance"),
}
indices = [
    (f"DOW JONES{_fut_sfx}",    d.get("dow_price_str", ""),     d["dow_chg_str"],     d["dow_chg"],     _idx_src["dow"]),
    (f"NASDAQ{_fut_sfx}",       d.get("nasdaq_price_str", ""),  d["nasdaq_chg_str"],  d["nasdaq_chg"],  _idx_src["nasdaq"]),
    (f"S&P 500{_fut_sfx}",      d.get("sp500_price_str", ""),   d["sp500_chg_str"],   d["sp500_chg"],   _idx_src["sp500"]),
    (f"RUSSELL 2000{_fut_sfx}", d.get("russell_price_str", ""), d["russell_chg_str"], d["russell_chg"], _idx_src["russell"]),
    ("MICRO NASDAQ",            d.get("nq_price_str", ""),      d["nq_chg_str"],      d["nq_chg"],      ("MNQ=F", "Micro E-mini NASDAQ-100 선물 (CME)", "Yahoo Finance 페이지")),
    ("EWY (한국 ETF)",           d.get("kospi_night_price_str", ""), d["kospi_night_chg_str"], d["kospi_night_chg"], ("EWY", "iShares MSCI South Korea ETF (미국 상장, KOSPI 대리변수)", "yfinance")),
]
idx_cols = st.columns(len(indices)) if indices else []
for col, (name, price_str, chg_str, chg_val, src) in zip(idx_cols, indices):
    with col:
        if chg_val is None:
            tkr, desc, source = src
            tooltip_text = f"티커: {tkr}<br>출처: {source}<br>{desc}<br>(데이터 미수신)"
            st.markdown(
                f"""<div class="idx-card" style="border-color:#88888840;">
                    <div style="position:absolute;top:0;left:0;right:0;height:2px;background:#888;"></div>
                    <p class="idx-name">{name} <span class="tt" tabindex="0">ⓘ<span class="tt-box">{tooltip_text}</span></span></p>
                    <p class="idx-val" style="color:#888;">—</p>
                </div>""",
                unsafe_allow_html=True,
            )
            continue
        if chg_val is not None:
            if chg_val >= 2: ic = "#4da6ff"
            elif chg_val <= -2: ic = COLOR_CRISIS
            elif chg_val >= 0: ic = COLOR_SAFE
            else: ic = COLOR_CRISIS
            tkr, desc, source = src
            tooltip_text = f"티커: {tkr}<br>출처: {source}<br>{desc}"
            display = f"{price_str} ({chg_str})" if price_str else chg_str
            st.markdown(
                f"""<div class="idx-card" style="border-color:{ic}40;">
                    <div style="position:absolute;top:0;left:0;right:0;height:2px;background:{ic};"></div>
                    <p class="idx-name">{name} <span class="tt" tabindex="0">ⓘ<span class="tt-box">{tooltip_text}</span></span></p>
                    <p class="idx-val" style="color:{ic};">{display}</p>
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


# ── 오늘의 일정 (주간 경제일정 시트에서 당일 row 추출) ──
@st.cache_data(ttl=1800)
def fetch_today_events():
    """주식리뷰 폴더에서 가장 최근 '주간 경제일정_*' 시트의 오늘 row만 반환.
    Returns: (sheet_name | None, list[list[str]] today_rows)."""
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        import gspread
        from googleapiclient.discovery import build
        from sheet_auth import get_credentials
        import os as _os

        kst = _tz(_td(hours=9))
        today_str = _dt.now(kst).strftime("%Y-%m-%d")
        folder_id = _os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")

        creds = get_credentials()
        gc = gspread.authorize(creds)
        drive = build("drive", "v3", credentials=creds)

        q = (f"name contains '주간 경제일정_' and '{folder_id}' in parents "
             f"and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
        results = drive.files().list(q=q, orderBy="name desc", pageSize=10, fields="files(id,name)").execute()
        files = results.get("files", [])
        if not files:
            return None, []

        latest = files[0]
        sh = gc.open_by_key(latest["id"])
        ws = sh.sheet1
        rows = ws.get_all_values()
        if not rows or len(rows) < 2:
            return latest["name"], []

        # 헤더: 날짜 | 요일 | 시간(KST) | 국가 | 지표명 | 예상 | 이전
        today_rows = [r for r in rows[1:] if r and r[0] == today_str]
        return latest["name"], today_rows
    except Exception as e:
        print(f"[today_events] fetch fail: {e}", flush=True)
        return None, []


st.markdown('<p class="section-title">오늘의 일정</p>', unsafe_allow_html=True)
_sheet_name, _today_events = fetch_today_events()

if not _today_events:
    _msg = ("오늘 예정된 주요 경제일정이 없습니다."
            if _sheet_name else
            "주간 경제일정 시트를 찾을 수 없습니다 (일요일 06:00 KST 자동 생성).")
    st.markdown(
        f'<div class="diagnosis-box" style="color:{TEXT_SECONDARY};">{_msg}</div>',
        unsafe_allow_html=True,
    )
else:
    _items_html = ""
    for _r in _today_events:
        _time = _r[2] if len(_r) > 2 else ""
        _country = _r[3] if len(_r) > 3 else ""
        _name = _r[4] if len(_r) > 4 else ""
        _fcst = _r[5] if len(_r) > 5 else ""
        _prev = _r[6] if len(_r) > 6 else ""
        _details = []
        if _fcst:
            _details.append(f"예상 {_fcst}")
        if _prev:
            _details.append(f"이전 {_prev}")
        _detail_str = " · ".join(_details)
        _items_html += (
            f'<li style="margin-bottom:6px;">'
            f'<span style="color:{ACCENT_GOLD};font-family:Consolas,monospace;font-weight:600;">{_time}</span> '
            f'<span style="color:{TEXT_SECONDARY};font-size:0.9em;">[{_country}]</span> '
            f'<strong>{_name}</strong>'
            + (f' <span style="color:#888;font-size:0.85em;">— {_detail_str}</span>' if _detail_str else '')
            + '</li>'
        )
    st.markdown(
        f'<div class="diagnosis-box"><ul style="padding-left:1.2em;margin:0;">{_items_html}</ul></div>',
        unsafe_allow_html=True,
    )


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

# 증시 리뷰 스프레드시트에서 추적 종목 읽기 (source of truth, 실패 시 하드코딩 폴백)
try:
    from sheet_tickers import load_tracking_tickers_from_sheet

    @st.cache_data(ttl=3600)
    def _cached_sheet_tickers():
        return load_tracking_tickers_from_sheet()

    _sheet_universe, _sheet_names = _cached_sheet_tickers()
    if _sheet_universe:
        _has_us = any(k.startswith("🇺🇸") for k in _sheet_universe)
        _has_kr = any(k.startswith("🇰🇷") for k in _sheet_universe)
        # 시트에 있는 쪽만 하드코딩을 대체 (없으면 기존 유지)
        if _has_us:
            STOCK_UNIVERSE = {k: v for k, v in STOCK_UNIVERSE.items() if not k.startswith("🇺🇸")}
        if _has_kr:
            STOCK_UNIVERSE = {k: v for k, v in STOCK_UNIVERSE.items() if not k.startswith("🇰🇷")}
        for k, v in _sheet_universe.items():
            STOCK_UNIVERSE[k] = v
        TICKER_NAMES.update(_sheet_names)
except Exception:
    pass

# 과거에 KOSPI200 스캔에서 Long sign 후 1주 유지되어 자동 편입된 종목
_promoted_kr = load_json_safe(PROMOTED_KR_PATH)
if _promoted_kr:
    STOCK_UNIVERSE.setdefault("🇰🇷 신규 편입", [])
    for _t, _info in _promoted_kr.items():
        if _t not in STOCK_UNIVERSE["🇰🇷 신규 편입"]:
            STOCK_UNIVERSE["🇰🇷 신규 편입"].append(_t)
        TICKER_NAMES.setdefault(_t, _info.get("name", _t))

def _is_kr_ticker(t: str) -> bool:
    return t.endswith(".KS") or t.endswith(".KQ")


@st.cache_data(ttl=600)
def _fetch_kr_daily(ticker: str):
    """한국 종목 일봉 (Naver 경유 FDR). ticker="005930.KS" → code="005930"."""
    try:
        import FinanceDataReader as fdr
        import pandas as pd
        from datetime import datetime as _dt, timedelta as _td
        code = ticker.split(".")[0]
        start = (_dt.now() - _td(days=400)).strftime("%Y-%m-%d")
        df = fdr.DataReader(code, start=start)
        if df is None or len(df) == 0:
            return None
        return df
    except Exception as e:
        print(f"[FDR] {ticker} fail: {e}", flush=True)
        return None


@st.cache_data(ttl=600)
def analyze_trend_signals(all_tickers, with_live=True):
    """종목별 추세 신호 분석 (daily bar, 선택적 장중 라이브 가격 합성, 10분 캐시).
    한국(.KS/.KQ)은 FinanceDataReader(Naver), 그 외는 yfinance 사용.
    with_live=False 시 라이브 1분봉 다운로드 스킵 (대규모 스캔용).
    """
    kr_tickers = [t for t in all_tickers if _is_kr_ticker(t)]
    us_tickers = [t for t in all_tickers if not _is_kr_ticker(t)]

    kr_data = {t: _fetch_kr_daily(t) for t in kr_tickers}

    us_data = None
    if us_tickers:
        try:
            us_data = yf.download(us_tickers, period="1y", auto_adjust=True, progress=False, group_by="ticker")
        except Exception:
            us_data = None

    # 장중 라이브 가격 (US만 — KR은 Naver 일봉 기반)
    live_prices = {}
    if with_live and us_tickers:
        try:
            live_data = yf.download(us_tickers, period="1d", interval="1m",
                                    progress=False, group_by="ticker", prepost=True)
            for t in us_tickers:
                try:
                    if len(us_tickers) == 1:
                        c = live_data["Close"].dropna()
                    else:
                        c = live_data[t]["Close"].dropna()
                    if len(c):
                        live_prices[t] = float(c.iloc[-1])
                except Exception:
                    pass
        except Exception:
            pass

    def _close_for(t):
        if _is_kr_ticker(t):
            df = kr_data.get(t)
            if df is None:
                return None
            return df["Close"].dropna()
        if us_data is None:
            return None
        try:
            if len(us_tickers) == 1:
                return us_data["Close"].dropna()
            return us_data[t]["Close"].dropna()
        except Exception:
            return None

    results = {}
    for t in all_tickers:
        try:
            close = _close_for(t)
            if close is None or len(close) < 200:
                continue
            # 마지막 일봉을 장중 라이브 가격으로 교체 (오늘 종가 대용)
            live = live_prices.get(t)
            if live is not None and live > 0:
                close = close.copy()
                close.iloc[-1] = live
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

            # 당일 상쇄(intraday 역전) 필터: long sign 발생 후 하락으로 당일 수익률이 음(-)이면 취소
            if long_sign_new and prev > 0 and (last / prev - 1) < 0:
                long_sign_new = False

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
                    # 5거래일 전 종가 대비 3% 이상 추가 하락 시에만 Sell (중간 크기 눌림 제외)
                    broke_5d_ago = last < close.iloc[-6] * 0.97 if len(close) >= 6 else False
                    if (prior_slope > 0 and recent_slope < 0
                            and abs(recent_slope) > prior_slope
                            and at_high_zone and broke_5d_ago):
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
                    rec_cum = (recent_s.iloc[-1] / recent_s.iloc[0] - 1) * 100
                    not_uptrend = ps <= 0   # 직전이 상승 추세가 아님 (횡보 또는 하락)
                    if rs < 0 and rec_cum <= -4 and abs(rs) > abs(ps) and not_uptrend:
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
    with st.expander(f"아침 리뷰 대비 신호 변화 — {len(SIGNAL_CHANGES)}개 종목", expanded=False):
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

# 시트 H열 기록은 GitHub Actions intraday_scan.py에서만 수행 (이중 기록 방지)
# 대시보드는 읽기/표시 전용
if False and MARKET_OPEN and SIGNAL_CHANGES:
    from datetime import time as _dtime
    now_t = datetime.now().time()
    target_market = "korea" if _dtime(9, 0) <= now_t <= _dtime(15, 30) else "global"
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
    new_html = NEW_BADGE_HTML if ("NEW_LONG_TICKERS" in globals() and t in NEW_LONG_TICKERS) else ""
    return (
        f'<div style="display:flex;justify-content:space-between;padding:4px 8px;border-bottom:1px solid #2a2a2a;">'
        f'<span><strong>{name}</strong> <span style="color:#888;font-size:0.85em;">{t}</span>{sector_html}{change_html}{new_html}</span>'
        f'<span style="color:{color};">{arrow} {info["chg"]:+.2f}% &nbsp; ${info["last"]:,.2f}</span>'
        f'</div>'
    )

long_hits = [(t, info) for t, info in sig.items() if info["tag"] == "long"]
sell_hits = [(t, info) for t, info in sig.items() if info["tag"] == "sell"]
short_hits = [(t, info) for t, info in sig.items() if info["tag"] == "short"]
cover_hits = [(t, info) for t, info in sig.items() if info["tag"] == "cover"]
hold_long = [(t, info) for t, info in sig.items() if info["tag"] == "hold_long"]
hold_sell = [(t, info) for t, info in sig.items() if info["tag"] == "hold_sell"]

def _sign_section(key, title, hits, caption, expanded=False):
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
_sign_section("short", "🔻 Short Sign — 대세하락 전환", short_hits,
              "200MA 하향이탈 또는 20일 신저가 + 역배열 — 신규 매수 중단 시그널", expanded=False)
_sign_section("cover", "🔺 Short Cover — 하락세 완화", cover_hits,
              "하락권 50MA 상향이탈 — 반등 시도, Long sign 재확립 전까지 관망")
_sign_section("hold_long", "✅ 추세 유지 중 (Long hold)", hold_long,
              "정배열 상태 유지 — 신규 신호 없음, 보유 지속")
_sign_section("hold_sell", "⛔ 하락 추세 지속", hold_sell,
              "역배열 지속 — 신규 매수 보류, Long sign 재확립 대기")


# ── 개별주식 2 - 신규 Long Sign 특징주 ──
# (섹션 타이틀·diff 배너는 scan 및 LAST_DIFF 계산 이후로 이동됨)

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

@st.cache_data(ttl=3600)
def _scan_sp500_long_signs_raw(exclude_set):
    """S&P500 중 추적 종목 외에서 Long sign 발생 종목 스캔 (내부, 무거움).
    상위 scan_sp500_long_signs 에서 장종료 후 1회만 호출되도록 게이팅."""
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
    sig_new = analyze_trend_signals(candidates, with_live=False)
    long_only = {t: info for t, info in sig_new.items() if info["tag"] == "long"}
    return long_only, sector_map, None


US_LONG_SCAN_CACHE = "us_long_scan_daily.json"

def _load_us_scan_cache():
    try:
        import json, os
        if not os.path.exists(US_LONG_SCAN_CACHE):
            return None
        with open(US_LONG_SCAN_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _save_us_scan_cache(payload):
    try:
        import json
        with open(US_LONG_SCAN_CACHE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def scan_sp500_long_signs(exclude_set):
    """장종료(미국 기준 16:00 ET 이후) 후 하루 1회만 실제 스캔. 그 외엔 디스크 캐시 반환."""
    from datetime import datetime
    try:
        import pytz
        now_et = datetime.now(pytz.timezone("America/New_York"))
    except Exception:
        now_et = datetime.now()
    today_str = now_et.strftime("%Y-%m-%d")
    post_close = now_et.hour >= 16  # 16:00 ET 이후
    cache = _load_us_scan_cache()

    if cache and cache.get("date") == today_str:
        long_only = cache.get("long_only", {})
        sector_map = cache.get("sector_map", {})
        return long_only, sector_map, None

    if post_close:
        long_only, sector_map, err = _scan_sp500_long_signs_raw(exclude_set)
        if err is None:
            _save_us_scan_cache({"date": today_str, "long_only": long_only, "sector_map": sector_map})
        return long_only, sector_map, err

    # 장중/프리마켓: 전일 캐시 반환
    if cache:
        return cache.get("long_only", {}), cache.get("sector_map", {}), None
    return {}, {}, None


KR_LONG_SCAN_CACHE = "kr_long_scan_daily.json"

def _load_kr_scan_cache():
    try:
        import json, os
        if not os.path.exists(KR_LONG_SCAN_CACHE):
            return None
        with open(KR_LONG_SCAN_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _save_kr_scan_cache(payload):
    try:
        import json
        with open(KR_LONG_SCAN_CACHE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass

@st.cache_data(ttl=3600)
def _scan_kr_long_signs_raw(exclude_set):
    """실제 스캔 수행 (내부 함수). 호출 시마다 네이버 크롤링 + 신호 분석.
    장종료 후 1회만 호출되도록 상위 scan_kr_long_signs 에서 게이팅."""
    from bs4 import BeautifulSoup
    candidates = []
    name_map = {}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        for market in ["0", "1"]:  # 0=KOSPI, 1=KOSDAQ
            for page in range(1, 5):
                url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={market}&page={page}"
                resp = req.get(url, headers=headers, timeout=15)
                resp.encoding = "euc-kr"
                soup = BeautifulSoup(resp.text, "html.parser")
                for row in soup.select("table.type_2 tr"):
                    tds = row.select("td")
                    if len(tds) < 7:
                        continue
                    a = tds[1].select_one("a")
                    if not a:
                        continue
                    name = a.get_text(strip=True)
                    href = a.get("href", "")
                    cm = re.search(r"code=(\d{6})(?!\w)", href)
                    if not cm:
                        continue
                    code = cm.group(1)
                    suffix = ".KQ" if market == "1" else ".KS"
                    ticker = code + suffix
                    mcap_text = tds[6].get_text(strip=True).replace(",", "")
                    try:
                        mcap_100m = int(mcap_text)  # 억원 단위
                    except ValueError:
                        continue
                    if mcap_100m < 30000:  # 3조원 = 30000억원
                        continue
                    # ETF/ETN 제외
                    if any(kw in name for kw in [
                        "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "HANARO",
                        "ACE", "KOSEF", "TREX", "RISE", "PLUS",
                        "ETN", "ETF", "인버스", "레버리지", "합성",
                    ]):
                        continue
                    if ticker in exclude_set:
                        continue
                    candidates.append(ticker)
                    name_map[ticker] = name
    except Exception as e:
        return {}, {}, str(e)
    candidates = list(dict.fromkeys(candidates))
    sig = analyze_trend_signals(candidates, with_live=False)
    long_only = {t: info for t, info in sig.items() if info["tag"] == "long"}
    # 시총 정보 주입 (위에서 구한 억원 값 대신 fast_info로 정확한 KRW)
    for t, info in long_only.items():
        try:
            info["mcap"] = yf.Ticker(t).fast_info.get("market_cap") or 0
        except Exception:
            info["mcap"] = 0
    name_out = {t: name_map.get(t, t) for t in long_only}
    return long_only, name_out, None


def scan_kr_long_signs(exclude_set):
    """장종료 후(16:00 KST 이후) 하루 1회만 실제 스캔. 그 외엔 디스크 캐시 반환.
    - 오늘 날짜 캐시(post-close 생성) 있으면: 그대로 사용
    - 없고 현재 KST 16:00 이후: 스캔 실행 후 저장
    - 16:00 이전: 전일 캐시 있으면 반환, 없으면 빈 결과
    """
    from datetime import datetime
    try:
        import pytz
        now_kst = datetime.now(pytz.timezone("Asia/Seoul"))
    except Exception:
        now_kst = datetime.now()
    today_str = now_kst.strftime("%Y-%m-%d")
    post_close = now_kst.hour >= 16
    cache = _load_kr_scan_cache()

    if cache and cache.get("date") == today_str:
        return cache.get("long_only", {}), cache.get("name_map", {}), None

    if post_close:
        long_only, name_map, err = _scan_kr_long_signs_raw(exclude_set)
        if err is None:
            _save_kr_scan_cache({"date": today_str, "long_only": long_only, "name_map": name_map})
        return long_only, name_map, err

    # 장중: 전일 캐시 반환 (있으면)
    if cache:
        return cache.get("long_only", {}), cache.get("name_map", {}), None
    return {}, {}, None


exclude_set = set(all_tk)

# 미국 S&P 500 — 미국장 마감(16:00 ET) 후 1회만 스캔
with st.spinner("미국 신규 Long Sign 조회 중 (장종료 후 1회 스캔)..."):
    us_longs, sector_map, us_err = scan_sp500_long_signs(exclude_set)

# 한국 (KOSPI + KOSDAQ, 시총 3조원+) — 장종료(16:00 KST) 후 1회만 스캔
with st.spinner("한국 신규 Long Sign 조회 중 (장종료 후 1회 스캔)..."):
    kr_longs, kr_name_map, kr_err = scan_kr_long_signs(exclude_set)

# 한글명 임시 병합 (_fmt_row 에서 조회)
TICKER_NAMES.update(kr_name_map)


def manage_kr_promotions(kr_long_map, name_map):
    """KOSPI200 Long sign이 1주(7일) 이상 고점 유지되면 추적 리스트로 승격.
    변곡점 깨짐 정의: 주봉 기준 Long sign 당시 종가 대비 -3% 이탈 또는 sell/short 태그 전환.
    """
    tracker = load_json_safe(KR_PROMO_TRACKER_PATH)
    promoted = load_json_safe(PROMOTED_KR_PATH)
    today = datetime.now().date()

    # 신규 Long sign 등록
    for t, info in kr_long_map.items():
        if t in promoted or t in tracker:
            continue
        tracker[t] = {
            "first_long_date": today.isoformat(),
            "trigger_close": float(info.get("last") or 0),
            "name": name_map.get(t, t),
        }

    if not tracker:
        save_json_safe(KR_PROMO_TRACKER_PATH, tracker)
        return {}

    # 추적 중 종목 현재 상태 조회
    cur_sig = analyze_trend_signals(list(tracker.keys()))

    to_delete = []
    newly_promoted = {}
    for t, entry in list(tracker.items()):
        first = datetime.fromisoformat(entry["first_long_date"]).date()
        days_elapsed = (today - first).days
        cur = cur_sig.get(t) or {}
        trigger = entry.get("trigger_close", 0) or 0
        last_close = cur.get("last")
        tag = cur.get("tag", "")
        # 변곡점 깨짐: 종가가 트리거 대비 -3% 이탈 OR sell/short 전환
        broken = False
        if last_close is not None and trigger > 0 and last_close < trigger * 0.97:
            broken = True
        if tag in ("sell", "short", "hold_sell"):
            broken = True
        if broken:
            to_delete.append(t)
            continue
        if days_elapsed >= 7:
            newly_promoted[t] = {
                "name": entry.get("name", t),
                "promotion_date": today.isoformat(),
                "trigger_close": trigger,
            }
            to_delete.append(t)

    for t in to_delete:
        tracker.pop(t, None)

    save_json_safe(KR_PROMO_TRACKER_PATH, tracker)
    if newly_promoted:
        promoted.update(newly_promoted)
        save_json_safe(PROMOTED_KR_PATH, promoted)
    return newly_promoted


_newly_promoted = manage_kr_promotions(kr_longs, kr_name_map)
if _newly_promoted:
    with st.expander(f"🆕 추적 리스트 편입 ({len(_newly_promoted)}개) — 다음 재실행부터 반영", expanded=False):
        for _t, _info in _newly_promoted.items():
            st.markdown(f"- **{_info.get('name', _t)}** (`{_t}`)")

# 10분 자동 갱신 기준으로 신규 등장 Long sign 종목 탐지
# 저장 갱신은 9분 이상 경과 시에만 수행 → 자동새로고침 주기 동안 NEW 배지 유지
_seen_data = load_json_safe(LONG_SIGN_SEEN_PATH)
_prev_seen = set(_seen_data.get("seen", []))
_current_long_set = set(us_longs.keys()) | set(kr_longs.keys())
NEW_LONG_TICKERS = _current_long_set - _prev_seen if _prev_seen else set()
REMOVED_LONG_TICKERS = _prev_seen - _current_long_set if _prev_seen else set()

# 확정된 diff (직전 갱신 사이클에서 발생한 변화) — UI 표시용
LAST_DIFF = {
    "added": _seen_data.get("last_added", []),
    "removed": _seen_data.get("last_removed", []),
    "prev_count": _seen_data.get("last_prev_count"),
    "cur_count": _seen_data.get("last_cur_count"),
    "ts": _seen_data.get("last_diff_ts"),
}

_prev_ts = _seen_data.get("ts")
_should_update = True
if _prev_ts:
    try:
        _elapsed = (datetime.now() - datetime.fromisoformat(_prev_ts)).total_seconds()
        _should_update = _elapsed >= 540  # 9분
    except Exception:
        _should_update = True

if _should_update:
    # diff가 있는 경우에만 last_* 필드를 갱신, 없으면 이전 diff 유지
    has_change = bool(NEW_LONG_TICKERS or REMOVED_LONG_TICKERS)
    save_json_safe(LONG_SIGN_SEEN_PATH, {
        "seen": sorted(_current_long_set),
        "ts": datetime.now().isoformat(),
        "last_added": sorted(NEW_LONG_TICKERS) if has_change else _seen_data.get("last_added", []),
        "last_removed": sorted(REMOVED_LONG_TICKERS) if has_change else _seen_data.get("last_removed", []),
        "last_prev_count": len(_prev_seen) if has_change else _seen_data.get("last_prev_count"),
        "last_cur_count": len(_current_long_set) if has_change else _seen_data.get("last_cur_count"),
        "last_diff_ts": datetime.now().isoformat() if has_change else _seen_data.get("last_diff_ts"),
    })
    if has_change:
        LAST_DIFF = {
            "added": sorted(NEW_LONG_TICKERS),
            "removed": sorted(REMOVED_LONG_TICKERS),
            "prev_count": len(_prev_seen),
            "cur_count": len(_current_long_set),
            "ts": datetime.now().isoformat(),
        }

NEW_BADGE_HTML = (
    '<span style="color:#fff;font-size:0.75em;background:#e04b4b;padding:1px 6px;'
    'border-radius:3px;margin-left:6px;font-weight:700;letter-spacing:0.5px;">NEW</span>'
)

# 섹션 타이틀 (직전 변화 배너는 한국 신규 Long Sign 아래에 표시)
st.markdown('<p class="section-title">개별 주식 2 — 신규 Long Sign (특징주) <span style="font-size:0.7em; color:#888; font-weight:normal;">· 장 마감 후 리뉴얼</span></p>', unsafe_allow_html=True)

def _render_last_diff_banner():
    if not (LAST_DIFF.get("added") or LAST_DIFF.get("removed")):
        return
    def _names(tickers):
        out = []
        for t in tickers:
            nm = TICKER_NAMES.get(t, t)
            out.append(f"{nm}({t})" if nm != t else t)
        return ", ".join(out) if out else "—"
    _prev_n = LAST_DIFF.get("prev_count")
    _cur_n = LAST_DIFF.get("cur_count")
    _ts = (LAST_DIFF.get("ts") or "")[:19].replace("T", " ")
    _parts = []
    if _prev_n is not None and _cur_n is not None:
        _parts.append(f"<strong>{_prev_n} → {_cur_n}</strong>")
    if LAST_DIFF.get("added"):
        _parts.append(f'🟢 신규 {len(LAST_DIFF["added"])}: {_names(LAST_DIFF["added"])}')
    if LAST_DIFF.get("removed"):
        _parts.append(f'🔻 제외 {len(LAST_DIFF["removed"])}: {_names(LAST_DIFF["removed"])}')
    st.markdown(
        f'<div style="background:#1a1a1a;border-left:3px solid #4da6ff;padding:8px 12px;'
        f'margin-top:8px;font-size:0.9em;color:#ccc;">'
        f'직전 변화 <span style="color:#888;font-size:0.8em;">({_ts})</span> — '
        + " &nbsp;•&nbsp; ".join(_parts) +
        '</div>',
        unsafe_allow_html=True,
    )

# ── 미국 ──
if us_err:
    st.warning(f"S&P 500 스캔 실패: {us_err}")
else:
    with st.expander(f"🇺🇸 미국 신규 Long Sign ({len(us_longs)}개)", expanded=False):
        if us_longs:
            sorted_hits = sorted(us_longs.items(), key=lambda x: -x[1]["chg"])
            st.markdown("".join(_fmt_row(t, i) for t, i in sorted_hits), unsafe_allow_html=True)
        else:
            st.markdown("_신규 Long sign 발생 종목 없음_")

# ── 한국 ──
if kr_err:
    st.warning(f"KOSPI 200 스캔 실패: {kr_err}")
else:
    with st.expander(f"🇰🇷 한국 신규 Long Sign ({len(kr_longs)}개)", expanded=False):
            if kr_longs:
                sorted_hits = sorted(kr_longs.items(), key=lambda x: -x[1]["chg"])
                st.markdown("".join(_fmt_row(t, i) for t, i in sorted_hits), unsafe_allow_html=True)
            else:
                st.markdown("_신규 Long sign 발생 종목 없음_")
            _render_last_diff_banner()


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
