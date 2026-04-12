import yfinance as yf, pandas as pd, requests, re
from io import StringIO

def analyze(close):
    if len(close) < 40: return None
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    last, prev = close.iloc[-1], close.iloc[-2]
    if pd.isna(ma200.iloc[-1]): return None
    prior_c = close.iloc[-30:-5]
    recent_c = close.iloc[-5:]
    if len(prior_c) < 10 or prior_c.iloc[0] <= 0 or recent_c.iloc[0] <= 0:
        return None
    pc = (prior_c.iloc[-1] - prior_c.iloc[0]) / prior_c.iloc[0] / len(prior_c)
    rc = (recent_c.iloc[-1] - recent_c.iloc[0]) / recent_c.iloc[0] / len(recent_c)
    high52 = close.max()
    ltd = last < ma200.iloc[-1] and last < high52 * 0.85
    recent_low = close.iloc[-15:].min()
    prev_low = close.iloc[-40:-15].min()
    hl = recent_low > prev_low
    is_cover = pc < 0 and rc > 0 and abs(rc) > abs(pc) and ltd and hl
    return {
        "cover": is_cover, "last": last, "chg": (last/prev-1)*100,
        "dd_52w": (last/high52-1)*100, "ma200_diff": (last/ma200.iloc[-1]-1)*100,
        "pc_slope": pc*100, "rc_slope": rc*100, "hl": hl,
    }

# 추적 유니버스 + 관심 대형주
tracked = ["NVDA","AMD","AVGO","MU","TXN","ASML","LRCX","AMAT","INTC","MRVL",
           "AAPL","MSFT","GOOG","AMZN","META","NFLX","ORCL","CRM","ADBE","NOW",
           "PLTR","CRWD","DDOG","SNOW","ETN","VRT","GEV","SMR","CEG","TLN","BWXT",
           "JPM","BAC","GS","CAT","DE","XOM","COP","TSLA","IBM","HD"]

# S&P500 상위 + 한국 대형주 일부
kr = ["005930.KS","000660.KS","042700.KS","373220.KS","006400.KS","005380.KS",
      "035420.KS","035720.KS","105560.KS","055550.KS","005490.KS","051910.KS"]

all_t = tracked + kr
print(f"Scanning {len(all_t)} tickers...")
data = yf.download(all_t, period="1y", auto_adjust=True, progress=False, group_by="ticker")

covers = []
for t in all_t:
    try:
        c = data[t]["Close"].dropna() if len(all_t) > 1 else data["Close"].dropna()
        r = analyze(c)
        if r and r["cover"]:
            covers.append((t, r))
    except Exception as e:
        continue

names = {"005930.KS":"삼성전자","000660.KS":"SK하이닉스","042700.KS":"한미반도체",
         "373220.KS":"LG에너지솔루션","006400.KS":"삼성SDI","005380.KS":"현대차",
         "035420.KS":"NAVER","035720.KS":"카카오","105560.KS":"KB금융",
         "055550.KS":"신한지주","005490.KS":"POSCO홀딩스","051910.KS":"LG화학"}

print(f"\n=== Short Cover Sign 종목: {len(covers)}개 ===\n")
for t, r in sorted(covers, key=lambda x: x[1]["dd_52w"]):
    name = names.get(t, t)
    print(f"  {name:20s} ({t:10s})  ${r['last']:>8.2f}  전일 {r['chg']:+.2f}%")
    print(f"    52w high 대비 {r['dd_52w']:+.1f}%  /  200MA 대비 {r['ma200_diff']:+.1f}%")
    print(f"    직전 기울기 {r['pc_slope']:+.2f}%/일  /  최근 기울기 {r['rc_slope']:+.2f}%/일")
    print()
