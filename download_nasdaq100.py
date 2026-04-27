"""
download_nasdaq100.py
---------------------
Download daily OHLCV data for NASDAQ 100 stocks from Yahoo Finance and save
each symbol to its own CSV inside a `data/` folder.

Behavior
--------
* First run  : fetches full history from START_DATE (default 2020-01-01) to today.
* Later runs : reads the last date already saved per symbol and only fetches
               new bars after that (incremental update, much faster).

Usage
-----
    pip install yfinance pandas
    python download_nasdaq100.py
    python download_nasdaq100.py --symbols nasdaq100_symbols.csv --out data --start 2020-01-01
    python download_nasdaq100.py --workers 16          # parallel downloads
    python download_nasdaq100.py --force               # ignore existing files, redownload all

Output
------
    data/AAPL.csv, data/MSFT.csv, ... one file per symbol.
    A run summary is appended to data/_download_log.csv.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    sys.exit("yfinance is required. Install with: pip install yfinance pandas")


# ---------- defaults ---------------------------------------------------------
DEFAULT_SYMBOLS_CSV = "nasdaq100_symbols.csv"
DEFAULT_OUT_DIR     = "data"
DEFAULT_START       = "2020-01-01"
DEFAULT_WORKERS     = 8
RETRIES             = 3
RETRY_SLEEP_SEC     = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nasdaq100")


# ---------- helpers ----------------------------------------------------------
def load_symbols(path: Path) -> list[str]:
    """Load tickers from a single-column CSV. Handles BOM and whitespace."""
    df = pd.read_csv(path)
    col = df.columns[0]                               # first column, whatever it's named
    symbols = (
        df[col].astype(str).str.strip().str.upper()
        .replace("", pd.NA).dropna().unique().tolist()
    )
    log.info("Loaded %d symbols from %s", len(symbols), path)
    return symbols


def last_saved_date(csv_path: Path) -> pd.Timestamp | None:
    """Return the latest Date already in csv_path, or None if file is missing/empty."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return None
    try:
        # only read the Date column to keep it cheap
        d = pd.read_csv(csv_path, usecols=["Date"], parse_dates=["Date"])
        if d.empty:
            return None
        return d["Date"].max().normalize()
    except Exception as e:
        log.warning("Could not read %s (%s) – will redownload", csv_path.name, e)
        return None


def fetch_one(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Download one symbol with retries. Returns a tidy DataFrame."""
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            df = yf.download(
                symbol,
                start=start,
                end=end,
                interval="1d",
                auto_adjust=False,        # keep raw OHLC + Adj Close
                progress=False,
                threads=False,            # we parallelise at the symbol level ourselves
            )
            if df is None or df.empty:
                return pd.DataFrame()

            # yfinance sometimes returns a MultiIndex on columns – flatten it.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.reset_index().rename(columns={"index": "Date"})
            df["Symbol"] = symbol
            cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
            df = df[[c for c in cols if c in df.columns]]
            return df
        except Exception as e:
            last_err = e
            log.warning("[%s] attempt %d/%d failed: %s", symbol, attempt, RETRIES, e)
            time.sleep(RETRY_SLEEP_SEC * attempt)
    raise RuntimeError(f"{symbol}: all {RETRIES} attempts failed ({last_err})")


def update_symbol(symbol: str, out_dir: Path, start: str, end: str, force: bool) -> dict:
    """Download (or incrementally update) one symbol. Returns a status dict."""
    csv_path = out_dir / f"{symbol}.csv"
    fetch_start = start

    if not force:
        last = last_saved_date(csv_path)
        if last is not None:
            next_day = (last + timedelta(days=1)).strftime("%Y-%m-%d")
            if next_day >= end:
                return {"symbol": symbol, "status": "up-to-date", "rows_added": 0,
                        "last_date": last.strftime("%Y-%m-%d")}
            fetch_start = next_day

    new_df = fetch_one(symbol, fetch_start, end)
    if new_df.empty:
        return {"symbol": symbol, "status": "no-data", "rows_added": 0, "last_date": None}

    if csv_path.exists() and not force and fetch_start != start:
        existing = pd.read_csv(csv_path, parse_dates=["Date"])
        combined = (
            pd.concat([existing, new_df], ignore_index=True)
              .drop_duplicates(subset=["Date"], keep="last")
              .sort_values("Date")
        )
    else:
        combined = new_df.sort_values("Date")

    combined.to_csv(csv_path, index=False)
    return {
        "symbol": symbol,
        "status": "updated" if csv_path.exists() else "created",
        "rows_added": len(new_df),
        "last_date": combined["Date"].max().strftime("%Y-%m-%d"),
    }


# ---------- main -------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Download NASDAQ 100 daily data from Yahoo Finance")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS_CSV, help="CSV file with a Symbol column")
    ap.add_argument("--out",     default=DEFAULT_OUT_DIR,     help="Output folder for per-symbol CSVs")
    ap.add_argument("--start",   default=DEFAULT_START,       help="Start date YYYY-MM-DD")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel download workers")
    ap.add_argument("--force",   action="store_true", help="Redownload full history, ignore existing CSVs")
    args = ap.parse_args()

    symbols_path = Path(args.symbols)
    out_dir      = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not symbols_path.exists():
        log.error("Symbols file not found: %s", symbols_path)
        return 1

    symbols = load_symbols(symbols_path)
    end = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")  # yfinance end is exclusive
    log.info("Downloading %s → %s into %s/  (workers=%d, force=%s)",
             args.start, end, out_dir, args.workers, args.force)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(update_symbol, s, out_dir, args.start, end, args.force): s
                   for s in symbols}
        for i, fut in enumerate(as_completed(futures), 1):
            sym = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"symbol": sym, "status": "error", "rows_added": 0,
                       "last_date": None, "error": str(e)}
                log.error("[%s] %s", sym, e)
            results.append(res)
            log.info("(%3d/%d) %-6s %-11s rows+=%-5s last=%s",
                     i, len(symbols), res["symbol"], res["status"],
                     res["rows_added"], res.get("last_date"))

    # write run summary
    summary = pd.DataFrame(results).sort_values("symbol")
    log_path = out_dir / "_download_log.csv"
    summary.insert(0, "run_at", datetime.now().isoformat(timespec="seconds"))
    summary.to_csv(log_path, mode="a", index=False, header=not log_path.exists())

    # final tally
    counts = summary["status"].value_counts().to_dict()
    log.info("Done. %s  →  log appended to %s", counts, log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())