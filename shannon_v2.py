import yfinance as yf
import pandas as pd
import numpy as np

END, START = "2026-04-12", "2021-04-12"

def cagr(s):
    y = (s.index[-1]-s.index[0]).days/365.25
    return (s.iloc[-1]/s.iloc[0])**(1/y) - 1
def vol(r): return r.std()*np.sqrt(252)
def mdd(s): return ((s/s.cummax())-1).min()

def rebalance(prices, weights, freq="ME"):
    prices = prices.dropna().copy()
    tks = list(weights.keys())
    w = np.array([weights[t] for t in tks])
    rebal = set(prices.resample(freq).last().index)
    shares = w / prices[tks].iloc[0].values
    out = []
    for i, d in enumerate(prices.index):
        px = prices[tks].iloc[i].values
        v = np.sum(shares*px)
        out.append(v)
        if d in rebal and i != 0:
            shares = (v*w)/px
    return pd.Series(out, index=prices.index)

tickers = ["BTC-USD","GLD","SPY","SHY","TLT"]  # SHY=단기채(현금대용)
data = yf.download(tickers, start=START, end=END, auto_adjust=True, progress=False)["Close"].ffill().dropna()

combos = {
    "① BTC+GLD+SPY (1/3씩, 공격)":              {"BTC-USD":1/3,"GLD":1/3,"SPY":1/3},
    "② BTC+GLD+SPY+SHY (25% 균등)":             {"BTC-USD":.25,"GLD":.25,"SPY":.25,"SHY":.25},
    "③ BTC10+GLD30+SPY40+SHY20 (밸런스)":       {"BTC-USD":.10,"GLD":.30,"SPY":.40,"SHY":.20},
    "④ BTC10+GLD30+SPY40+TLT20 (채권 대신)":    {"BTC-USD":.10,"GLD":.30,"SPY":.40,"TLT":.20},
    "⑤ BTC5+GLD25+SPY45+SHY25 (보수적)":        {"BTC-USD":.05,"GLD":.25,"SPY":.45,"SHY":.25},
}

print(f"기간: {data.index[0].date()} ~ {data.index[-1].date()}\n")
print(f"{'조합':<42} {'CAGR':>7} {'변동성':>7} {'MDD':>7} {'Sharpe':>7}")
print("-"*82)
for name, w in combos.items():
    tks = list(w.keys())
    sub = data[tks].dropna()
    for lbl, series in [(" ", rebalance(sub, w, "ME"))]:
        c, v, d = cagr(series), vol(series.pct_change().dropna()), mdd(series)
        print(f"{name:<42} {c*100:6.2f}% {v*100:6.2f}% {d*100:6.2f}% {c/v:6.2f}")

# 위기 구간 분석 (2022년)
print("\n=== 2022년 (금리인상기) 구간 성과 ===")
sub22 = data.loc["2022-01-01":"2022-12-31"]
for name, w in combos.items():
    tks = list(w.keys())
    s = rebalance(sub22[tks].dropna(), w, "ME")
    ret22 = s.iloc[-1]/s.iloc[0]-1
    d = mdd(s)
    print(f"{name:<42} 연수익:{ret22*100:6.2f}%   MDD:{d*100:6.2f}%")
