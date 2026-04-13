"""
미국/한국 증시 일일 리뷰 자동화 스크립트
- 매일 실행하면 '증시 리뷰_YYMMDD' 스프레드시트를 생성
- 자동 수집 가능한 데이터(가격, 지수, 환율 등)를 채우고
- 뉴스/코멘트 등 수동 항목은 빈칸으로 남김
"""

import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import gspread
from googleapiclient.discovery import build
import yfinance as yf
import requests as req
from market_common import classify_signal, SIGNAL_LABEL_KR, save_snapshot
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
import time
from sheet_auth import get_credentials

FOLDER_ID = os.environ.get("GSHEET_FOLDER_ID", "1oCzJUMAklZwXqBR67CmvzmFdZGg3wLuv")

creds = get_credentials()
gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)


# ── 경제지표 함수 (investing.com 경제 캘린더) ──

def get_direction(actual, prev):
    """현수치와 이전값 비교해서 방향 표시 (▲/▼/=)"""
    try:
        a = float(actual.replace("%", "").replace("K", "").replace("B", "").replace("M", "").replace(",", "").replace("T", ""))
        p = float(prev.replace("%", "").replace("K", "").replace("B", "").replace("M", "").replace(",", "").replace("T", ""))
        if a > p:
            return "▲"
        elif a < p:
            return "▼"
        else:
            return "="
    except (ValueError, AttributeError):
        return ""


def build_economic_indicators(target_date, country="US"):
    """investing.com 경제 캘린더에서 전날 발표된 경제지표 수집
    country: "US" (미국 3성급) 또는 "KR" (한국 2성급 이상)
    """
    country_map = {
        "US": {"code": "5", "importance": ["3"], "label": "미국"},
        "KR": {"code": "11", "importance": ["2", "3"], "label": "한국"},
    }
    cfg = country_map[country]
    date_str = target_date.strftime("%Y-%m-%d")
    print(f"  investing.com: {date_str} 발표 {cfg['label']} 경제지표 확인 중...")

    url = "https://kr.investing.com/economic-calendar/Service/getCalendarFilteredData"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://kr.investing.com/economic-calendar/",
    }
    payload = {
        "dateFrom": date_str,
        "dateTo": date_str,
        "country[]": cfg["code"],
        "importance[]": cfg["importance"],
    }

    try:
        resp = req.post(url, headers=headers, data=payload, timeout=15)
        data = resp.json()
        html = data.get("data", "")
    except Exception as e:
        print(f"    investing.com 호출 실패: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr", class_="js-event-item")

    results = []
    for row in rows:
        # 이벤트명
        event_td = row.find("td", class_="event")
        name = event_td.get_text(strip=True) if event_td else ""

        # 실제값
        actual_td = row.find("td", class_=re.compile("act"))
        actual = actual_td.get_text(strip=True) if actual_td else ""

        # 예상값
        forecast_td = row.find("td", class_=re.compile("fore"))
        forecast = forecast_td.get_text(strip=True) if forecast_td else ""

        # 이전값
        prev_td = row.find("td", class_=re.compile("prev"))
        prev = prev_td.get_text(strip=True) if prev_td else ""

        # 실제값이 발표된 것만 (빈칸이면 아직 미발표)
        if not actual:
            continue

        direction = get_direction(actual, prev)

        results.append((
            name,                                    # 지표명 (예: "근원 소비지출물가지수 (YoY) (2월)")
            f"{actual} {direction}".strip(),          # 내용 (예: "3.0% ▼")
            f"예상: {forecast} / 이전: {prev}",       # 비고 (예: "예상: 3.0% / 이전: 3.1%")
        ))
        print(f"    {name}: {actual} {direction} (예상: {forecast} / 이전: {prev})")

    if not results:
        print("    전날 발표된 주요 경제지표 없음")

    return results


# ── 유틸 함수 ──
def get_change_pct(ticker_symbol):
    """전일 대비 등락률(%) 반환"""
    try:
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period="5d")
        if len(hist) < 2:
            return ""
        prev_close = hist["Close"].iloc[-2]
        last_close = hist["Close"].iloc[-1]
        pct = (last_close - prev_close) / prev_close * 100
        return f"{pct:+.2f}%"
    except Exception:
        return ""


def get_price(ticker_symbol):
    """최근 종가 반환"""
    try:
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period="5d")
        if len(hist) < 1:
            return ""
        price = hist["Close"].iloc[-1]
        if price >= 1000:
            return f"{price:,.0f}"
        elif price >= 100:
            return f"{price:.2f}"
        else:
            return f"{price:.3f}"
    except Exception:
        return ""


def get_price_and_change(ticker_symbol):
    """종가와 등락률 둘 다 반환"""
    try:
        tk = yf.Ticker(ticker_symbol)
        hist = tk.history(period="5d")
        if len(hist) < 2:
            return "", ""
        prev_close = hist["Close"].iloc[-2]
        last_close = hist["Close"].iloc[-1]
        pct = (last_close - prev_close) / prev_close * 100
        if last_close >= 1000:
            price_str = f"{last_close:,.0f}"
        elif last_close >= 100:
            price_str = f"{last_close:.2f}"
        else:
            price_str = f"{last_close:.3f}"
        return price_str, f"{pct:+.2f}%"
    except Exception:
        return "", ""


