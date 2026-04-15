"""스카우터 공통 코어 — 대시보드와 scouter_logger가 공유하는 fetch + compute.

Streamlit 비의존. 이 파일을 수정하면 대시보드와 GH Actions cron 양쪽에 반영됨.
"""
import os
import re
import time
import threading
from datetime import datetime, timedelta, time as dtime, timezone
from zoneinfo import ZoneInfo

import yfinance as yf
import requests as req
import pandas as pd

# ──────────────────────────────────────────────
# curl_cffi 세션 (Yahoo rate limit 우회)
# ──────────────────────────────────────────────
try:
    from curl_cffi import requests as cffi_req
except Exception:
    cffi_req = None

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


# yfinance에 세션 주입
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


# ──────────────────────────────────────────────
# 시간 헬퍼
# ──────────────────────────────────────────────
def _kst_now():
    return datetime.now(ZoneInfo("Asia/Seoul"))


def is_us_cash_open():
    """미국 현물장 (KST 월~금 22:30~05:00)."""
    now = _kst_now()
    t = now.time()
    wd = now.weekday()
    if t >= dtime(22, 30) and wd <= 4:
        return True
    if t <= dtime(5, 0) and 1 <= wd <= 5:
        return True
    return False


# ──────────────────────────────────────────────
# 가격 fetch 헬퍼
# ──────────────────────────────────────────────
def _live_and_prev(tk):
    """1분봉 최신가와 일봉 전일 종가를 반환."""
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
    try:
        last_idx_date = daily.index[-1].date() if len(daily) else None
        today_utc = datetime.utcnow().date()
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


