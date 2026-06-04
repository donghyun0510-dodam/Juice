"""
Long Sign 풀스캔 코어 (Streamlit 비의존, GitHub Actions 헤드리스 실행용).

기존에 market_dashboard.py 안에서 앱 프로세스로 돌리던 S&P500 / 한국 전종목
Long sign 스캔을 분리한 모듈. Streamlit Cloud 1GB 컨테이너 OOM의 주범이었던
대규모 yfinance 일괄 다운로드 + 종목별 DataFrame 캐시 누적을 제거하기 위해:

  - 미국: yf.download 를 CHUNK_SIZE 단위로 쪼개 받고, 청크마다 신호만 추출 후
          DataFrame 을 즉시 폐기(gc.collect)해 피크 메모리를 일정하게 유지.
  - 한국: 종목별 FinanceDataReader 일봉을 한 종목씩 받아 분류 후 즉시 폐기.

신호 분류 로직(classify_close)은 종전 대시보드의 inline 판정과 동일.
대시보드는 이제 이 스캔을 직접 수행하지 않고, 산출된 JSON 캐시만 읽는다.
"""

import gc
import re

import requests as req


CHUNK_SIZE = 40  # yf.download 1회당 미국 종목 수 (피크 메모리 제어)


def is_kr_ticker(t: str) -> bool:
    return t.endswith(".KS") or t.endswith(".KQ")


def classify_close(close):
    """단일 종목 Close 시리즈로 추세 신호 분류.
    종전 market_dashboard.analyze_trend_signals 의 inline 판정과 동일한 규칙.
    데이터 부족(200일 미만)이면 None.
    반환: {"tag","last","chg","ma50","ma200"} 또는 None
    """
    if close is None:
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

    # === Long Sign: 3가지 조건 (52주 신고가 괴리 3% 게이트 + 모멘텀 요구) ===
    high_52w = close.max()
    near_52w_high = last >= high_52w * 0.97

    strong_momentum = False
    if len(close) >= 11:
        close_5d = last / close.iloc[-6] - 1
        close_10d = last / close.iloc[-11] - 1
        strong_momentum = close_5d >= 0.05 and close_10d >= 0.10

    cond1_break_52w = last >= high_52w * 1.001

    cond2_alignment = False
    if len(close) >= 120:
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        ma120 = close.rolling(120).mean()
        aligned = ma20.iloc[-1] > ma60.iloc[-1] > ma120.iloc[-1]
        ma20_slope_up = ma20.iloc[-1] > ma20.iloc[-5] if len(ma20.dropna()) >= 5 else False
        if aligned and ma20_slope_up and near_52w_high and strong_momentum:
            cond2_alignment = True

    cond3_box_break = False
    if len(close) >= 21:
        box_high = close.iloc[-21:-1].max()
        if last > box_high * 1.01 and near_52w_high and strong_momentum:
            cond3_box_break = True

    long_sign_new = cond1_break_52w or cond2_alignment or cond3_box_break
    # 당일 상쇄(intraday 역전) 필터: long sign 후 당일 수익률이 음(-)이면 취소
    if long_sign_new and prev > 0 and (last / prev - 1) < 0:
        long_sign_new = False

    # === Sell Sign (고점권에서 꺾임) ===
    sell_sign_new = False
    if len(close) >= 30:
        prior = close.iloc[-30:-5]
        recent = close.iloc[-5:]
        if len(prior) >= 10 and len(recent) >= 3 and prior.iloc[0] > 0 and recent.iloc[0] > 0:
            prior_slope = (prior.iloc[-1] - prior.iloc[0]) / prior.iloc[0] / len(prior)
            recent_slope = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0] / len(recent)
            at_high_zone = prior.max() >= high_52w * 0.95
            broke_5d_ago = last < close.iloc[-6] * 0.97 if len(close) >= 6 else False
            if (prior_slope > 0 and recent_slope < 0
                    and abs(recent_slope) > prior_slope
                    and at_high_zone and broke_5d_ago):
                sell_sign_new = True

    # === Short Sign ===
    short_sign_new = False
    if last <= close.min() * 1.001:
        short_sign_new = True
    if not short_sign_new and len(close) >= 30:
        prior_s = close.iloc[-30:-5]
        recent_s = close.iloc[-5:]
        if len(prior_s) >= 10 and len(recent_s) >= 3 and prior_s.iloc[0] > 0 and recent_s.iloc[0] > 0:
            ps = (prior_s.iloc[-1] - prior_s.iloc[0]) / prior_s.iloc[0] / len(prior_s)
            rs = (recent_s.iloc[-1] - recent_s.iloc[0]) / recent_s.iloc[0] / len(recent_s)
            rec_cum = (recent_s.iloc[-1] / recent_s.iloc[0] - 1) * 100
            if rs < 0 and rec_cum <= -4 and abs(rs) > abs(ps) and ps <= 0:
                short_sign_new = True

    # === Short Cover Sign (장기 하락 반전) ===
    cover_sign_new = False
    if len(close) >= 40:
        prior_c = close.iloc[-30:-5]
        recent_c = close.iloc[-5:]
        if len(prior_c) >= 10 and len(recent_c) >= 3 and prior_c.iloc[0] > 0 and recent_c.iloc[0] > 0:
            pc_slope = (prior_c.iloc[-1] - prior_c.iloc[0]) / prior_c.iloc[0] / len(prior_c)
            rc_slope = (recent_c.iloc[-1] - recent_c.iloc[0]) / recent_c.iloc[0] / len(recent_c)
            long_term_downtrend = last < ma200_last and last < high_52w * 0.85
            recent_low = close.iloc[-15:].min()
            prev_low = close.iloc[-40:-15].min()
            strict_higher_low = recent_low > prev_low
            strong_bounce_from_low = recent_low > 0 and (last / recent_low - 1) >= 0.10
            low_pattern_ok = strict_higher_low or strong_bounce_from_low
            if (pc_slope < 0 and rc_slope > 0
                    and abs(rc_slope) > abs(pc_slope)
                    and long_term_downtrend and low_pattern_ok):
                cover_sign_new = True

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

    return {
        "tag": tag,
        "last": float(last),
        "chg": float((last / prev - 1) * 100),
        "ma50": float(ma50_last),
        "ma200": float(ma200_last),
    }