# ── 위험 진단 함수 ──
def parse_price(price_str):
    """포맷된 가격 문자열을 숫자로 변환"""
    try:
        return float(price_str.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def assess_risk(indicator, value):
    """지표별 위험 수준 판단 → (라벨, RGB색상)"""
    if value is None:
        return "", None

    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    # (안정 상한, 주의 상한, 위험 상한) → 초과 시 고위험
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
            return "안정", GREEN
        elif value <= t2:
            return "주의", YELLOW
        elif value <= t3:
            return "위험", ORANGE
        else:
            return "고위험", RED

    return "", None


def _scrape_investing(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        resp = req.get(url, headers=headers, timeout=15)
        text = resp.text
        price_match = re.search(r'data-test="instrument-price-last"[^>]*>([^<]+)', text)
        change_match = re.search(r'data-test="instrument-price-change-percent"[^>]*>([^<]+)', text)
        price_str = price_match.group(1).strip() if price_match else ""
        change_str = change_match.group(1).strip() if change_match else ""
        price_val = float(price_str.replace(",", "")) if price_str else None
        return price_str, change_str, price_val
    except Exception as e:
        print(f"    investing.com 수집 실패 ({url}): {e}")
        return "", "", None


def get_copper_investing():
    return _scrape_investing("https://kr.investing.com/commodities/copper?cid=959211")


def get_wti_investing():
    return _scrape_investing("https://kr.investing.com/commodities/crude-oil")


def get_gold_investing():
    return _scrape_investing("https://kr.investing.com/commodities/gold")


def get_silver_investing():
    return _scrape_investing("https://kr.investing.com/commodities/silver")


def get_vix_futures_investing():
    return _scrape_investing("https://kr.investing.com/indices/us-spx-vix-futures")


def parse_pct(chg_str):
    """'+0.47%' 또는 '(-3.99%)' 문자열 → float. 실패 시 None"""
    if not chg_str:
        return None
    m = re.search(r"([+-]?\d+\.?\d*)", str(chg_str))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def linear_risk_score(value, t1, t3, cap=40):
    """t1에서 0, t3에서 30, 선형 외삽 (최대 cap)"""
    if value is None or t3 <= t1:
        return 0
    s = (value - t1) / (t3 - t1) * 30
    return max(0, min(cap, s))


def assess_copper_risk(current_price=None):
    """구리 위험 진단: 전고점 대비 하락률 기반 (investing.com 가격 사용)"""
    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    try:
        # investing.com 페이지에서 과거 데이터는 제한적이므로 yfinance로 추세 판단
        tk = yf.Ticker("HG=F")
        hist = tk.history(period="2y")
        if len(hist) < 20:
            return "", None

        # yfinance 기준 비율 계산 (단위 무관, 비율만 사용)
        current = hist["Close"].iloc[-1]
        peak = hist["Close"].max()
        drawdown_pct = (current - peak) / peak * 100  # 음수

        # 전년도 데이터 (1년 전 이전)
        one_year_ago = hist.index[-1] - timedelta(days=365)
        last_year_data = hist[hist.index < one_year_ago]

        if drawdown_pct > -10:
            return "안정", GREEN
        elif drawdown_pct > -20:
            return "주의", YELLOW
        elif len(last_year_data) > 0 and current < last_year_data["Close"].min():
            return "고위험", RED
        else:
            return "위험", ORANGE
    except Exception:
        return "", None


def compute_c_risk_index(wti_val, brent_val, gold_val, copper_val,
                         oil_chg=None, gold_chg=None, silver_chg=None,
                         copper_chg=None, btc_chg=None):
    """원자재 종합 위험 지수 (선형 + 단기 모멘텀)
    유가 수준(선형 0@85→30@105, 가중 2.0) + G/C 비율(선형 0@0.35→20@0.55, 가중 1.0)
    + 단기 모멘텀(원자재·BTC 일변동 절대값 Σ max(0,|%|-2)*5, 최대 50)
    """
    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    oil_score = 0
    oil_avg = None
    if wti_val is not None and brent_val is not None:
        oil_avg = (wti_val + brent_val) / 2
        oil_score = max(0, min(40, (oil_avg - 85) / 20 * 30))
    elif wti_val is not None:
        oil_avg = wti_val
        oil_score = max(0, min(40, (oil_avg - 85) / 20 * 30))

    gc_score = 0
    gc_ratio = None
    if gold_val is not None and copper_val is not None and copper_val > 0:
        gc_ratio = gold_val / copper_val
        gc_score = max(0, min(25, (gc_ratio - 0.35) / 0.20 * 20))

    # 단기 모멘텀
    momentum = 0
    for chg in (oil_chg, gold_chg, silver_chg, copper_chg, btc_chg):
        v = parse_pct(chg) if isinstance(chg, str) else chg
        if v is None:
            continue
        momentum += max(0, abs(v) - 2) * 5
    momentum = min(momentum, 50)

    total = oil_score * 2.0 + gc_score * 1.0 + momentum
    total = min(total, 100)

    # 디버그 출력
    if oil_avg and gc_ratio:
        print(f"  C-Risk: 유가평균=${oil_avg:.1f}, G/C비율={gc_ratio:.3f}")
    print(f"  C-Risk: 유가={oil_score:.1f} G/C={gc_score:.1f} 모멘텀={momentum:.0f} 종합={total:.0f}점")

    if total <= 30:
        return f"안정({total:.0f}점)", GREEN, total
    elif total <= 60:
        return f"주의({total:.0f}점)", YELLOW, total
    elif total <= 85:
        return f"위험({total:.0f}점)", ORANGE, total
    else:
        return f"고위험({total:.0f}점)", RED, total


def compute_fx_risk_index(dxy_val, jpy_val, cny_val):
    """FX 종합 위험 지수 2.0 (Tri-Axis Model)
    DXY 50%, USD/JPY 30%, USD/CNY 20%
    100점 환산: DXY×1.67 + JPY×1.0 + CNY×0.67
    """
    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    dxy_score = linear_risk_score(dxy_val, 103, 108)
    jpy_score = linear_risk_score(jpy_val, 145, 158)
    cny_score = linear_risk_score(cny_val, 7.15, 7.35)

    total = dxy_score * 1.67 + jpy_score * 1.0 + cny_score * 0.67
    total = min(total, 100)

    if total <= 30:
        return f"안정({total:.0f}점)", GREEN, total
    elif total <= 60:
        return f"주의({total:.0f}점)", YELLOW, total
    elif total <= 85:
        return f"위험({total:.0f}점)", ORANGE, total
    else:
        return f"고위험({total:.0f}점)", RED, total


def compute_t_risk_index(bond_2y_val, bond_10y_val, bond_30y_val):
    """국채 금리 종합 위험 지수 (T-Risk Index) 산출
    가중치: 2Y 40%, 10Y 30%, 30Y 10%, 장단기 금리차(10Y-2Y) 20%
    """
    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    # 각 금리 수치별 점수 (선형)
    score_2y = linear_risk_score(bond_2y_val, 4.5, 5.2)
    score_10y = linear_risk_score(bond_10y_val, 4.2, 4.8)
    score_30y = linear_risk_score(bond_30y_val, 4.3, 5.2)

    # 장단기 금리차 점수
    spread_score = 0
    if bond_10y_val is not None and bond_2y_val is not None:
        current_spread = bond_10y_val - bond_2y_val

        # 역전 후 급격한 정상화 감지 (과거 역전 → 현재 정상화)
        rapid_normalization = False
        try:
            import pandas as pd
            tk_10y = yf.Ticker("^TNX")
            tk_2y = yf.Ticker("2YY=F")
            hist_10y = tk_10y.history(period="6mo")["Close"]
            hist_2y = tk_2y.history(period="6mo")["Close"]
            spread_hist = pd.DataFrame({"10Y": hist_10y, "2Y": hist_2y}).dropna()
            if len(spread_hist) > 20:
                spread_hist["spread"] = spread_hist["10Y"] - spread_hist["2Y"]
                recent = spread_hist.tail(60)  # 최근 ~3개월
                was_inverted = (recent["spread"] < 0).any()
                if was_inverted and current_spread > 0:
                    min_spread = recent["spread"].min()
                    if current_spread - min_spread >= 0.5:
                        rapid_normalization = True
        except Exception:
            pass

        if rapid_normalization:
            spread_score = 30
        elif current_spread <= -0.5:
            spread_score = 20
        elif current_spread < 0:
            spread_score = 10
        else:
            spread_score = 0

    # 가중 합산
    total = score_2y * 0.4 + score_10y * 0.3 + score_30y * 0.1 + spread_score * 0.2

    # T-Risk는 0~30 스케일 → 100점 환산
    normalized = total * (100 / 30)

    if total <= 5:
        return f"안정({total:.0f}점)", GREEN, normalized
    elif total <= 10:
        return f"주의({total:.0f}점)", YELLOW, normalized
    elif total <= 20:
        return f"위험({total:.0f}점)", ORANGE, normalized
    else:
        return f"고위험({total:.0f}점)", RED, normalized


def compute_macro_composite(t_risk_score, fx_risk_score, c_risk_score, vix_val):
    """매크로 종합 점수
    금리(T-Risk) 30%, 환율(FX-Risk) 25%, 원자재(C-Risk) 25%, VIX 20%
    모두 100점 스케일로 통일 후 가중합산
    """
    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    # VIX → 100점 환산 (선형: 15→0, 35→100)
    vix_score = 0
    if vix_val is not None:
        vix_score = max(0, min(100, (vix_val - 15) / 20 * 100))

    total = (t_risk_score * 0.30
             + fx_risk_score * 0.25
             + c_risk_score * 0.25
             + vix_score * 0.20)
    total = min(total, 100)

    print(f"  매크로 종합: 금리={t_risk_score:.0f} 환율={fx_risk_score:.0f} 원자재={c_risk_score:.0f} VIX={vix_score:.0f} → {total:.0f}점")

    if total <= 25:
        return f"안정({total:.0f}점)", GREEN, total, vix_score
    elif total <= 50:
        return f"주의({total:.0f}점)", YELLOW, total, vix_score
    elif total <= 75:
        return f"위험({total:.0f}점)", ORANGE, total, vix_score
    else:
        return f"고위험({total:.0f}점)", RED, total, vix_score


def scan_featured_stocks(existing_tickers):
    """S&P 500 중 추적 외 + 시총 $50B+ + Long Sign 종목 스캔
    (대시보드 '개별 주식 2 신규 Long Sign'과 동일 기준)"""
    import pandas as pd
    from market_common import analyze_trend_signals as common_trend_signals

    print("  특징주 스캔 중 (S&P 500, 대시보드와 동일 기준)...")

    try:
        from io import StringIO
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = req.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=headers, timeout=15)
        tables = pd.read_html(StringIO(resp.text))
        sp500_df = tables[0]
        sp500_df["Ticker"] = sp500_df["Symbol"].str.replace(".", "-", regex=False)
        sp500 = sp500_df["Ticker"].tolist()
        sector_map = dict(zip(sp500_df["Ticker"], sp500_df["GICS Sector"]))
    except Exception as e:
        print(f"    S&P 500 목록 가져오기 실패: {e}")
        return []

    existing = set(existing_tickers)
    candidates = [t for t in sp500 if t not in existing]
    print(f"    스캔 대상: {len(candidates)}개 종목")

    sig = common_trend_signals(candidates)
    long_only = {t: info for t, info in sig.items() if info.get("tag") == "long"}
    print(f"    Long Sign 감지: {len(long_only)}개")

    featured = []
    MCAP_MIN = 50_000_000_000  # $50B (대시보드와 동일)
    for ticker, info in long_only.items():
        try:
            tk_info = yf.Ticker(ticker)
            mcap = getattr(tk_info.fast_info, "market_cap", None) or 0
        except Exception:
            mcap = 0
        if mcap < MCAP_MIN:
            continue
        sector = sector_map.get(ticker, "")
        pct = info.get("chg", 0.0)
        featured.append((ticker, f"{pct:+.2f}%", "Long Sign", "Long Sign", sector))

    print(f"    특징주 {len(featured)}개 ($50B+ 필터 적용)")
    return featured


# ── 글로벌 시트 데이터 구성 ──
def build_global_sheet(target_date):
    print("글로벌 데이터 수집 중...")

    # 경제지표 (FRED) - 전날 발표된 것만
    econ_items = build_economic_indicators(target_date)

    # 매크로 - 금리
    bond_2y, bond_2y_chg = get_price_and_change("2YY=F")
    bond_10y, bond_10y_chg = get_price_and_change("^TNX")
    bond_30y, bond_30y_chg = get_price_and_change("^TYX")

    # 매크로 - 환율
    dxy, dxy_chg = get_price_and_change("DX-Y.NYB")
    eur_usd, eur_usd_chg = get_price_and_change("EURUSD=X")
    usd_jpy, usd_jpy_chg = get_price_and_change("JPY=X")
    usd_cny, usd_cny_chg = get_price_and_change("CNY=X")

    # 매크로 - 원자재 (Investing.com 실시간 스크래핑)
    brent, brent_chg = get_price_and_change("BZ=F")
    print("  investing.com: WTI/Gold/Silver/Copper/VIX선물 가격 확인 중...")
    wti, wti_chg, _wti_val = get_wti_investing()
    gold_price, gold_chg, _gold_val = get_gold_investing()
    silver_price, silver_chg, silver_val = get_silver_investing()
    copper, copper_chg, copper_val = get_copper_investing()

    # 위험 심리 — VIX는 현물가 우선, 실패 시 선물
    vix_price, vix_chg = get_price_and_change("^VIX")
    vix_is_futures = False
    if not vix_price:
        _vp, _vc, _ = get_vix_futures_investing()
        vix_price, vix_chg = _vp, _vc
        vix_is_futures = True
    btc_price, btc_chg = get_price_and_change("BTC-USD")

    # 지수 등락률
    dow_chg = get_change_pct("^DJI")
    nasdaq_chg = get_change_pct("^IXIC")
    sp500_chg = get_change_pct("^GSPC")
    russell_chg = get_change_pct("^RUT")

    # 섹터/종목 — 기존 시트가 있으면 그 종목 리스트를 우선 사용 (source of truth)
    _sheet_us_sectors = {}
    try:
        from sheet_tickers import load_tracking_tickers_from_sheet
        _u, _ = load_tracking_tickers_from_sheet()
        for _label, _tks in _u.items():
            if _label.startswith("🇺🇸"):
                _sec = _label.replace("🇺🇸", "").strip() or "기타"
                _sheet_us_sectors.setdefault(_sec, []).extend(_tks)
    except Exception:
        pass

    # 섹터/종목 - 미국 개별종목 등락률
    us_stocks = {
        # 반도체
        "SOX": "^SOX", "INTC": "INTC", "AMD": "AMD", "ARM": "ARM",
        "AMAT": "AMAT", "ASML": "ASML", "LRCX": "LRCX", "MU": "MU",
        "TXN": "TXN", "NVDA": "NVDA", "AVGO": "AVGO", "MRVL": "MRVL",
        "COHR": "COHR", "SNDK": "SNDK",
        # 하드웨어
        "DELL": "DELL", "ANET": "ANET", "CSCO": "CSCO",
        # 빅테크
        "ORCL": "ORCL", "AAPL": "AAPL", "MSFT": "MSFT", "AMZN": "AMZN",
        "GOOG": "GOOG", "META": "META", "NFLX": "NFLX",
        # 소프트웨어
        "IBM": "IBM", "ADBE": "ADBE", "CRM": "CRM", "DDOG": "DDOG",
        "CRWD": "CRWD", "SNOW": "SNOW", "PLTR": "PLTR", "NOW": "NOW",
        "SAP": "SAP", "ISRG": "ISRG", "RDNT": "RDNT", "TEM": "TEM",
        # 전력/원전
        "ETN": "ETN", "VRT": "VRT", "GEV": "GEV", "SMR": "SMR",
        "CEG": "CEG", "TLN": "TLN", "BWXT": "BWXT", "SU": "SU", "CMI": "CMI",
        # Physical AI
        "TSLA": "TSLA", "DE": "DE", "SYM": "SYM",
        # 은행
        "C": "C", "JPM": "JPM", "BAC": "BAC", "GS": "GS",
        # 자동차
        "GM": "GM", "F": "F", "CON": "CON",
        # 에너지
        "XOM": "XOM", "COP": "COP", "BP": "BP", "TTE": "TTE",
        # 소재
        "DD": "DD", "DOW_stock": "DOW", "AA": "AA", "FCX": "FCX",
        # 기계/운송
        "CAT": "CAT", "FDX": "FDX", "UPS": "UPS",
        # 소비/유통
        "TGT": "TGT", "HD": "HD", "LOW": "LOW", "NKE": "NKE",
        # 친환경
        "NEE": "NEE",
    }
    if _sheet_us_sectors:
        us_stocks = {t: t for tks in _sheet_us_sectors.values() for t in tks}
        print(f"  시트에서 미국 추적 종목 {len(us_stocks)}개 로드 (하드코딩 덮어씀)")

    # 일괄 다운로드
    print("  미국 종목 데이터 다운로드 중...")
    tickers_list = list(set(us_stocks.values()))
    stock_changes = {}
    try:
        data = yf.download(tickers_list, period="5d", progress=False)
        close = data["Close"]
        for label, ticker in us_stocks.items():
            try:
                col = close[ticker].dropna()
                if len(col) >= 2:
                    pct = (col.iloc[-1] - col.iloc[-2]) / col.iloc[-2] * 100
                    stock_changes[label] = f"{pct:+.2f}%"
                else:
                    stock_changes[label] = ""
            except Exception:
                stock_changes[label] = ""
    except Exception:
        stock_changes = {k: "" for k in us_stocks}
    # 스냅샷은 try 블록 이후에 저장 (데이터 일부 실패해도 가능한 것만)

    # 종목 특이사항 체크 (2년 데이터)
    print("  미국 종목 특이사항 확인 중...")
    stock_notes = {}
    stock_signs = {}
    signal_tags = {}  # {ticker: tag} 스냅샷용
    try:
        data_2y = yf.download(tickers_list, period="2y", progress=False)
        close_2y = data_2y["Close"]
        for label, ticker in us_stocks.items():
            try:
                col = close_2y[ticker].dropna()
                if len(col) < 50:
                    stock_notes[label] = ""
                    continue

                current = col.iloc[-1]
                one_year = col.tail(252)
                high_52w = one_year.max()
                low_52w = one_year.min()

                notes = []

                # ── 52주 신고가 / 전고점 돌파 / 신저가 ──
                if current >= high_52w:
                    older = col.iloc[:-252] if len(col) > 252 else None
                    if older is not None and len(older) > 0 and current > older.max():
                        notes.append("전고점 돌파")
                    else:
                        notes.append("52주 신고가")
                elif current <= low_52w:
                    notes.append("52주 신저가")

                # ── 골든크로스 / 데드크로스 (50일선 vs 200일선) ──
                if len(col) >= 200:
                    ma50 = col.rolling(50).mean()
                    ma200 = col.rolling(200).mean()
                    # 오늘 교차 여부: 전일과 비교
                    if len(ma50.dropna()) >= 2 and len(ma200.dropna()) >= 2:
                        prev_diff = ma50.iloc[-2] - ma200.iloc[-2]
                        curr_diff = ma50.iloc[-1] - ma200.iloc[-1]
                        if prev_diff <= 0 and curr_diff > 0:
                            notes.append("골든크로스")
                        elif prev_diff >= 0 and curr_diff < 0:
                            notes.append("데드크로스")

                    # ── 200일선 이탈 / 회복 ──
                    ma200_today = ma200.iloc[-1]
                    ma200_yesterday = ma200.iloc[-2]
                    prev_close = col.iloc[-2]
                    if prev_close >= ma200_yesterday and current < ma200_today:
                        notes.append("200일선 이탈")
                    elif prev_close < ma200_yesterday and current >= ma200_today:
                        notes.append("200일선 회복")

                # ── 고점 대비 낙폭 ──
                if not notes or "52주 신저가" in notes:
                    drawdown = (current - high_52w) / high_52w * 100
                    if drawdown <= -30:
                        notes.append("고점대비 -30%")
                    elif drawdown <= -20:
                        notes.append("고점대비 -20%")
                    elif drawdown <= -10:
                        notes.append("고점대비 -10%")

                stock_notes[label] = " / ".join(notes)

                # ── 매매 신호 판단 (signal-judge 로직, market_common.classify_signal 사용) ──
                sig = classify_signal(col)
                if sig:
                    signal_tags[ticker] = sig["tag"]
                    if sig["tag"] in {"long", "sell", "short", "cover"}:
                        stock_signs[label] = SIGNAL_LABEL_KR[sig["tag"]]
                    else:
                        stock_signs[label] = ""
                else:
                    stock_signs[label] = ""

            except Exception:
                stock_notes[label] = ""
                stock_signs[label] = ""
    except Exception:
        stock_notes = {k: "" for k in us_stocks}
        stock_signs = {k: "" for k in us_stocks}

    # 글로벌 스냅샷 저장 (장중 변화 감지 기준점)
    try:
        save_snapshot(signal_tags, market="global")
        print(f"  글로벌 신호 스냅샷 저장: {len(signal_tags)}개")
    except Exception as e:
        print(f"  스냅샷 저장 실패: {e}")

    # 섹터별 종목 그룹 (시트가 source of truth면 시트 구조 사용)
    if _sheet_us_sectors:
        sector_groups = [(s, tks) for s, tks in _sheet_us_sectors.items()]
    else:
     sector_groups = [
        ("반도체", ["SOX", "INTC", "AMD", "ARM", "AMAT", "ASML", "LRCX", "MU", "TXN", "NVDA", "AVGO", "MRVL", "COHR", "SNDK"]),
        ("하드웨어", ["DELL", "ANET", "CSCO"]),
        ("빅테크", ["ORCL", "AAPL", "MSFT", "AMZN", "GOOG", "META", "NFLX"]),
        ("소프트웨어", ["IBM", "ADBE", "CRM", "DDOG", "CRWD", "SNOW", "PLTR", "NOW", "SAP", "ISRG", "RDNT", "TEM"]),
        ("전력/원전", ["ETN", "VRT", "GEV", "SMR", "CEG", "TLN", "BWXT", "SU", "CMI"]),
        ("Physical AI(자동화 로봇)", ["TSLA", "DE", "SYM"]),
        ("은행", ["C", "JPM", "BAC", "GS"]),
        ("자동차", ["GM", "F", "CON"]),
        ("에너지", ["XOM", "COP", "BP", "TTE"]),
        ("소재", ["DD", "DOW_stock", "AA", "FCX"]),
        ("기계/운송", ["CAT", "FDX", "UPS"]),
        ("소비/유통", ["TGT", "HD", "LOW", "NKE"]),
        ("친환경", ["NEE"]),
    ]

    # 위험 진단
    risk_cells = []   # (1-based 행번호, "F", RGB색상)
    color_cells = []  # (1-based 행번호, 열문자, RGB색상) - 범용 색상 셀

    bond_2y_val = parse_price(bond_2y)
    bond_10y_val = parse_price(bond_10y)
    bond_30y_val = parse_price(bond_30y)

    bond_2y_risk, bond_2y_rc = assess_risk("2Y", bond_2y_val)
    bond_10y_risk, bond_10y_rc = assess_risk("10Y", bond_10y_val)
    bond_30y_risk, bond_30y_rc = assess_risk("30Y", bond_30y_val)
    t_risk_label, t_risk_color, t_risk_score = compute_t_risk_index(bond_2y_val, bond_10y_val, bond_30y_val)
    dxy_val = parse_price(dxy)
    jpy_val = parse_price(usd_jpy)
    cny_val = parse_price(usd_cny)
    dxy_risk, dxy_rc = assess_risk("DXY", dxy_val)
    jpy_risk, jpy_rc = assess_risk("USD/JPY", jpy_val)
    cny_risk, cny_rc = assess_risk("USD/CNY", cny_val)
    fx_risk_label, fx_risk_color, fx_risk_score = compute_fx_risk_index(dxy_val, jpy_val, cny_val)
    brent_val = parse_price(brent)
    wti_val = parse_price(wti)
    gold_val = parse_price(gold_price)
    brent_risk, brent_rc = assess_risk("BRN", brent_val)
    wti_risk, wti_rc = assess_risk("WTI", wti_val)
    copper_risk, copper_rc = assess_copper_risk()
    # 유가 평균 등락
    _wti_pct = parse_pct(wti_chg)
    _brent_pct = parse_pct(brent_chg)
    _oil_pct = None
    if _wti_pct is not None and _brent_pct is not None:
        _oil_pct = (_wti_pct + _brent_pct) / 2
    elif _wti_pct is not None:
        _oil_pct = _wti_pct
    elif _brent_pct is not None:
        _oil_pct = _brent_pct
    c_risk_label, c_risk_color, c_risk_score = compute_c_risk_index(
        wti_val, brent_val, gold_val, copper_val,
        oil_chg=_oil_pct,
        gold_chg=parse_pct(gold_chg),
        silver_chg=parse_pct(silver_chg),
        copper_chg=parse_pct(copper_chg),
        btc_chg=parse_pct(btc_chg),
    )
    vix_val = parse_price(vix_price)
    vix_risk, vix_color = assess_risk("VIX", vix_val)
    macro_label, macro_color, macro_total_val, vix_score_val = compute_macro_composite(
        t_risk_score, fx_risk_score, c_risk_score, vix_val
    )

    # 성과 추적용 메트릭 (스카우터_성과자료 시트에 누적 기록)
    try:
        sp_hist = yf.Ticker("^GSPC").history(period="5d")
        sp500_close = float(sp_hist["Close"].iloc[-1]) if len(sp_hist) >= 1 else None
        sp500_chg_pct = (
            (sp500_close / float(sp_hist["Close"].iloc[-2]) - 1) * 100
            if len(sp_hist) >= 2 else None
        )
    except Exception:
        sp500_close, sp500_chg_pct = None, None
    oil_avg_val = (wti_val + brent_val) / 2 if (wti_val is not None and brent_val is not None) else None
    perf_metrics = {
        "date": target_date.strftime("%Y-%m-%d"),
        "t_risk": round(t_risk_score, 1) if t_risk_score is not None else "",
        "fx_risk": round(fx_risk_score, 1) if fx_risk_score is not None else "",
        "c_risk": round(c_risk_score, 1) if c_risk_score is not None else "",
        "vix_score": round(vix_score_val, 1) if vix_score_val is not None else "",
        "macro_total": round(macro_total_val, 1) if macro_total_val is not None else "",
        "vix": round(vix_val, 2) if vix_val is not None else "",
        "dxy": round(dxy_val, 2) if dxy_val is not None else "",
        "us_10y": round(bond_10y_val, 3) if bond_10y_val is not None else "",
        "oil_avg": round(oil_avg_val, 2) if oil_avg_val is not None else "",
        "sp500_close": round(sp500_close, 2) if sp500_close is not None else "",
        "sp500_chg_pct": round(sp500_chg_pct, 2) if sp500_chg_pct is not None else "",
    }

    # 시트 데이터 구성
    rows = []
    # 헤더
    rows.append(["단계", "주제", "체크포인트", "내용", "비고", "위험 여부"])

    # 1. 시장 뉴스 (수동)
    rows.append(["1.시장 뉴스", "국제", "", "", ""])
    rows.append(["", "정치", "", "", ""])
    rows.append(["", "개별기업", "", "", ""])
    rows.append(["", "", "", "", ""])

    # 2. 경제 지표 (investing.com - 전날 발표된 미국 3성급 지표)
    if econ_items:
        first = True
        for name, actual_dir, expected_prev in econ_items:
            rows.append([
                "2.경제 지표" if first else "",
                "",
                name,              # 예: "근원 개인소비지출 물가지수 (YoY) (2월)"
                actual_dir,        # 예: "3.0% ▼"
                expected_prev,     # 예: "예상: 3.0% / 이전: 3.1%"
            ])
            first = False
    else:
        rows.append(["2.경제 지표", "", "(전날 주요 지표 발표 없음)", "", ""])
    rows.append(["", "", "", "", ""])

    # 3. 매크로 동향
    rows.append(["3.매크로 동향", "금리", "2년물", bond_2y, bond_2y_chg, bond_2y_risk])
    if bond_2y_rc: risk_cells.append((len(rows), bond_2y_rc))
    rows.append(["", "", "10년물", bond_10y, bond_10y_chg, bond_10y_risk])
    if bond_10y_rc: risk_cells.append((len(rows), bond_10y_rc))
    rows.append(["", "", "30년물", bond_30y, bond_30y_chg, bond_30y_risk])
    if bond_30y_rc: risk_cells.append((len(rows), bond_30y_rc))
    rows.append(["", "", "금리 종합 경보", "", "", t_risk_label])
    if t_risk_color: risk_cells.append((len(rows), t_risk_color))
    rows.append(["", "환율", "DXY", dxy, dxy_chg, dxy_risk])
    if dxy_rc: risk_cells.append((len(rows), dxy_rc))
    rows.append(["", "", "EUR/USD", eur_usd, eur_usd_chg, ""])
    rows.append(["", "", "USD/JPY", usd_jpy, usd_jpy_chg, jpy_risk])
    if jpy_rc: risk_cells.append((len(rows), jpy_rc))
    rows.append(["", "", "USD/CNY", usd_cny, usd_cny_chg, cny_risk])
    if cny_rc: risk_cells.append((len(rows), cny_rc))
    rows.append(["", "", "환율 종합 경보", "", "", fx_risk_label])
    if fx_risk_color: risk_cells.append((len(rows), fx_risk_color))
    rows.append(["", "원자재", "BRN", brent, brent_chg, brent_risk])
    if brent_rc: risk_cells.append((len(rows), brent_rc))
    rows.append(["", "", "WTI", wti, wti_chg, wti_risk])
    if wti_rc: risk_cells.append((len(rows), wti_rc))
    rows.append(["", "", "COPPER", copper, copper_chg, copper_risk])
    if copper_rc: risk_cells.append((len(rows), copper_rc))
    rows.append(["", "", "SILVER", silver_price, silver_chg, ""])
    rows.append(["", "", "원자재 종합 경보", "", "", c_risk_label])
    if c_risk_color: risk_cells.append((len(rows), c_risk_color))
    vix_label = "VIX (선물)" if vix_is_futures else "VIX"
    rows.append(["", "위험 심리", vix_label, vix_price, vix_chg, vix_risk])
    if vix_color: risk_cells.append((len(rows), vix_color))
    rows.append(["", "", "GOLD", gold_price, gold_chg, ""])
    rows.append(["", "", "BITCOIN", btc_price, btc_chg, ""])
    rows.append(["", "", "", "", "", ""])
    rows.append(["", "★ 매크로 종합", "", "", "", macro_label])
    if macro_color: risk_cells.append((len(rows), macro_color))
    rows.append(["", "", "", "", "", ""])

    # 4. 지수 동향 (±2% 이상 시 색상 표시)
    BLUE = {"red": 0, "green": 0, "blue": 0.8}
    RED_TEXT = {"red": 1, "green": 0, "blue": 0}

    for idx_label, idx_chg, idx_name in [
        ("4.지수 동향", dow_chg, "DOW"),
        ("", nasdaq_chg, "NASDAQ"),
        ("", sp500_chg, "S&P500"),
        ("", russell_chg, "RUSSELL2000"),
    ]:
        rows.append([idx_label, "", idx_name, idx_chg, ""])
        try:
            pct = float(idx_chg.replace("%", "").replace("+", ""))
            if pct >= 2:
                color_cells.append((len(rows), "D", BLUE))
            elif pct <= -2:
                color_cells.append((len(rows), "D", RED_TEXT))
        except (ValueError, AttributeError):
            pass
    rows.append(["", "", "", "", ""])

    # 5. 섹터/종목 (±2% 색상 + 특이사항 + 매매 신호)
    SIGN_COLORS = {
        "Long Sign":        {"red": 0, "green": 0, "blue": 0.8},     # 파랑
        "Short Cover Sign": {"red": 0, "green": 0.6, "blue": 0},     # 초록
        "Sell Sign":        {"red": 1, "green": 0.5, "blue": 0},     # 주황
        "Short Sign":       {"red": 1, "green": 0, "blue": 0},       # 빨강
    }

    first_sector = True
    for sector_name, tickers in sector_groups:
        for i, tk_label in enumerate(tickers):
            display_name = "DOW" if tk_label == "DOW_stock" else tk_label
            chg = stock_changes.get(tk_label, "")
            note = stock_notes.get(tk_label, "")
            sign = stock_signs.get(tk_label, "")
            if i == 0:
                rows.append(["5.섹터/종목" if first_sector else "", sector_name, display_name, chg, note, sign])
                first_sector = False
            else:
                rows.append(["", "", display_name, chg, note, sign])
            # ±2% 등락률 색상
            try:
                pct = float(chg.replace("%", "").replace("+", ""))
                if pct >= 2:
                    color_cells.append((len(rows), "D", BLUE))
                elif pct <= -2:
                    color_cells.append((len(rows), "D", RED_TEXT))
            except (ValueError, AttributeError):
                pass
            # 매매 신호 색상
            if sign in SIGN_COLORS:
                color_cells.append((len(rows), "F", SIGN_COLORS[sign]))

    # 특징주 스캔 (S&P 500 중 미추적 + 시총 $165억+ + 특이사항)
    existing_tickers = list(set(us_stocks.values()))
    featured = scan_featured_stocks(existing_tickers)
    if featured:
        first_feat = True
        for ft_ticker, ft_chg, ft_note, ft_sign, ft_sector in featured:
            if first_feat:
                rows.append(["", "특징주", ft_ticker, ft_chg, ft_note, ft_sign, ft_sector])
                first_feat = False
            else:
                rows.append(["", "", ft_ticker, ft_chg, ft_note, ft_sign, ft_sector])
            # ±2% 등락률 색상
            try:
                pct = float(ft_chg.replace("%", "").replace("+", ""))
                if pct >= 2:
                    color_cells.append((len(rows), "D", BLUE))
                elif pct <= -2:
                    color_cells.append((len(rows), "D", RED_TEXT))
            except (ValueError, AttributeError):
                pass
            # 매매 신호 색상
            if ft_sign in SIGN_COLORS:
                color_cells.append((len(rows), "F", SIGN_COLORS[ft_sign]))

    rows.append(["", "", "", "", ""])
    rows.append(["", "", "", "", ""])

    # 6. 종합 요약 (수동)
    rows.append(["6.종합 요약", "시장 흐름", "상승/하락 요인", "", ""])
    rows.append(["", "", "특징주(Long Sign)", "", ""])
    rows.append(["", "", "특징주(랠리)", "", ""])
    rows.append(["", "", "특징주(단기조정)", "", ""])
    rows.append(["", "", "특징주(추세상실)", "", ""])
    rows.append(["", "", "", "", ""])

    # 7. 결론 (수동)
    rows.append(["7.결론", "투자 관점", "위험 시그널", "", ""])
    rows.append(["", "주도 섹터", "핵심 종목", "", ""])

    return rows, risk_cells, color_cells, perf_metrics


def assess_kr_risk(indicator, value):
    """한국 지표별 위험 수준 판단 → (라벨, RGB색상)"""
    if value is None:
        return "", None

    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    thresholds = {
        "KR3Y": (3.0, 3.5, 4.0),
    }

    if indicator in thresholds:
        t1, t2, t3 = thresholds[indicator]
        if value <= t1:
            return "안정", GREEN
        elif value <= t2:
            return "주의", YELLOW
        elif value <= t3:
            return "위험", ORANGE
        else:
            return "고위험", RED

    return "", None


def compute_krw_risk():
    """환율 위험 지수 (KRW-Risk Index)
    3축 모델: 달러/원 수준 30% + 원화 독자 약세(vs DXY) 40% + 엔/원 추세 30%
    100점 환산: 수준×1.0 + 독자약세×1.33 + 엔원×1.0
    """
    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    try:
        data = yf.download(["DX-Y.NYB", "JPY=X", "KRW=X"], period="3mo", progress=False)
        close = data["Close"]

        krw = close["KRW=X"].dropna()
        dxy = close["DX-Y.NYB"].dropna()
        jpy = close["JPY=X"].dropna()

        if len(krw) < 20 or len(dxy) < 20 or len(jpy) < 20:
            return None, None, None, None

        krw_now = krw.iloc[-1]

        # ── 축1: 달러/원 절대 수준 (30%) ──
        # 1,250 이하: 0점 / ~1,300: 10점 / ~1,350: 20점 / 1,350+: 30점
        if krw_now <= 1250:
            level_score = 0
        elif krw_now <= 1300:
            level_score = 10
        elif krw_now <= 1350:
            level_score = 20
        else:
            level_score = 30

        # ── 축2: 원화 독자 약세 (40%) ──
        # 달러/원 20일 변동률 - DXY 20일 변동률
        # DXY가 +2% 오르고 달러/원이 +5% 오르면 → 독자 약세 +3%
        krw_20d = (krw.iloc[-1] / krw.iloc[-20] - 1) * 100
        dxy_20d = (dxy.iloc[-1] / dxy.iloc[-20] - 1) * 100
        divergence = krw_20d - dxy_20d  # 양수 = 원화 독자 약세

        if divergence <= 1:
            div_score = 0
        elif divergence <= 3:
            div_score = 10
        elif divergence <= 5:
            div_score = 20
        else:
            div_score = 30

        # ── 축3: 엔/원 추세 (30%) ──
        # 엔/원(100엔당 원) = USD/KRW ÷ USD/JPY × 100
        # 상승 = 엔 대비 원화 약세 (수출 경쟁력 악화)
        common = krw.index.intersection(jpy.index)
        jpykrw = (krw[common] / jpy[common]) * 100
        if len(jpykrw) >= 20:
            jpykrw_20d = (jpykrw.iloc[-1] / jpykrw.iloc[-20] - 1) * 100
        else:
            jpykrw_20d = 0

        if jpykrw_20d <= 1:
            jpy_score = 0
        elif jpykrw_20d <= 3:
            jpy_score = 10
        elif jpykrw_20d <= 5:
            jpy_score = 20
        else:
            jpy_score = 30

        total = level_score * 1.0 + div_score * 1.33 + jpy_score * 1.0
        total = min(total, 100)

        detail = (
            f"수준={krw_now:,.0f}({level_score}점) "
            f"독자약세={divergence:+.1f}%({div_score}점) "
            f"엔/원={jpykrw_20d:+.1f}%({jpy_score}점)"
        )
        print(f"  KRW-Risk: {detail} → 종합={total:.0f}점")

        if total <= 30:
            label, color = "안정", GREEN
        elif total <= 60:
            label, color = "주의", YELLOW
        elif total <= 85:
            label, color = "위험", ORANGE
        else:
            label, color = "고위험", RED

        # 개별 수준 판단 (F열 표시용)
        if krw_now <= 1250:
            level_label, level_color = "안정", GREEN
        elif krw_now <= 1300:
            level_label, level_color = "주의", YELLOW
        elif krw_now <= 1350:
            level_label, level_color = "위험", ORANGE
        else:
            level_label, level_color = "고위험", RED

        return label, color, level_label, level_color

    except Exception as e:
        print(f"  KRW-Risk 계산 실패: {e}")
        return None, None, None, None


def compute_kr_macro_risk(krw_risk_total, bond_3y_val, wti_val):
    """한국 매크로 종합 위험 지수 (KR-Macro Risk)
    환율(KRW-Risk) 40% + 금리 30% + 유가(WTI) 30%
    100점 환산: 환율×1.33 + 금리×1.0 + 유가×1.0
    """
    GREEN = {"red": 0, "green": 0.6, "blue": 0}
    YELLOW = {"red": 0.8, "green": 0.8, "blue": 0}
    ORANGE = {"red": 1, "green": 0.5, "blue": 0}
    RED = {"red": 1, "green": 0, "blue": 0}

    def label_to_score(label):
        if label is None:
            return 0
        if "안정" in label:
            return 0
        elif "주의" in label:
            return 10
        elif "고위험" in label:
            return 30
        elif "위험" in label:
            return 20
        return 0

    krw_score = label_to_score(krw_risk_total)

    bond_label, bond_color = assess_kr_risk("KR3Y", bond_3y_val)
    bond_score = label_to_score(bond_label)
    bond_display = bond_label

    wti_label, wti_color = assess_risk("WTI", wti_val)
    wti_score = label_to_score(wti_label)
    wti_display = wti_label

    total = krw_score * 1.33 + bond_score * 1.0 + wti_score * 1.0
    total = min(total, 100)

    print(f"  KR-Macro: 환율({krw_score}점) + 금리={bond_3y_val}%({bond_label},{bond_score}점) + WTI=${wti_val}({wti_label},{wti_score}점) → 종합={total:.0f}점")

    if total <= 30:
        macro_label, macro_color = f"안정({total:.0f}점)", GREEN
    elif total <= 60:
        macro_label, macro_color = f"주의({total:.0f}점)", YELLOW
    elif total <= 85:
        macro_label, macro_color = f"위험({total:.0f}점)", ORANGE
    else:
        macro_label, macro_color = f"고위험({total:.0f}점)", RED

    return macro_label, macro_color, bond_display, bond_color, wti_display, wti_color


def scan_kr_featured_stocks(existing_tickers):
    """KOSPI + KOSDAQ 중 추적 외 + 시총 2조원+ + Long Sign 종목 스캔
    (대시보드 '개별 주식 2 신규 Long Sign'과 동일 기준)"""
    from market_common import analyze_trend_signals as common_trend_signals

    print("  한국 특징주 스캔 중 (KOSPI+KOSDAQ, 시총 2조원+, 대시보드와 동일 기준)...")

    candidates = []
    name_map = {}
    existing = set(existing_tickers)
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
                    if mcap_100m < 20000:
                        continue
                    if any(kw in name for kw in [
                        "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "HANARO",
                        "ACE", "KOSEF", "TREX", "RISE", "PLUS",
                        "ETN", "ETF", "인버스", "레버리지", "합성",
                    ]):
                        continue
                    if ticker in existing:
                        continue
                    candidates.append(ticker)
                    name_map[ticker] = name
    except Exception as e:
        print(f"    네이버 금융 크롤링 실패: {e}")
        return []
    candidates = list(dict.fromkeys(candidates))

    print(f"    스캔 대상: {len(candidates)}개 종목")
    if not candidates:
        return []

    sig = common_trend_signals(candidates)
    long_only = {t: info for t, info in sig.items() if info.get("tag") == "long"}
    print(f"    Long Sign 감지: {len(long_only)}개")

    featured = []
    for ticker, info in long_only.items():
        name = name_map.get(ticker, ticker)
        pct = info.get("chg", 0.0)
        featured.append((name, f"{pct:+.2f}%", "Long Sign", "Long Sign"))

    print(f"    특징주 {len(featured)}개")
    return featured


def get_kr_bond_3y():
    """한국 국채 3년물 금리를 investing.com에서 스크래핑"""
    try:
        url = "https://kr.investing.com/rates-bonds/south-korea-3-year-bond-yield"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        resp = req.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        price_el = soup.find(attrs={"data-test": "instrument-price-last"})
        price = price_el.get_text(strip=True) if price_el else ""

        change_el = soup.find(attrs={"data-test": "instrument-price-change-percent"})
        change_pct = change_el.get_text(strip=True).strip("()") if change_el else ""

        print(f"  한국 국채 3년물: {price}% {change_pct}")
        return f"{price}%", change_pct
    except Exception as e:
        print(f"  한국 국채 3년물 스크래핑 실패: {e}")
        return "", ""


# ── 국장 시트 데이터 구성 ──
def build_korea_sheet(target_date):
    print("국장 데이터 수집 중...")

    # 시트가 source of truth — 기존 시트의 국장 탭에서 종목 리스트 읽기 시도
    _sheet_kr_rows = []
    try:
        from sheet_tickers import load_tracking_tickers_from_sheet
        _u, _n = load_tracking_tickers_from_sheet()
        for _label, _tks in _u.items():
            if _label.startswith("🇰🇷"):
                _sec = _label.replace("🇰🇷", "").strip() or "기타"
                for _t in _tks:
                    _name = _n.get(_t, _t)
                    _krx = f"KRX:{_t.replace('.KS', '').replace('.KQ', '')}"
                    _sheet_kr_rows.append((_sec, _name, _t, _krx))
    except Exception:
        pass

    # 한국 종목 리스트 (종목명, 티커, KRX코드)
    kr_stocks_info = [
        # 반도체
        ("반도체", "삼성전자", "005930.KS", "KRX:005930"),
        ("", "SK하이닉스", "000660.KS", "KRX:000660"),
        ("", "한미반도체", "042700.KS", "KRX:042700"),
        # 2차전지
        ("2차전지", "LG에너지솔루션", "373220.KS", "KRX:373220"),
        ("", "에코프로", "086520.KQ", "KRX:086520"),
        ("", "에코프로비엠", "247540.KQ", "KRX:247540"),
        ("", "포스코퓨쳐엠", "003670.KS", "KRX:003670"),
        ("", "삼성SDI", "006400.KS", "KRX:006400"),
        # 자동차
        ("자동차", "현대차", "005380.KS", "KRX:005380"),
        ("", "기아", "000270.KS", "KRX:000270"),
        ("", "현대모비스", "012330.KS", "KRX:012330"),
        # 소재
        ("소재", "POSCO홀딩스", "005490.KS", "KRX:005490"),
        ("", "현대제철", "004020.KS", "KRX:004020"),
        ("", "LG화학", "051910.KS", "KRX:051910"),
        ("", "롯데케미칼", "011170.KS", "KRX:011170"),
        # IT 소프트웨어
        ("IT 소프트웨어", "NAVER", "035420.KS", "KRX:035420"),
        ("", "카카오", "035720.KS", "KRX:035720"),
        ("", "엔씨소프트", "036570.KS", "KRX:036570"),
        # 지주사
        ("지주사", "삼성물산", "028260.KS", "KRX:028260"),
        ("", "한화", "000880.KS", "KRX:000880"),
        ("", "SK", "034730.KS", "KRX:034730"),
        ("", "CJ", "001040.KS", "KRX:001040"),
        ("", "LG", "003550.KS", "KRX:003550"),
        ("", "LS", "006260.KS", "KRX:006260"),
        # 금융
        ("금융", "KB금융", "105560.KS", "KRX:105560"),
        ("", "신한지주", "055550.KS", "KRX:055550"),
        ("", "하나금융", "086790.KS", "KRX:086790"),
        ("", "키움증권", "039490.KS", "KRX:039490"),
        ("", "삼성증권", "016360.KS", "KRX:016360"),
        # IT 테크
        ("IT 테크", "삼성전기", "009150.KS", "KRX:009150"),
        ("", "LG전자", "066570.KS", "KRX:066570"),
        ("", "LG이노텍", "011070.KS", "KRX:011070"),
        # 운송
        ("운송", "HMM", "011200.KS", "KRX:011200"),
        ("", "현대글로비스", "086280.KS", "KRX:086280"),
        ("", "CJ대한통운", "000120.KS", "KRX:000120"),
        # 기계/장비
        ("기계/장비", "두산로보틱스", "454910.KS", "KRX:454910"),
        ("", "두산밥캣", "241560.KS", "KRX:241560"),
        ("", "레인보우로보틱스", "277810.KQ", "KRX:277810"),
        # 유틸리티
        ("유틸리티", "두산에너빌리티", "034020.KS", "KRX:034020"),
        ("", "OCI", "456040.KS", "KRX:456040"),
        ("", "한화솔루션", "009830.KS", "KRX:009830"),
        ("", "LS ELECTRIC", "010120.KS", "KRX:010120"),
        ("", "HD현대일렉트릭", "267260.KS", "KRX:267260"),
        ("", "효성중공업", "298040.KS", "KRX:298040"),
        ("", "한국전력", "015760.KS", "KRX:015760"),
        # 방산
        ("방산", "한화에어로스페이스", "012450.KS", "KRX:012450"),
        ("", "현대로템", "064350.KS", "KRX:064350"),
        ("", "한국항공우주", "047810.KS", "KRX:047810"),
        ("", "한화시스템", "272210.KS", "KRX:272210"),
        ("", "LIG넥스원", "079550.KS", "KRX:079550"),
        # 조선
        ("조선", "HD현대중공업", "329180.KS", "KRX:329180"),
        ("", "HD한국조선해양", "009540.KS", "KRX:009540"),
        ("", "한화오션", "042660.KS", "KRX:042660"),
        ("", "삼성중공업", "010140.KS", "KRX:010140"),
        # 건설
        ("건설", "현대건설", "000720.KS", "KRX:000720"),
        ("", "KCC", "002380.KS", "KRX:002380"),
        # 미디어/엔터
        ("미디어/엔터", "하이브", "352820.KS", "KRX:352820"),
        ("", "에스엠", "041510.KS", "KRX:041510"),
        ("", "JYP Ent.", "035900.KS", "KRX:035900"),
        ("", "CJ ENM", "035760.KS", "KRX:035760"),
        # 화장품
        ("화장품", "아모레퍼시픽", "090430.KS", "KRX:090430"),
        ("", "코스맥스", "044820.KS", "KRX:044820"),
        ("", "한국콜마", "161890.KS", "KRX:161890"),
        # 음식료
        ("음식료", "KT&G", "033780.KS", "KRX:033780"),
        ("", "CJ제일제당", "097950.KS", "KRX:097950"),
        ("", "오리온", "271560.KS", "KRX:271560"),
        ("", "농심", "004370.KS", "KRX:004370"),
        ("", "삼양식품", "003230.KS", "KRX:003230"),
        # 여행/관광
        ("여행/관광", "호텔신라", "008770.KS", "KRX:008770"),
        ("", "파라다이스", "034230.KS", "KRX:034230"),
        ("", "GKL", "114090.KS", "KRX:114090"),
        ("", "하나투어", "039130.KS", "KRX:039130"),
        ("", "모두투어", "080160.KQ", "KRX:080160"),
        ("", "대한항공", "003490.KS", "KRX:003490"),
        # 유통
        ("유통", "신세계", "004170.KS", "KRX:004170"),
        ("", "이마트", "139480.KS", "KRX:139480"),
        ("", "롯데쇼핑", "023530.KS", "KRX:023530"),
        # 제약/바이오
        ("제약/바이오", "삼성바이오로직스", "207940.KS", "KRX:207940"),
        ("", "알테오젠", "196170.KS", "KRX:196170"),
        ("", "유한양행", "000100.KS", "KRX:000100"),
        ("", "SK바이오팜", "326030.KS", "KRX:326030"),
        ("", "셀트리온", "068270.KS", "KRX:068270"),
        # 통신
        ("통신", "SK텔레콤", "017670.KS", "KRX:017670"),
        ("", "KT", "030200.KS", "KRX:030200"),
        ("", "LG유플러스", "032640.KS", "KRX:032640"),
    ]
    if _sheet_kr_rows:
        kr_stocks_info = _sheet_kr_rows
        print(f"  시트에서 한국 추적 종목 {len(kr_stocks_info)}개 로드 (하드코딩 덮어씀)")

    # 한국 종목 일괄 다운로드 (5일 등락률 + 2년 특이사항/매매신호)
    print("  한국 종목 데이터 다운로드 중...")
    kr_tickers = [info[2] for info in kr_stocks_info]
    kr_changes = {}
    kr_signs = {}
    kr_signal_tags = {}  # {ticker: tag} 스냅샷용
    try:
        data_2y = yf.download(kr_tickers, period="2y", progress=False)
        close_2y = data_2y["Close"]
        for sector, name, ticker, krx in kr_stocks_info:
            try:
                col = close_2y[ticker].dropna()
                # 등락률
                if len(col) >= 2:
                    pct = (col.iloc[-1] - col.iloc[-2]) / col.iloc[-2] * 100
                    kr_changes[ticker] = f"{pct:+.2f}%"
                else:
                    kr_changes[ticker] = ""
                    kr_signs[ticker] = ""
                    continue

                # 특이사항 + 매매신호 (50일 이상 데이터 필요)
                if len(col) < 50:
                    kr_signs[ticker] = ""
                    continue

                current = col.iloc[-1]
                one_year = col.tail(252)
                high_52w = one_year.max()
                low_52w = one_year.min()

                notes = []

                # 52주 신고가 / 전고점 돌파 / 신저가
                if current >= high_52w:
                    older = col.iloc[:-252] if len(col) > 252 else None
                    if older is not None and len(older) > 0 and current > older.max():
                        notes.append("전고점 돌파")
                    else:
                        notes.append("52주 신고가")
                elif current <= low_52w:
                    notes.append("52주 신저가")

                # 골든크로스 / 데드크로스
                if len(col) >= 200:
                    ma50 = col.rolling(50).mean()
                    ma200 = col.rolling(200).mean()
                    if len(ma50.dropna()) >= 2 and len(ma200.dropna()) >= 2:
                        prev_diff = ma50.iloc[-2] - ma200.iloc[-2]
                        curr_diff = ma50.iloc[-1] - ma200.iloc[-1]
                        if prev_diff <= 0 and curr_diff > 0:
                            notes.append("골든크로스")
                        elif prev_diff >= 0 and curr_diff < 0:
                            notes.append("데드크로스")

                    # 200일선 이탈 / 회복
                    ma200_today = ma200.iloc[-1]
                    ma200_yesterday = ma200.iloc[-2]
                    prev_close = col.iloc[-2]
                    if prev_close >= ma200_yesterday and current < ma200_today:
                        notes.append("200일선 이탈")
                    elif prev_close < ma200_yesterday and current >= ma200_today:
                        notes.append("200일선 회복")

                # 고점 대비 낙폭
                if not notes or "52주 신저가" in notes:
                    drawdown = (current - high_52w) / high_52w * 100
                    if drawdown <= -30:
                        notes.append("고점대비 -30%")
                    elif drawdown <= -20:
                        notes.append("고점대비 -20%")
                    elif drawdown <= -10:
                        notes.append("고점대비 -10%")

                # 매매 신호 판단 (signal-judge 로직, market_common.classify_signal 사용)
                sig = classify_signal(col)
                if sig:
                    kr_signal_tags[ticker] = sig["tag"]
                    if sig["tag"] in {"long", "sell", "short", "cover"}:
                        kr_signs[ticker] = SIGNAL_LABEL_KR[sig["tag"]]
                    else:
                        kr_signs[ticker] = ""
                else:
                    kr_signs[ticker] = ""

            except Exception:
                kr_changes[ticker] = ""
                kr_signs[ticker] = ""
    except Exception:
        kr_changes = {info[2]: "" for info in kr_stocks_info}
        kr_signs = {info[2]: "" for info in kr_stocks_info}

    # 한국 스냅샷 저장
    try:
        save_snapshot(kr_signal_tags, market="korea")
        print(f"  한국 신호 스냅샷 저장: {len(kr_signal_tags)}개")
    except Exception as e:
        print(f"  한국 스냅샷 저장 실패: {e}")

    # 소수점 2자리 고정 포맷 (국장 지수/환율용)
    def get_price_and_change_2f(ticker_symbol):
        try:
            tk = yf.Ticker(ticker_symbol)
            hist = tk.history(period="5d")
            if len(hist) < 2:
                return "", ""
            prev_close = hist["Close"].iloc[-2]
            last_close = hist["Close"].iloc[-1]
            pct = (last_close - prev_close) / prev_close * 100
            price_str = f"{last_close:,.2f}"
            return price_str, f"{pct:+.2f}%"
        except Exception:
            return "", ""

    # 아시아 지수 (종가 + 등락률)
    asia_indices = [
        ("니케이225", "^N225"),
        ("대만 가권", "^TWII"),
        ("항셍", "^HSI"),
        ("상해종합", "000001.SS"),
    ]
    asia_data = {}
    for name, ticker in asia_indices:
        asia_data[name] = get_price_and_change_2f(ticker)

    # 한국 지수/매크로 (종가 + 등락률)
    kospi_price, kospi_chg = get_price_and_change_2f("^KS11")
    kospi200_price, kospi200_chg = get_price_and_change_2f("^KS200")
    kosdaq_price, kosdaq_chg = get_price_and_change_2f("^KQ11")

    usd_krw_price, usd_krw_chg = get_price_and_change_2f("KRW=X")

    # 국채 3년물 (investing.com 스크래핑)
    kr_bond_3y, kr_bond_3y_chg = get_kr_bond_3y()

    # WTI 유가
    wti_price, wti_chg = get_price_and_change_2f("CL=F")
    wti_val = parse_price(wti_price)

    # 위험 진단
    bond_3y_val = None
    try:
        bond_3y_val = float(kr_bond_3y.replace("%", ""))
    except (ValueError, AttributeError):
        pass

    # 환율 위험 (3축: 수준 + 독자약세 vs DXY + 엔/원 추세)
    krw_risk_label, krw_risk_color, krw_level, krw_rc = compute_krw_risk()

    macro_label, macro_color, bond_risk, bond_rc, wti_risk, wti_rc = compute_kr_macro_risk(krw_risk_label, bond_3y_val, wti_val)

    # 시트 구성
    rows = []
    rows.append(["단계", "주제", "체크포인트", "내용", "비고", "", "", "", "", ""])

    # 1. 시장 뉴스 (수동)
    rows.append(["1.시장 뉴스", "정책", "", "", "", "", "", "", "", ""])
    rows.append(["", "개별기업", "", "", "", "", "", "", "", ""])
    rows.append(["", "", "", "", "", "", "", "", "", ""])

    # 2. 경제지표 (investing.com - 한국 2성급 이상)
    kr_econ = build_economic_indicators(target_date, country="KR")
    if kr_econ:
        first = True
        for name, actual_dir, expected_prev in kr_econ:
            rows.append([
                "2.경제지표" if first else "",
                "",
                name,
                actual_dir,
                expected_prev,
                "", "", "", "", "",
            ])
            first = False
    else:
        rows.append(["2.경제지표", "", "(당일 주요 지표 발표 없음)", "", "", "", "", "", "", ""])
    rows.append(["", "", "", "", "", "", "", "", "", ""])

    # 3. 아시아 증시 (종가 + 등락률)
    asia_labels = [
        ("3.아시아 증시", "일본", "니케이225"),
        ("", "대만", "대만 가권"),
        ("", "홍콩", "항셍"),
        ("", "중국", "상해종합"),
    ]
    for step, country, name in asia_labels:
        price, chg = asia_data.get(name, ("", ""))
        rows.append([step, country, name, price, chg, "", "", "", "", ""])
    rows.append(["", "", "", "", "", "", "", "", "", ""])

    # 4. 지수 및 Macro (종가 + 등락률 + 위험 진단)
    risk_cells = []

    rows.append(["4.지수 및 Macro", "지수", "KOSPI", kospi_price, kospi_chg, "", "", "", "", ""])
    rows.append(["", "", "KOSPI200", kospi200_price, kospi200_chg, "", "", "", "", ""])
    rows.append(["", "", "KOSDAQ", kosdaq_price, kosdaq_chg, "", "", "", "", ""])
    rows.append(["", "환율", "원/달러", usd_krw_price, usd_krw_chg, "", "", "", "", ""])
    rows.append(["", "", "환율 종합 경보", "", "", krw_risk_label if krw_risk_label else "", "", "", "", ""])
    if krw_risk_color:
        risk_cells.append((len(rows), krw_risk_color))
    rows.append(["", "금리", "국채 3년물", kr_bond_3y, kr_bond_3y_chg, bond_risk, "", "", "", ""])
    if bond_rc:
        risk_cells.append((len(rows), bond_rc))
    rows.append(["", "유가", "WTI", wti_price, wti_chg, wti_risk, "", "", "", ""])
    if wti_rc:
        risk_cells.append((len(rows), wti_rc))
    rows.append(["", "", "", "", "", "", "", "", "", ""])
    rows.append(["", "★ KR-Macro 종합", "", "", "", macro_label, "", "", "", ""])
    if macro_color:
        risk_cells.append((len(rows), macro_color))
    rows.append(["", "", "", "", "", "", "", "", "", ""])

    # 6. 섹터/종목 (매매신호 색상)
    SIGN_COLORS = {
        "Long Sign":        {"red": 0, "green": 0, "blue": 0.8},     # 파랑
        "Short Cover Sign": {"red": 0, "green": 0.6, "blue": 0},     # 초록
        "Sell Sign":        {"red": 1, "green": 0.5, "blue": 0},     # 주황
        "Short Sign":       {"red": 1, "green": 0, "blue": 0},       # 빨강
    }
    BLUE = {"red": 0, "green": 0, "blue": 0.8}
    RED_TEXT = {"red": 1, "green": 0, "blue": 0}
    color_cells = []

    first_sector = True
    for sector, name, ticker, krx in kr_stocks_info:
        chg = kr_changes.get(ticker, "")
        sign = kr_signs.get(ticker, "")
        row = [
            "5.섹터/종목" if first_sector and sector else "",
            sector,
            name,
            chg,
            sign,
            ticker,  # col F: 티커 (대시보드가 source of truth로 읽어감)
            "",
            "",
            "",
            "",
        ]
        if first_sector and sector:
            first_sector = False
        rows.append(row)
        # ±2% 등락률 색상 (D열)
        try:
            pct = float(chg.replace("%", "").replace("+", ""))
            if pct >= 2:
                color_cells.append((len(rows), "D", BLUE))
            elif pct <= -2:
                color_cells.append((len(rows), "D", RED_TEXT))
        except (ValueError, AttributeError):
            pass
        # 매매 신호 색상 (E열)
        if sign in SIGN_COLORS:
            color_cells.append((len(rows), "E", SIGN_COLORS[sign]))

    # 특징주 스캔 (시총 2조+ 미추적 종목 중 Long Sign)
    existing_kr_tickers = [info[2] for info in kr_stocks_info]
    kr_featured = scan_kr_featured_stocks(existing_kr_tickers)
    if kr_featured:
        first_feat = True
        for ft_name, ft_chg, ft_note, ft_sign in kr_featured:
            rows.append([
                "",
                "특징주" if first_feat else "",
                ft_name,
                ft_chg,
                ft_sign,
                ft_note,
                "", "", "", "",
            ])
            first_feat = False
            # 등락률 색상
            try:
                pct = float(ft_chg.replace("%", "").replace("+", ""))
                if pct >= 2:
                    color_cells.append((len(rows), "D", BLUE))
                elif pct <= -2:
                    color_cells.append((len(rows), "D", RED_TEXT))
            except (ValueError, AttributeError):
                pass
            # Long Sign 색상
            if ft_sign in SIGN_COLORS:
                color_cells.append((len(rows), "E", SIGN_COLORS[ft_sign]))

    rows.append(["", "", "", "", "", "", "", "", "", ""])
    rows.append(["", "", "", "", "", "", "", "", "", ""])

    # 6. 종합 요약 (수동)
    rows.append(["6.종합 요약", "시장 흐름", "상승/하락 요인", "", "", "", "", "", "", ""])
    rows.append(["", "", "특징주(Long Sign)", "", "", "", "", "", "", ""])
    rows.append(["", "", "특징주(랠리)", "", "", "", "", "", "", ""])
    rows.append(["", "", "특징주(단기조정)", "", "", "", "", "", "", ""])
    rows.append(["", "", "특징주(추세상실)", "", "", "", "", "", "", ""])
    rows.append(["", "", "", "", "", "", "", "", "", ""])

    # 7. 결론 (수동)
    rows.append(["7.결론", "투자 관점", "위험 시그널", "", "", "", "", "", "", ""])
    rows.append(["", "주도 섹터", "핵심 종목", "", "", "", "", "", "", ""])

    return rows, risk_cells, color_cells


# ── 메인 실행 ──
PERF_SHEET_NAME = "스카우터_성과자료"
PERF_HEADERS = ["날짜", "T-RISK", "FX-RISK", "C-RISK", "VIX점수", "매크로종합",
                "VIX값", "DXY", "US10Y", "Oil평균", "S&P500 종가", "S&P500 일변동%"]
PERF_KEYS = ["date", "t_risk", "fx_risk", "c_risk", "vix_score", "macro_total",
             "vix", "dxy", "us_10y", "oil_avg", "sp500_close", "sp500_chg_pct"]


def append_performance_log(perf):
    """스카우터_성과자료 시트에 하루치 메트릭 누적 기록 (중복 날짜는 덮어쓰기)"""
    try:
        query = (f"name='{PERF_SHEET_NAME}' and '{FOLDER_ID}' in parents "
                 "and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false")
        results = drive.files().list(q=query, fields="files(id, name)").execute()
        files = results.get("files", [])
        if files:
            sh = gc.open_by_key(files[0]["id"])
        else:
            print(f"  {PERF_SHEET_NAME} 시트 새로 생성")
            sh = gc.create(PERF_SHEET_NAME, folder_id=FOLDER_ID)
        ws = sh.sheet1
        existing = ws.get_all_values()
        # 헤더 확인·기록
        if not existing or existing[0] != PERF_HEADERS:
            if not existing:
                ws.update(range_name="A1", values=[PERF_HEADERS])
                existing = [PERF_HEADERS]
            else:
                ws.update(range_name="A1", values=[PERF_HEADERS])
                existing[0] = PERF_HEADERS

        row = [perf.get(k, "") for k in PERF_KEYS]
        # 날짜 중복 확인
        date_col = [r[0] for r in existing[1:]] if len(existing) > 1 else []
        if perf["date"] in date_col:
            idx = date_col.index(perf["date"]) + 2  # header + 1-based
            ws.update(range_name=f"A{idx}", values=[row])
            print(f"  {PERF_SHEET_NAME} 기록 갱신: {perf['date']} (행 {idx})")
        else:
            ws.append_row(row)
            print(f"  {PERF_SHEET_NAME} 기록 추가: {perf['date']}")
    except Exception as e:
        print(f"  {PERF_SHEET_NAME} 기록 실패: {e}")


def main():
    # 명령줄 인자 처리
    global_only = "--global-only" in sys.argv
    korea_only = "--korea-only" in sys.argv

    # --date YYMMDD 옵션: 특정 날짜 시트에 작성
    today = datetime.now()
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        date_str = sys.argv[idx + 1]
        today = datetime.strptime(date_str, "%y%m%d")
    else:
        date_str = today.strftime("%y%m%d")
    sheet_name = f"증시 리뷰_{date_str}"

    mode = "글로벌만" if global_only else ("국장만" if korea_only else "글로벌+국장")
    print(f"=== {sheet_name} 생성 시작 ({mode}) ===\n")

    # 전일 날짜 (미국 증시 기준 — 글로벌 시트용)
    yesterday = today - timedelta(days=1)
    # 주말이면 금요일로
    if yesterday.weekday() == 6:  # 일요일
        yesterday = yesterday - timedelta(days=2)
    elif yesterday.weekday() == 5:  # 토요일
        yesterday = yesterday - timedelta(days=1)

    # 글로벌 시트: 전일 휴장일 체크
    if not korea_only:
        check = yf.download("^GSPC", start=yesterday.strftime("%Y-%m-%d"),
                            end=(yesterday + timedelta(days=1)).strftime("%Y-%m-%d"),
                            progress=False)
        if check.empty:
            print(f"전일({yesterday.strftime('%Y-%m-%d')}) 미국 증시 휴장 → 글로벌 스킵")
            if global_only:
                return
            # 글로벌+국장 모드에서 미국 휴장이면 국장만 진행
            global_only = False
            korea_only = True
            mode = "국장만 (미국 휴장)"
            print(f"  → {mode}으로 전환\n")

    # 데이터 수집
    global_data = risk_cells = color_cells = None
    korea_data = kr_risk_cells = kr_color_cells = None
    perf_metrics = None

    if not korea_only:
        global_data, risk_cells, color_cells, perf_metrics = build_global_sheet(yesterday)
    if not global_only:
        korea_data, kr_risk_cells, kr_color_cells = build_korea_sheet(today)

    # 기존 스프레드시트 찾기 또는 새로 생성
    sh = None
    try:
        query = f"name='{sheet_name}' and '{FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        results = drive.files().list(q=query, fields="files(id, name)").execute()
        files = results.get("files", [])
        if files:
            print(f"\n기존 시트 '{sheet_name}' 발견 → 덮어쓰기")
            sh = gc.open_by_key(files[0]["id"])
        else:
            print(f"\n구글 시트 '{sheet_name}' 새로 생성 중...")
            sh = gc.create(sheet_name, folder_id=FOLDER_ID)
    except Exception:
        print(f"\n구글 시트 '{sheet_name}' 새로 생성 중...")
        sh = gc.create(sheet_name, folder_id=FOLDER_ID)

    existing_titles = [ws.title for ws in sh.worksheets()]

    # 글로벌 시트 작성
    if not korea_only and global_data is not None:
        if "글로벌" in existing_titles:
            ws_global = sh.worksheet("글로벌")
            ws_global.clear()
            ws_global.format("A1:Z500", {
                "textFormat": {
                    "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                    "bold": False,
                }
            })
        else:
            ws_global = sh.sheet1
            ws_global.update_title("글로벌")
        ws_global.update(range_name="A1", values=global_data)

        fmt_list = []
        for row_num, color in risk_cells:
            fmt_list.append({
                "range": f"F{row_num}",
                "format": {"textFormat": {"foregroundColor": color, "bold": True}},
            })
        for row_num, col, color in color_cells:
            fmt_list.append({
                "range": f"{col}{row_num}",
                "format": {"textFormat": {"foregroundColor": color, "bold": True}},
            })
        if fmt_list:
            ws_global.batch_format(fmt_list)
        print("  글로벌 시트 작성 완료")

    # 국장 시트 작성
    if not global_only and korea_data is not None:
        if not korea_only:
            time.sleep(1)  # API rate limit 방지
        # 기존 워크시트 목록 갱신 (글로벌 시트에서 이름 변경했을 수 있으므로)
        existing_titles = [ws.title for ws in sh.worksheets()]

        if "국장" in existing_titles:
            ws_korea = sh.worksheet("국장")
            ws_korea.clear()
            ws_korea.format("A1:Z500", {
                "textFormat": {
                    "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                    "bold": False,
                }
            })
        else:
            ws_korea = sh.add_worksheet(title="국장", rows=200, cols=27)
        ws_korea.update(range_name="A1", values=korea_data)

        # 색상 적용 (위험 진단 F열 + 등락률 ±2% + 매매신호)
        kr_fmt_list = []
        for row_num, color in kr_risk_cells:
            kr_fmt_list.append({
                "range": f"F{row_num}",
                "format": {"textFormat": {"foregroundColor": color, "bold": True}},
            })
        for row_num, col, color in kr_color_cells:
            kr_fmt_list.append({
                "range": f"{col}{row_num}",
                "format": {"textFormat": {"foregroundColor": color, "bold": True}},
            })
        if kr_fmt_list:
            ws_korea.batch_format(kr_fmt_list)
        print("  국장 시트 작성 완료")

    # 성과 추적 시트에 하루치 매크로 메트릭 + S&P500 종가 누적
    if perf_metrics:
        print(f"\n성과 추적 기록 중...")
        append_performance_log(perf_metrics)

    print(f"\n=== 완료! ===")
    print(f"파일명: {sheet_name}")
    print(f"URL: {sh.url}")


if __name__ == "__main__":
    main()
