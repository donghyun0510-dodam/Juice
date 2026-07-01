# -*- coding: utf-8 -*-
"""네이버 금융 매크로/위험심리 지표 단일 수집 모듈.

매크로 동향(금리·환율·원자재)과 위험 심리(VIX·GOLD)를 네이버 모바일 JSON API
(api.stock.naver.com) + 일부 FX는 데스크톱 worldDailyQuote에서 가져온다.
- 비공식 API이므로 항상 1차 소스로만 쓰고, 호출부는 실패 시 yfinance/CNBC/investing로 폴백.
- 비트코인(USD)은 네이버에 원화(업비트)만 있어 제외 — 호출부에서 yfinance(BTC-USD) 유지.

반환 규약: naver_quote(key) -> (val: float|None, chg_str: str, ratio: float|None)
  val=현재가, chg_str="+1.27%" 형식, ratio=전일종가 대비 일변동률(float, 부호 포함).
실패 시 (None, "", None). 호출부는 val is None이면 폴백한다.

지표 신선도: 모바일 JSON은 전일 종가가 정상 반영(데스크톱 worldDailyQuote 유가는
며칠 지연 버그가 있어 유가/금속은 모바일만 사용). FX 크로스(USD/JPY·USD/CNY)는
모바일 목록에 없어 데스크톱 worldDailyQuote 사용(이쪽은 FX가 신선함).
"""
import re
import time
import threading
from datetime import datetime

import requests
from bs4 import BeautifulSoup

_API = "https://api.stock.naver.com"
_DESK = "https://finance.naver.com/marketindex/worldDailyQuote.naver"

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"),
    "Referer": "https://m.stock.naver.com/",
})

_TTL = 60.0  # 프로세스 내 캐시(초): 한 실행에서 같은 URL 반복 호출 방지
_CACHE = {}  # url -> (ts, payload)
_LOCK = threading.Lock()

# 논리 key -> (전략, 코드)
_DISPATCH = {
    "2Y":     ("bond",   "US2YT=RR"),
    "10Y":    ("bond",   "US10YT=RR"),
    "30Y":    ("bond",   "US30YT=RR"),
    "DXY":    ("fxlist", ".DXY"),
    "EURUSD": ("fxlist", "EURUSD"),
    "USDJPY": ("fxdesk", "FX_USDJPY"),
    "USDCNY": ("fxdesk", "FX_USDCNY"),
    "USDKRW": ("fxlist", "FX_USDKRW"),
    "WTI":    ("energy", "CLcv1"),
    "BRENT":  ("energy", "LCOcv1"),
    "GOLD":   ("metals", "GCcv1"),
    "COPPER": ("metals", "HGcv1"),   # $/lb (호출부에서 필요시 ×2204.62 → $/톤)
    "SILVER": ("metals", "SIcv1"),
    "VIX":    ("index",  ".VIX"),
}

# yfinance 티커 -> 논리 key (호출부 매핑용). BTC-USD는 의도적으로 제외.
TICKER_KEY = {
    "DX-Y.NYB": "DXY",
    "EURUSD=X": "EURUSD",
    "JPY=X":    "USDJPY",
    "CNY=X":    "USDCNY",
    "^VIX":     "VIX",
    "CL=F":     "WTI",
    "BZ=F":     "BRENT",
    "GC=F":     "GOLD",
    "HG=F":     "COPPER",
    "SI=F":     "SILVER",
    "KRW=X":    "USDKRW",
}

# 지수 코드: 해외(.RIC)는 api.stock /index, 국내(KOSPI 등)는 m.stock /api/index.
# 국장 시트용 — 아시아 지수(.N225/.TWII/.HSI/.SSEC), 국내(KOSPI/KOSDAQ/KPI200).
_M_API = "https://m.stock.naver.com/api"


def _get_json(url):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(url)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    try:
        r = _SESSION.get(url, timeout=12)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None
    with _LOCK:
        _CACHE[url] = (now, data)
    return data