def _yf_prev_close(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d")
        if len(h) >= 2:
            return float(h["Close"].iloc[-2])
    except Exception:
        pass
    return None


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


# 원자재: yfinance 선물 primary
def _yf_commodity(ticker_symbol):
    price_str, chg_str, val = get_price_and_change(ticker_symbol)
    if val is not None:
        return price_str, chg_str, val
    return "", "", None


def get_copper_investing():
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


# 미국 채권: CNBC primary (실시간), yfinance 폴백
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
            last = float(d.iloc[-1])
            prev = float(d.iloc[-2])
            return f"{last:.3f}", f"{(last-prev)/prev*100:+.2f}%", last
    except Exception:
        pass
    return "", "", None


def get_yield_live(maturity, yf_ticker):
    r = _fetch_cnbc_yield(CNBC_YIELD_SYM.get(maturity, ""))
    if r[2] is not None:
        return r
    return _yf_yield(yf_ticker or YIELD_YF.get(maturity, ""))


def scrape_yahoo_quote(url, symbol=None):
    """Yahoo Finance quote 페이지에서 현재가와 등락률 파싱."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = req.get(url, headers=headers, timeout=15)
        text = resp.text
        if symbol:
            price_val = None
            pct_val = None
            for m in re.finditer(r'<fin-streamer\b([^>]*)>', text):
                attrs = m.group(1)
                if f'data-symbol="{symbol}"' not in attrs:
                    continue
                if 'data-field="regularMarketPrice"' in attrs and price_val is None:
                    v = re.search(r'value="([-\d.]+)"', attrs)
                    if v:
                        price_val = float(v.group(1))
                elif 'data-field="regularMarketChangePercent"' in attrs and pct_val is None:
                    v = re.search(r'value="([-\d.]+)"', attrs)
                    if v:
                        pct_val = float(v.group(1))
                if price_val is not None and pct_val is not None:
                    break
        else:
            pm = re.search(r'data-field="regularMarketPrice"[^>]*value="([-\d.]+)', text)
            cm = re.search(r'data-field="regularMarketChangePercent"[^>]*value="([-\d.]+)', text)
            price_val = float(pm.group(1)) if pm else None
            pct_val = float(cm.group(1)) if cm else None
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


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def chg_num(chg):
    """등락률 문자열/숫자에서 float 추출."""
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


# ──────────────────────────────────────────────
# 리스크 점수 계산
# ──────────────────────────────────────────────
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
        if t3 > t1:
            score = (value - t1) / (t3 - t1) * 30
        else:
            score = 0
        score = max(0, min(40, score))
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
    oil_score = 0
    oil_avg = None
    if wti is not None and brent is not None:
        oil_avg = (wti + brent) / 2
        oil_score = max(0, min(40, (oil_avg - 85) / 20 * 30))
    elif wti is not None:
        oil_avg = wti
        oil_score = max(0, min(40, (oil_avg - 85) / 20 * 30))

    gc_score = 0
    gc_ratio = None
    if gold is not None and copper is not None and copper > 0:
        gc_ratio = gold / copper
        gc_score = max(0, min(25, (gc_ratio - 0.35) / 0.20 * 20))

    momentum = 0
    for chg in (oil_chg, gold_chg, silver_chg, copper_chg, btc_chg):
        v = chg_num(chg)
        if v is None:
            continue
        momentum += max(0, abs(v) - 2) * 5
    momentum = min(momentum, 50)

    total = oil_score * 2.0 + gc_score * 1.0 + momentum
    return min(total, 100), oil_avg, gc_ratio


def compute_vix_score(vix_val):
    if vix_val is None:
        return 0
    return max(0, min(100, (vix_val - 15) / 20 * 100))


def _compute_yesterday_baseline():
    """yfinance 일봉 전일 종가로부터 어제의 4대 risk 점수 재구성."""
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
        copper = _yf_prev_close("HG=F")
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


# ──────────────────────────────────────────────
# 통합 수집 — dashboard collect_all_data와 동일한 로직의 점수 산출
# ──────────────────────────────────────────────
def collect_macro_scores() -> dict:
    """대시보드와 동일한 방식으로 fetch하고 매크로 점수 반환.

    반환: {"t_risk", "fx_risk", "c_risk", "vix_score", "macro_total",
           "wti", "brent", "gold", "copper", "vix", "oil_avg", "gc_ratio",
           "wti_chg", "brent_chg", "gold_chg", "silver_chg", "copper_chg",
           "btc_chg", "oil_chg_avg", ...}
    """
    from concurrent.futures import ThreadPoolExecutor
    us_open = is_us_cash_open()

    # 채권
    _, _, y2 = get_yield_live("2Y", None)
    _, _, y10 = get_yield_live("10Y", "^TNX")
    _, _, y30 = get_yield_live("30Y", "^TYX")

    # 환율
    _, _, dxy = get_price_and_change("DX-Y.NYB")
    _, _, jpy = get_price_and_change("JPY=X")
    _, _, cny = get_price_and_change("CNY=X")

    # 원자재 (병렬)
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_wti = ex.submit(get_wti_investing)
        f_copper = ex.submit(get_copper_investing)
        f_gold = ex.submit(get_gold_investing)
        f_silver = ex.submit(get_silver_investing)
        f_brent = ex.submit(get_brent_investing)
    _, wti_chg, wti = f_wti.result()
    _, brent_chg, brent = f_brent.result()
    _, copper_chg, copper = f_copper.result()
    _, gold_chg, gold = f_gold.result()
    _, silver_chg, silver = f_silver.result()
    _, btc_chg, _btc = get_price_and_change("BTC-USD")

    # VIX
    if us_open:
        _, _, vix = get_price_and_change("^VIX")
    else:
        _, _, vix = get_vix_futures_investing()
        if vix is None:
            _, _, vix = get_price_and_change("^VIX")

    # 점수 계산
    oil_chg_avg = avg_chg(wti_chg, brent_chg)
    _, t_risk, _ = compute_t_risk(y2, y10, y30)
    fx_risk = compute_fx_risk(dxy, jpy, cny)
    c_risk, oil_avg, gc_ratio = compute_c_risk(
        wti, brent, gold, copper,
        silver=silver, btc_chg=btc_chg,
        oil_chg=oil_chg_avg, gold_chg=gold_chg,
        silver_chg=silver_chg, copper_chg=copper_chg,
    )
    vix_score = compute_vix_score(vix)
    macro_total = min(
        t_risk * 0.30 + fx_risk * 0.25 + c_risk * 0.25 + vix_score * 0.20, 100
    )

    return {
        "t_risk": t_risk,
        "fx_risk": fx_risk,
        "c_risk": c_risk,
        "vix_score": vix_score,
        "macro_total": macro_total,
        # 디버그·참조용
        "y2": y2, "y10": y10, "y30": y30,
        "dxy": dxy, "jpy": jpy, "cny": cny,
        "wti": wti, "brent": brent, "gold": gold, "copper": copper, "silver": silver,
        "vix": vix,
        "oil_avg": oil_avg, "gc_ratio": gc_ratio,
        "wti_chg": wti_chg, "brent_chg": brent_chg,
        "gold_chg": gold_chg, "silver_chg": silver_chg,
        "copper_chg": copper_chg, "btc_chg": btc_chg,
        "oil_chg_avg": oil_chg_avg,
    }
