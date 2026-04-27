"""
backtest_strategy.py
--------------------
Backtest a momentum-breakout strategy on the per-symbol CSVs produced by
download_nasdaq100.py.

Strategy
~~~~~~~~
Filters (all must be true on the signal day's CLOSE):
  1. SMA(150) > EMA(220)
  2. Close   > SMA(50)
  3. SMA(50) > SMA(150)
  4. Close   > 1.25 * 52-week Low                (>25% off 52w low)
  5. Low has dipped below EMA(220) at least once in the past 90 trading days
  6. Close strictly greater than the prior 252-day max close (52w breakout)

Trade rules
~~~~~~~~~~~
  - Signals are evaluated on the CLOSE of day T.
  - Entries are placed at the OPEN of day T+1.
  - An open position is exited at the OPEN of day T+1 if, on the CLOSE of T:
        * Close <= entry_price * (1 - stop_pct)        (15% stop loss), OR
        * Close <  EMA(220)                            (trend break)
        whichever happens first.
  - Position sizing : 10% of current equity at entry.
  - Max simultaneous positions : 10.
  - Starting capital : $100,000. Cash earns 0%.
  - Optional one-way transaction cost in bps (default 0).

Outputs (in --out folder)
~~~~~~~~~~~~~~~~~~~~~~~~~
  equity_curve.csv     daily cash, holdings, equity, drawdown, n_positions
  trades.csv           every closed trade with PnL, return %, exit reason
  open_positions.csv   positions still open at the end of the run
  summary.txt          human-readable performance summary
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backtest")

# ---- defaults ---------------------------------------------------------------
DEFAULT_DATA_DIR      = "data"
DEFAULT_OUT_DIR       = "results"
DEFAULT_START_CAPITAL = 100_000.0
DEFAULT_MAX_POSITIONS = 10
DEFAULT_POSITION_SIZE = 0.10
DEFAULT_STOP_PCT      = 0.15
DEFAULT_COST_BPS      = 0.0

# strategy windows in trading days
SMA50_WIN    = 50
SMA150_WIN   = 150
EMA220_WIN   = 220
DIP_LOOKBACK = 90
HIGH52W_WIN  = 252
LOW52W_WIN   = 252


# ---- data loading -----------------------------------------------------------
def load_all_data(data_dir: Path, start_date: pd.Timestamp | None) -> dict[str, pd.DataFrame]:
    """Load every per-symbol CSV in `data_dir` (skipping log files starting with '_')."""
    out: dict[str, pd.DataFrame] = {}
    for csv in sorted(data_dir.glob("*.csv")):
        if csv.name.startswith("_"):
            continue
        symbol = csv.stem
        try:
            df = pd.read_csv(csv, parse_dates=["Date"])
        except Exception as e:
            log.warning("Could not read %s: %s", csv.name, e)
            continue
        df = df.sort_values("Date").drop_duplicates("Date").set_index("Date")
        keep = [c for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
        df = df[keep].astype(float)
        if start_date is not None:
            df = df.loc[df.index >= start_date]
        if len(df) < 260:                         # need ~1y of warmup
            continue
        out[symbol] = df
    log.info("Loaded %d symbols from %s", len(out), data_dir)
    return out


# ---- indicators -------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns and the boolean `entry_signal` column."""
    df = df.copy()
    close, low = df["Close"], df["Low"]

    df["SMA50"]         = close.rolling(SMA50_WIN).mean()
    df["SMA150"]        = close.rolling(SMA150_WIN).mean()
    df["EMA220"]        = close.ewm(span=EMA220_WIN, adjust=False).mean()
    df["High52W_prior"] = close.rolling(HIGH52W_WIN).max().shift(1)
    df["Low52W"]        = low.rolling(LOW52W_WIN).min()

    below_ema = (low < df["EMA220"]).astype(int)
    df["DippedRecently"] = below_ema.rolling(DIP_LOOKBACK, min_periods=1).max() > 0

    cond1 = df["SMA150"] > df["EMA220"]
    cond2 = close       > df["SMA50"]
    cond3 = df["SMA50"] > df["SMA150"]
    cond4 = close       > 1.25 * df["Low52W"]
    cond5 = df["DippedRecently"]
    cond6 = close       > df["High52W_prior"]                # breakout

    df["entry_signal"] = (cond1 & cond2 & cond3 & cond4 & cond5 & cond6).fillna(False)
    return df


# ---- backtest engine --------------------------------------------------------
@dataclass
class Position:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int