def _fetch_kr_close(ticker: str):
    """한국 종목 일봉 Close 시리즈 (FinanceDataReader, Naver 경유)."""
    try:
        import FinanceDataReader as fdr
        from datetime import datetime as _dt, timedelta as _td
        code = ticker.split(".")[0]
        start = (_dt.now() - _td(days=400)).strftime("%Y-%m-%d")
        df = fdr.DataReader(code, start=start)
        if df is None or len(df) == 0:
            return None
        return df["Close"].dropna()
    except Exception as e:
        print(f"[FDR] {ticker} fail: {e}", flush=True)
        return None


def analyze_trend_signals(all_tickers):
    """다수 종목 추세 신호 일괄 분석 (메모리 절약형, 라이브 가격 미합성).

    미국: yf.download 를 CHUNK_SIZE 단위로 쪼개 받고 청크마다 즉시 폐기.
    한국(.KS/.KQ): 종목별 FinanceDataReader 일봉을 한 종목씩 받아 분류 후 폐기.
    """
    import yfinance as yf

    kr_tickers = [t for t in all_tickers if is_kr_ticker(t)]
    us_tickers = [t for t in all_tickers if not is_kr_ticker(t)]

    results = {}

    # ── 미국: 청크 단위 다운로드 → 분류 → 폐기 ──
    for i in range(0, len(us_tickers), CHUNK_SIZE):
        chunk = us_tickers[i:i + CHUNK_SIZE]
        try:
            data = yf.download(chunk, period="1y", auto_adjust=True,
                               progress=False, group_by="ticker")
        except Exception as e:
            print(f"[us chunk {i//CHUNK_SIZE}] download fail: {e}", flush=True)
            continue
        for t in chunk:
            try:
                close = data["Close"].dropna() if len(chunk) == 1 else data[t]["Close"].dropna()
                r = classify_close(close)
                if r is not None:
                    results[t] = r
            except Exception:
                continue
        del data
        gc.collect()

    # ── 한국: 종목별 순차 처리 ──
    for t in kr_tickers:
        try:
            close = _fetch_kr_close(t)
            r = classify_close(close)
            if r is not None:
                results[t] = r
        except Exception:
            continue
        del close
    if kr_tickers:
        gc.collect()

    return results


# ══════════════════════════════════════════
# S&P500 스캔
# ══════════════════════════════════════════

def scan_sp500(exclude_set):
    """S&P500 중 추적 종목(exclude_set) 외에서 Long sign 발생 종목 스캔.
    반환: (long_only: {ticker: info}, sector_map: {ticker: sector}, err)"""
    try:
        import pandas as pd
        from io import StringIO
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = req.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                    headers=headers, timeout=10)
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        sp500_df = tables[0]
        sp500_df["Ticker"] = sp500_df["Symbol"].str.replace(".", "-", regex=False)
        candidates = [t for t in sp500_df["Ticker"].tolist() if t not in exclude_set]
        sector_map = dict(zip(sp500_df["Ticker"], sp500_df["GICS Sector"]))
    except Exception as e:
        return {}, {}, str(e)
    sig = analyze_trend_signals(candidates)
    long_only = {t: info for t, info in sig.items() if info["tag"] == "long"}
    return long_only, sector_map, None


# ══════════════════════════════════════════
# 한국 (KOSPI + KOSDAQ, 시총 3조원+) 스캔
# ══════════════════════════════════════════

def scan_kr(exclude_set):
    """KOSPI/KOSDAQ 시총 3조원+ 중 추적 종목 외에서 Long sign 발생 종목 스캔.
    반환: (long_only: {ticker: info}, name_map: {ticker: name}, err)"""
    import yfinance as yf
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
    sig = analyze_trend_signals(candidates)
    long_only = {t: info for t, info in sig.items() if info["tag"] == "long"}
    # 시총 정보 주입 (fast_info 로 정확한 KRW)
    for t, info in long_only.items():
        try:
            info["mcap"] = yf.Ticker(t).fast_info.get("market_cap") or 0
        except Exception:
            info["mcap"] = 0
    name_out = {t: name_map.get(t, t) for t in long_only}
    return long_only, name_out, None