def _num(x):
    """'1,538.00' / 4.232 / None -> float|None."""
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _from_item(item, guard_session=False):
    """네이버 시세 dict -> (val, chg_str, ratio).

    guard_session: 선물(원자재)은 정비 휴장(marketStatus=PREOPEN) 동안 종가는 유지되나
    fluctuationsRatio가 0.00으로 리셋된다(openPrice=0, type=UNCHANGED). 이 구간엔
    등락률을 신뢰할 수 없으므로 None을 반환해 호출부가 yfinance로 폴백하게 한다.
    (daily-global 실행 시각 17:37 ET이 NYMEX/COMEX 정비 휴장 17:00~18:00 ET과 겹침.)
    """
    if not isinstance(item, dict):
        return None, "", None
    val = _num(item.get("closePrice"))
    if val is None:
        return None, "", None
    if guard_session:
        ms = item.get("marketStatus")
        ftype = item.get("fluctuationsType")
        ftype = ftype.get("name") if isinstance(ftype, dict) else ftype
        if ms == "PREOPEN" or (ftype == "UNCHANGED" and item.get("openPrice") in (0, 0.0, None)):
            return None, "", None
    ratio = _num(item.get("fluctuationsRatio"))
    chg = f"{ratio:+.2f}%" if ratio is not None else ""
    return val, chg, ratio


def _find_in_lists(payload, code):
    """list 또는 {normalList,majorList}에서 reutersCode 매칭 항목 찾기."""
    buckets = []
    if isinstance(payload, list):
        buckets = [payload]
    elif isinstance(payload, dict):
        buckets = [v for v in payload.values() if isinstance(v, list)]
    for arr in buckets:
        for it in arr:
            if isinstance(it, dict) and it.get("reutersCode") == code:
                return it
    return None


def _prices_latest(category, code):
    """일별 시세(/prices) 최신 바 -> (val, chg_str, ratio).

    선물 정비 휴장(PREOPEN)으로 live 등락률이 0으로 리셋됐을 때 사용. 일별 바는
    settled 종가 + 전일 대비 등락률을 정상 보유하므로 '전일 종가' 기준으로 가져온다.
    """
    payload = _get_json(f"{_API}/marketindex/{category}/{code}/prices")
    if isinstance(payload, list) and payload:
        return _from_item(payload[0])  # guard 불필요 — 일별 바는 등락률 정상
    return None, "", None


def _fetch_desk_fx(code):
    """데스크톱 worldDailyQuote(HTML, euc-kr)에서 FX 크로스 최신/직전 종가."""
    try:
        url = f"{_DESK}?marketindexCd={code}&fdtc=4"
        r = _SESSION.get(url, timeout=12)
        r.encoding = "euc-kr"
        s = BeautifulSoup(r.text, "html.parser")
        tds = [t.get_text(strip=True) for t in s.select("td")]
        # 형식: [날짜, 종가, 전일대비, 등락률, 전일날짜, 전일종가, ...]
        if len(tds) >= 4:
            val = _num(tds[1])
            ratio = None
            m = re.search(r"([+-]?\d+\.?\d*)", tds[3])
            if m:
                ratio = float(m.group(1))
            if val is not None:
                chg = f"{ratio:+.2f}%" if ratio is not None else ""
                return val, chg, ratio
    except Exception:
        pass
    return None, "", None


