<div align="center">

# 📊 NASDAQ 100 — Momentum Breakout Scanner

**A live 6-condition momentum-breakout scanner + backtester for all 100 NASDAQ stocks**

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://nasdaq100.streamlit.app)
&nbsp;
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=flat&logo=streamlit&logoColor=white)
![Plotly](https://img.shields.io/badge/Plotly-3F4F75?style=flat&logo=plotly&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat)

</div>

---

## 🌐 Live App

> **Try it now →** [anasdaq100.streamlit.app](https://anasdaq100.streamlit.app)

No installation needed. The app scans all 100 NASDAQ stocks live via Yahoo Finance and shows which ones qualify under the momentum-breakout strategy — right now.

---

## 🧠 The Strategy

A **long-only momentum-breakout** system. All **6 conditions** must be true on the signal day's close for a stock to qualify. Signals fire on day T's close; trades execute at day T+1's open.

### ✅ The 6 Conditions

| # | Condition | Logic |
|---|-----------|-------|
| C1 | Trend aligned | `SMA(150) > EMA(220)` |
| C2 | Price above fast MA | `Close > SMA(50)` |
| C3 | MAs stacked | `SMA(50) > SMA(150)` |
| C4 | Distance from lows | `Close > 1.25 × 52-week Low` (≥25% off low) |
| C5 | Recent pullback | `Low < EMA(220)` at least once in the past **90 trading days** |
| C6 | Breakout trigger | `Close > prior 252-day max of Close` (new 52-week closing high) |

> A stock scoring **6/6** is a full signal. **5/6** is a near-miss worth watching.

### 🚪 Exit Rules (whichever fires first on close of day T → execute at open T+1)

| Exit | Condition | Label |
|------|-----------|-------|
| Stop loss | `Close ≤ Entry price × 0.85` | `stop_loss` (-15%) |
| Trend break | `Close < EMA(220)` | `ema220_break` |

### 💼 Position Sizing

- **10% of current equity** per new position
- **Max 10 simultaneous positions**
- **Starting capital**: $100,000 USD
- **No leverage, no shorting**
- Optional transaction cost (bps) configurable at backtest time

---

## 📊 Backtest Results

> Backtested on 100 NASDAQ stocks using local CSV data via `backtest_strategy.py`

| Metric | Value |
|--------|-------|
| 💰 Start Capital | $100,000 |
| 💵 Final Equity | $231,030 |
| 📈 Total Return | **+131.03%** |
| 🚀 CAGR | **14.23%** |
| 📉 Max Drawdown | -25.15% |
| 📊 Annualised Vol | 20.81% |
| ⚖️ Sharpe Ratio | **0.74** |
| 🔁 Total Trades | 68 |
| 🏆 Win Rate | 36.8% |
| 💹 Avg Win | +51.35% |
| 🛑 Avg Loss | -10.02% |
| 🌟 Best Trade | +333.61% |
| ⚠️ Worst Trade | -24.11% |
| ⚡ Profit Factor | **2.39** |
| 🕐 Avg Days Held | 198.1 days |

> $100K grew to $231K — a low win rate (36.8%) offset by large asymmetric winners averaging **+51% per win** vs **-10% per loss**.

---

## 🖥️ Dashboard Layout

The dashboard uses a **2-column layout** (scanner left, stock detail right) with a sidebar for filters.

### 📊 KPI Strip (top of page)

| KPI | Description |
|-----|-------------|
| Stocks scanned | Total symbols fetched and evaluated |
| ✅ Qualifying (6/6) | Stocks passing all 6 conditions right now |
| ⚠️ Near misses (5/6) | One condition away from a full signal |
| Avg conditions met | Average score across the universe (out of 6) |
| Median day Δ | Median % change today + count of advancers |

Qualifying tickers are also shown as **inline chips** for quick reference.

---

### 📋 Left Panel — Scanner Table

A sortable, scrollable table of all stocks in the current view. Each row shows:

- `Symbol`, `Price`, `Day Δ%`, `% from 52w High`, `% above 52w Low`
- `SMA50`, `SMA150`, `EMA220` values
- **C1–C6** as checkboxes (hover for condition description)
- **Score** as a visual progress bar (0–6)
- **Qualifies** checkbox

Includes a **📥 Download scan as CSV** button (timestamped filename).

---

### 🔍 Right Panel — Stock Detail

Select any stock from the filtered list to see:

- **Mini metrics**: Price with day Δ%, Score (N/6), distance from 52w high/low
- **Condition breakdown**: 🟢/🔴 per condition with label
- **Candlestick chart** (last ~14 months / 300 bars) with:
  - SMA50 (blue), SMA150 (purple), EMA220 (amber dotted)
  - Prior 52-week high as a dashed horizontal reference line

---

### ⚙️ Sidebar Controls

| Control | Default | Description |
|---------|---------|-------------|
| Symbols file | `nasdaq100_symbols.csv` | CSV of tickers to scan |
| View filter | `Qualifying only (6/6)` | Options: 6/6, ≥5, ≥4, All |
| Min price ($) | `0` | Filter out penny stocks |
| 🔄 Refresh | — | Clears cache and re-fetches all data |

Data is cached for **5 minutes** to balance freshness and Yahoo Finance rate limits.

> **Note:** During regular trading hours, the last bar updates as the day progresses. Signals confirm at market close.

---

## 📂 Project Structure

```
nasdaq100/
├── dashboard.py               # Streamlit live scanner app
├── backtest_strategy.py       # Historical backtest engine
├── download_nasdaq100.py      # Bulk data downloader (yfinance → CSV)
├── nasdaq100_symbols.csv      # 100 NASDAQ ticker symbols
├── requirements.txt           # Python dependencies
│
├── data/                      # Pre-downloaded OHLCV CSVs (100 stocks)
│   ├── NVDA.csv
│   ├── AAPL.csv
│   ├── MSFT.csv
│   └── ... (97 more)
│
└── results/                   # Backtest outputs
    ├── trades.csv             # Every closed trade with PnL & exit reason
    ├── equity_curve.csv       # Daily cash, holdings, equity, drawdown, positions
    ├── open_positions.csv     # Positions still open at end of run
    └── summary.txt            # Human-readable performance summary
```

---

## 🚀 Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/growwanalysis/nasdaq100.git
cd nasdaq100
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> Requirements: `streamlit`, `yfinance`, `pandas`, `numpy`, `plotly`

### 3. (Optional) Refresh stock data

```bash
python download_nasdaq100.py
```

Downloads daily OHLCV CSVs for all 100 tickers into `data/`. The live dashboard also fetches directly via yfinance (5-min cache).

### 4. Launch the dashboard

```bash
streamlit run dashboard.py
```

Opens at `http://localhost:8501`. If `nasdaq100_symbols.csv` is missing, the app automatically falls back to a built-in list of all 100 tickers.

### 5. Run the backtest

```bash
python backtest_strategy.py

# Custom options:
python backtest_strategy.py --data-dir data --start 2020-01-01 --cost-bps 5
```

Results are saved to `results/` — full trade log, equity curve, open positions, and a plain-text summary.

---

## 🔁 Also See: Nifty 100 Scanner

This project is the **US counterpart** to the [Nifty 100 Strategy Scanner](https://nifty100.streamlit.app) — the same momentum-breakout strategy applied to India's top 100 NSE stocks.

| | NASDAQ 100 | Nifty 100 |
|--|--|--|
| Universe | 100 US stocks (NASDAQ) | 100 Indian stocks (NSE) |
| Currency | USD | INR |
| Backtest return | +131% | +203% |
| CAGR | 14.23% | 19.21% |
| Sharpe | 0.74 | 1.22 |
| Conditions | 6 (C1–C6 all-in-one) | 5 filters + breakout trigger |
| Cache TTL | 5 minutes | 10 minutes |

---

## ⚠️ Disclaimer

> This tool is built **for educational and research purposes only**. It is **not financial advice**. Past backtest performance does not guarantee future results. Always do your own research before making any investment decisions.

---

## 🙌 Contributing

1. Fork this repo
2. Create a branch: `git checkout -b feature/my-feature`
3. Commit: `git commit -m "Add my feature"`
4. Push & open a Pull Request

---

<div align="center">

Made with ❤️ by [growwanalysis](https://github.com/growwanalysis)

⭐ **Star this repo if it helped you!**

</div>
