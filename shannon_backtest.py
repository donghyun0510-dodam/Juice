import yfinance as yf
import pandas as pd
import numpy as np

END = "2026-04-12"
START = "2021-04-12"

def cagr(series):
    years = (series.index[-1] - series.index[0]).days / 365.25
    return (series.iloc[-1] / series.iloc[0]) ** (1/years) - 1

def vol(returns):
    return returns.std() * np.sqrt(252)

def backtest_rebalance(prices: pd.DataFrame, weights: dict, freq="ME"):
    """periodic rebalance to target weights. freq: 'ME'(월말), 'QE'(분기말), 'W'"""
    prices = prices.dropna().copy()
    tickers = list(weights.keys())
    w = np.array([weights[t] for t in tickers])
    
    # rebalance dates
    rebal_dates = prices.resample(freq).last().index
    rebal_dates = [d for d in rebal_dates if d in prices.index or prices.index.searchsorted(d) < len(prices)]
    
    # Start with $1
    shares = w / prices[tickers].iloc[0].values  # $1 portfolio
    portfolio = []
    last_rebal_idx = 0
    
    for i, date in enumerate(prices.index):
        px = prices[tickers].iloc[i].values
        val = np.sum(shares * px)
        portfolio.append(val)
        # Rebalance at month-end
        if date in rebal_dates and i != 0:
            shares = (val * w) / px
    
    return pd.Series(portfolio, index=prices.index)

def buy_hold(prices: pd.DataFrame, weights: dict):
    tickers = list(weights.keys())
    w = np.array([weights[t] for t in tickers])
    shares = w / prices[tickers].iloc[0].values
    return (prices[tickers] * shares).sum(axis=1)

combos = {
    "SPY+GLD (50/50)":         {"SPY": 0.5, "GLD": 0.5},
    "SPY+TLT+GLD (1/3씩)":      {"SPY": 1/3, "TLT": 1/3, "GLD": 1/3},
    "BTC+SPY (50/50)":         {"BTC-USD": 0.5, "SPY": 0.5},
    "BTC+GLD (50/50)":         {"BTC-USD": 0.5, "GLD": 0.5},
}

all_tickers = sorted({t for w in combos.values() for t in w})
print(f"Downloading: {all_tickers}")
data = yf.download(all_tickers, start=START, end=END, auto_adjust=True, progress=False)["Close"]
data = data.ffill().dropna()
print(f"Data range: {data.index[0].date()} ~ {data.index[-1].date()} ({len(data)} days)\n")

# Individual CAGR & correlation
print("=== 개별 자산 성과 ===")
for t in all_tickers:
    s = data[t]
    ret = s.pct_change().dropna()
    print(f"  {t:10s}  CAGR: {cagr(s)*100:6.2f}%   연변동성: {vol(ret)*100:5.2f}%")

print("\n=== 자산 간 상관계수 (일간 수익률) ===")
print((data.pct_change().corr()*100).round(1))

print("\n=== 포트폴리오 비교 ===")
print(f"{'조합':<28} {'전략':<18} {'CAGR':>8} {'변동성':>8} {'Sharpe*':>8}")
print("-" * 76)
for name, w in combos.items():
    tickers = list(w.keys())
    sub = data[tickers].dropna()
    
    bh = buy_hold(sub, w)
    reb_m = backtest_rebalance(sub, w, freq="ME")
    reb_q = backtest_rebalance(sub, w, freq="QE")
    
    for label, series in [("Buy&Hold", bh), ("월간 리밸런싱", reb_m), ("분기 리밸런싱", reb_q)]:
        c = cagr(series)
        v = vol(series.pct_change().dropna())
        sh = c / v if v > 0 else 0
        print(f"{name:<28} {label:<18} {c*100:6.2f}%  {v*100:6.2f}%  {sh:6.2f}")
    print()

print("* Sharpe는 무위험수익률 0% 가정 단순비율")