def naver_quote(key):
    """논리 key('2Y','DXY','WTI','VIX'...) -> (val, chg_str, ratio). 실패 시 (None,'',None)."""
    spec = _DISPATCH.get(key)
    if not spec:
        return None, "", None
    strat, code = spec
    try:
        if strat == "bond":
            # 미 국채 실시간 호가는 미 마감(17:05 ET) 직후 closePrice가 야간 세션 값으로
            # 넘어가지만 fluctuationsRatio의 '전일 종가' 기준 롤오버가 지연돼, 등락률이
            # 두 세션분(전일 정산→야간 라이브)을 잡는다. daily-global(21:37 ET)이 이 구간과
            # 겹침. 실시간 등락률의 내재 base가 최신 정산 종가(일별 바)와 어긋나면 stale로
            # 보고 일별 정산 바를 신뢰한다(원자재 PREOPEN 가드의 채권 버전).
            live = _from_item(_get_json(f"{_API}/marketindex/bond/{code}"))
            daily = _prices_latest("bond", code)  # 최신 정산 일별 바(전일대비 정상)
            if (live[0] is not None and live[2] is not None
                    and daily[0] not in (None, 0)):
                implied_base = live[0] / (1.0 + live[2] / 100.0)
                if abs(implied_base - daily[0]) / daily[0] > 0.001:
                    return daily  # 롤오버 지연 감지 → 일별 정산 종가로 대체
            return live if live[0] is not None else daily
        if strat == "index":
            return _from_item(_get_json(f"{_API}/index/{code}/basic"))
        if strat == "metals":
            live = _from_item(_get_json(f"{_API}/marketindex/metals/{code}"), guard_session=True)
            if live[0] is not None:
                return live
            return _prices_latest("metals", code)  # PREOPEN → 전일 종가 일별 바
        if strat == "energy":
            live = _from_item(_find_in_lists(_get_json(f"{_API}/marketindex/energy"), code),
                              guard_session=True)
            if live[0] is not None:
                return live
            return _prices_latest("energy", code)  # PREOPEN → 전일 종가 일별 바
        if strat == "fxlist":
            return _from_item(_find_in_lists(_get_json(f"{_API}/marketindex/exchange"), code))
        if strat == "fxdesk":
            return _fetch_desk_fx(code)
    except Exception:
        pass
    return None, "", None


def _fmt_price(val):
    """get_price_and_change 표시 포맷과 동일."""
    if val >= 1000:
        return f"{val:,.0f}"
    if val >= 100:
        return f"{val:.2f}"
    return f"{val:.3f}"


def naver_quote_fmt(key):
    """(price_str, chg_str, val) — get_price_and_change/_yf_commodity 호환 포맷."""
    val, chg, _ = naver_quote(key)
    if val is None:
        return "", "", None
    return _fmt_price(val), chg, val


def naver_quote_for_ticker(ticker):
    """yfinance 티커로 조회 -> (val, chg_str, ratio). 매핑 없으면 (None,'',None)→폴백."""
    key = TICKER_KEY.get(ticker)
    if not key:
        return None, "", None
    return naver_quote(key)


def naver_quote_fmt_for_ticker(ticker):
    """yfinance 티커로 조회 -> (price_str, chg_str, val). get_price_and_change 호환."""
    key = TICKER_KEY.get(ticker)
    if not key:
        return "", "", None
    return naver_quote_fmt(key)


def naver_index(code):
    """지수 종가/등락률 -> (val, chg_str, ratio). 실패 시 (None,'',None).

    code: 해외 '.N225'/'.TWII'/'.HSI'/'.SSEC'(api.stock /index),
          국내 'KOSPI'/'KOSDAQ'/'KPI200'(m.stock /api/index).
    """
    if code.startswith("."):
        url = f"{_API}/index/{code}/basic"
    else:
        url = f"{_M_API}/index/{code}/basic"
    return _from_item(_get_json(url))


def naver_kr_stock(code):
    """KR 종목(6자리 코드) 현재 종가/등락률 -> (val, chg_str, ratio).

    yfinance/FDR가 KRX 당일 일봉을 늦게 게시하는 종목(에스엠·JYP 등)의 전일종가
    오긁힘을 보정하는 용도. 실패 시 (None,'',None).
    """
    return _from_item(_get_json(f"{_M_API}/stock/{code}/basic"))


def naver_index_date(code):
    """지수의 최신 거래일을 datetime(자정)으로 반환. 실패 시 None.

    국장 시트 날짜 정렬용 — KOSPI의 localTradedAt(KST) 기준. yfinance 일봉 게시
    지연으로 시트 날짜와 (네이버) 데이터 날짜가 어긋나는 문제 방지.
    """
    url = f"{_API}/index/{code}/basic" if code.startswith(".") else f"{_M_API}/index/{code}/basic"
    payload = _get_json(url)
    if isinstance(payload, dict):
        ts = payload.get("localTradedAt")
        if ts:
            try:
                return datetime.strptime(ts[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                pass
    return None