def run_backtest(
    data: dict[str, pd.DataFrame],
    start_capital: float,
    max_positions: int,
    position_size_pct: float,
    stop_pct: float,
    cost_bps: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    all_dates = sorted(set().union(*(df.index for df in data.values())))
    log.info("Simulating %d trading days from %s to %s",
             len(all_dates), all_dates[0].date(), all_dates[-1].date())

    cash       = float(start_capital)
    positions: dict[str, Position] = {}
    trades:    list[dict] = []
    equity_records: list[dict] = []

    cost_rate = cost_bps / 10_000.0
    prev_equity = float(start_capital)

    for i, date in enumerate(all_dates):
        if i == 0:
            equity_records.append({"date": date, "cash": cash, "holdings": 0.0,
                                   "equity": cash, "n_positions": 0})
            continue

        prev_date = all_dates[i - 1]

        # ---- A. exits (signal on prev_date, fill at today's open) ----------
        exits_today: list[tuple[str, str]] = []
        for sym, pos in positions.items():
            df = data[sym]
            if prev_date not in df.index:
                continue
            close_p = df.at[prev_date, "Close"]
            ema220  = df.at[prev_date, "EMA220"]
            stop_p  = pos.entry_price * (1.0 - stop_pct)

            reason = None
            if close_p <= stop_p:
                reason = "stop_loss"
            elif close_p < ema220:
                reason = "ema220_break"
            if reason:
                exits_today.append((sym, reason))

        for sym, reason in exits_today:
            pos = positions.pop(sym)
            df  = data[sym]
            exit_price = df.at[date, "Open"] if date in df.index else df.at[prev_date, "Close"]
            gross = pos.shares * exit_price
            fees  = gross * cost_rate
            cash += gross - fees
            entry_cost = pos.shares * pos.entry_price
            entry_fees = entry_cost * cost_rate
            pnl = (gross - fees) - (entry_cost + entry_fees)
            trades.append({
                "symbol":      sym,
                "entry_date":  pos.entry_date,
                "entry_price": round(pos.entry_price, 4),
                "exit_date":   date,
                "exit_price":  round(exit_price, 4),
                "shares":      pos.shares,
                "pnl_dollars": round(pnl, 2),
                "return_pct":  round((exit_price / pos.entry_price - 1) * 100, 2),
                "exit_reason": reason,
                "days_held":   int((date - pos.entry_date).days),
            })

        # ---- B. entries (signal on prev_date, fill at today's open) --------
        candidates = []
        for sym, df in data.items():
            if sym in positions:
                continue
            if prev_date not in df.index or date not in df.index:
                continue
            if bool(df.at[prev_date, "entry_signal"]):
                candidates.append(sym)
        candidates.sort()

        target_alloc = prev_equity * position_size_pct

        for sym in candidates:
            if len(positions) >= max_positions:
                break
            entry_price = data[sym].at[date, "Open"]
            shares = int(target_alloc // entry_price)
            if shares <= 0:
                continue
            cost = shares * entry_price
            fees = cost * cost_rate
            if cash < cost + fees:
                continue
            cash -= (cost + fees)
            positions[sym] = Position(sym, date, entry_price, shares)

        # ---- C. mark-to-market at today's close -----------------------------
        holdings = 0.0
        for sym, pos in positions.items():
            df = data[sym]
            holdings += pos.shares * (df.at[date, "Close"] if date in df.index else pos.entry_price)
        equity = cash + holdings
        equity_records.append({"date": date, "cash": cash, "holdings": holdings,
                               "equity": equity, "n_positions": len(positions)})
        prev_equity = equity

    equity_df = pd.DataFrame(equity_records).set_index("date")
    equity_df["drawdown"] = equity_df["equity"] / equity_df["equity"].cummax() - 1

    trades_df = pd.DataFrame(trades).sort_values("entry_date").reset_index(drop=True) \
                if trades else pd.DataFrame(columns=[
                    "symbol","entry_date","entry_price","exit_date","exit_price",
                    "shares","pnl_dollars","return_pct","exit_reason","days_held"])

    open_pos = pd.DataFrame([
        {"symbol": p.symbol, "entry_date": p.entry_date,
         "entry_price": p.entry_price, "shares": p.shares,
         "last_price":  data[p.symbol]["Close"].iloc[-1],
         "open_pnl_pct": (data[p.symbol]["Close"].iloc[-1] / p.entry_price - 1) * 100}
        for p in positions.values()
    ])

    return equity_df, trades_df, open_pos


# ---- metrics ----------------------------------------------------------------
def compute_metrics(equity_df: pd.DataFrame, trades_df: pd.DataFrame, start_capital: float) -> dict:
    equity = equity_df["equity"]
    daily  = equity.pct_change().dropna()

    n_years   = len(equity) / 252.0
    final     = float(equity.iloc[-1])
    total_ret = final / start_capital - 1
    cagr      = (final / start_capital) ** (1 / n_years) - 1 if n_years > 0 else 0.0
    vol       = daily.std() * np.sqrt(252) if len(daily) > 1 else 0.0
    sharpe    = (daily.mean() * 252) / vol if vol > 0 else 0.0
    max_dd    = float(equity_df["drawdown"].min())

    if len(trades_df) > 0:
        wins   = trades_df[trades_df["pnl_dollars"] > 0]
        losses = trades_df[trades_df["pnl_dollars"] <= 0]
        win_rate = len(wins) / len(trades_df)
        avg_win  = wins["return_pct"].mean()  if len(wins)   else 0.0
        avg_loss = losses["return_pct"].mean() if len(losses) else 0.0
        gw, gl   = wins["pnl_dollars"].sum(), abs(losses["pnl_dollars"].sum())
        pf       = gw / gl if gl > 0 else float("inf")
        avg_hold = trades_df["days_held"].mean()
        best, worst = trades_df["return_pct"].max(), trades_df["return_pct"].min()
    else:
        win_rate = avg_win = avg_loss = pf = avg_hold = best = worst = 0.0

    return {
        "Start capital":     f"${start_capital:,.0f}",
        "Final equity":      f"${final:,.0f}",
        "Total return":      f"{total_ret*100:.2f}%",
        "CAGR":              f"{cagr*100:.2f}%",
        "Annualised vol":    f"{vol*100:.2f}%",
        "Sharpe ratio":      f"{sharpe:.2f}",
        "Max drawdown":      f"{max_dd*100:.2f}%",
        "Number of trades":  f"{len(trades_df)}",
        "Win rate":          f"{win_rate*100:.1f}%",
        "Avg win":           f"{avg_win:.2f}%",
        "Avg loss":          f"{avg_loss:.2f}%",
        "Best trade":        f"{best:.2f}%",
        "Worst trade":       f"{worst:.2f}%",
        "Profit factor":     f"{pf:.2f}",
        "Avg holding days":  f"{avg_hold:.1f}",
    }


def render_summary(metrics: dict) -> str:
    width = max(len(k) for k in metrics) + 2
    out = ["=" * 50, "BACKTEST SUMMARY", "=" * 50]
    out += [f"{k:<{width}}{v:>22}" for k, v in metrics.items()]
    out += ["=" * 50]
    return "\n".join(out)


# ---- main -------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest momentum-breakout strategy on NASDAQ 100 daily data")
    ap.add_argument("--data",          default=DEFAULT_DATA_DIR)
    ap.add_argument("--out",           default=DEFAULT_OUT_DIR)
    ap.add_argument("--start",         default=None,                help="Backtest start date YYYY-MM-DD")
    ap.add_argument("--capital",       type=float, default=DEFAULT_START_CAPITAL)
    ap.add_argument("--max-positions", type=int,   default=DEFAULT_MAX_POSITIONS)
    ap.add_argument("--size-pct",      type=float, default=DEFAULT_POSITION_SIZE)
    ap.add_argument("--stop-pct",      type=float, default=DEFAULT_STOP_PCT)
    ap.add_argument("--cost-bps",      type=float, default=DEFAULT_COST_BPS,
                    help="One-way transaction cost in basis points (default 0)")
    args = ap.parse_args()

    data_dir = Path(args.data)
    out_dir  = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.exists():
        log.error("Data folder not found: %s", data_dir); return 1

    start_dt = pd.Timestamp(args.start) if args.start else None
    data = load_all_data(data_dir, start_dt)
    if not data:
        log.error("No usable data in %s", data_dir); return 1

    log.info("Computing indicators ...")
    for sym in list(data.keys()):
        data[sym] = compute_indicators(data[sym])

    log.info("Running backtest ...")
    equity_df, trades_df, open_pos = run_backtest(
        data,
        start_capital     = args.capital,
        max_positions     = args.max_positions,
        position_size_pct = args.size_pct,
        stop_pct          = args.stop_pct,
        cost_bps          = args.cost_bps,
    )

    metrics = compute_metrics(equity_df, trades_df, args.capital)
    summary = render_summary(metrics)
    print("\n" + summary + "\n")

    equity_df.to_csv(out_dir / "equity_curve.csv")
    trades_df.to_csv(out_dir / "trades.csv", index=False)
    open_pos.to_csv(out_dir / "open_positions.csv", index=False)
    (out_dir / "summary.txt").write_text(summary + "\n")
    log.info("Wrote results to %s/", out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())