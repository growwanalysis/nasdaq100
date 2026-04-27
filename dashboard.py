"""
strategy_dashboard.py
---------------------
Live Streamlit dashboard that scans the NASDAQ 100 universe against the
momentum-breakout strategy and shows which names qualify *right now*.

Pulls daily bars from Yahoo Finance (cached for 5 minutes), computes the
indicators on the fly, and renders:

  - KPI strip: scanned, qualifying, near-misses, avg score, median day change
  - Scanner table with all 6 conditions per stock (sortable, downloadable)
  - Detail panel with candlestick + SMA50 / SMA150 / EMA220 overlay and a
    condition-by-condition pass/fail checklist for the selected symbol

Run
---
    pip install streamlit yfinance pandas plotly
    streamlit run strategy_dashboard.py

Reads tickers from `nasdaq100_symbols.csv` next to the script by default
(falls back to a hard-coded list if the file is missing).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# ============================================================================
# CONFIG
# ============================================================================
APP_TITLE       = "NASDAQ 100 — Momentum Breakout Scanner"
SYMBOLS_FILE    = "nasdaq100_symbols.csv"
HIST_PERIOD     = "2y"                # ≥ 252+220 trading days for full warmup
CACHE_TTL_SEC   = 300                 # 5 minutes

# strategy windows (trading days)
SMA50_W, SMA150_W, EMA220_W = 50, 150, 220
HIGH52W_W, LOW52W_W, DIP_W  = 252, 252, 90

# fallback list if symbols CSV is missing
FALLBACK_SYMBOLS = [
    "NVDA","AAPL","MSFT","AMZN","GOOGL","AVGO","GOOG","META","TSLA","WMT",
    "AMD","ASML","MU","COST","INTC","NFLX","CSCO","PLTR","LRCX","AMAT",
    "KLAC","TXN","ARM","LIN","PEP","TMUS","ADI","AMGN","ISRG","SHOP",
    "GILD","QCOM","APP","SNDK","PANW","MRVL","BKNG","PDD","WDC","HON",
    "STX","CRWD","CEG","SBUX","INTU","VRTX","ADBE","CMCSA","MAR","SNPS",
    "MELI","CDNS","ABNB","CSX","MPWR","REGN","ADP","ORLY","DASH","MNST",
    "MDLZ","AEP","ROST","CTAS","BKR","WBD","PCAR","FTNT","NXPI","MSTR",
    "FANG","FAST","EA","ADSK","FER","XEL","MCHP","EXC","ODFL","DDOG",
    "PYPL","IDXX","CCEP","ALNY","TRI","KDP","TTWO","ROP","PAYX","AXON",
    "CPRT","GEHC","WDAY","INSM","CTSH","KHC","DXCM","VRSK","CHTR","ZS","CSGP",
]

st.set_page_config(
    page_title="NASDAQ 100 Scanner",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================================
# STYLING
# ============================================================================
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    [data-testid="stMetricValue"] { font-size: 1.55rem; font-weight: 600; }
    [data-testid="stMetricLabel"] { font-size: 0.85rem; opacity: 0.75; }
    h1 { font-weight: 700; letter-spacing: -0.02em; }
    h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .cond-row { padding: 4px 0; font-size: 0.95rem; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# DATA LAYER
# ============================================================================
@st.cache_data(show_spinner=False)
def load_symbols(path: str) -> list[str]:
    """Load tickers from CSV; fall back to hard-coded list if missing."""
    p = Path(path)
    if not p.exists():
        return FALLBACK_SYMBOLS
    try:
        df = pd.read_csv(p)
        col = df.columns[0]
        syms = (
            df[col].astype(str).str.strip().str.upper()
            .replace("", pd.NA).dropna().unique().tolist()
        )
        return syms or FALLBACK_SYMBOLS
    except Exception:
        return FALLBACK_SYMBOLS


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner="Fetching live data from Yahoo Finance ...")
def fetch_universe(symbols: tuple[str, ...], period: str) -> dict[str, pd.DataFrame]:
    """Bulk download daily bars. Returns {symbol: DataFrame indexed by Date}."""
    raw = yf.download(
        list(symbols),
        period=period, interval="1d",
        group_by="ticker", auto_adjust=False,
        threads=True, progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            sub = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in sub.columns]
            sub = sub[keep].dropna()
            if len(sub) < 260:                       # need ~1y warmup
                continue
            out[sym] = sub
        except (KeyError, ValueError):
            continue
    return out


# ============================================================================
# STRATEGY
# ============================================================================
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, l = df["Close"], df["Low"]
    df["SMA50"]         = c.rolling(SMA50_W).mean()
    df["SMA150"]        = c.rolling(SMA150_W).mean()
    df["EMA220"]        = c.ewm(span=EMA220_W, adjust=False).mean()
    df["High52W_prior"] = c.rolling(HIGH52W_W).max().shift(1)
    df["High52W"]       = c.rolling(HIGH52W_W).max()
    df["Low52W"]        = l.rolling(LOW52W_W).min()
    below = (l < df["EMA220"]).astype(int)
    df["DippedRecently"] = below.rolling(DIP_W, min_periods=1).max() > 0
    return df


def scan(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Run the strategy filters on the most recent bar of each stock."""
    rows = []
    for sym, raw in data.items():
        df = compute_indicators(raw)
        last, prev = df.iloc[-1], df.iloc[-2] if len(df) > 1 else df.iloc[-1]
        c = last["Close"]
        cond = {
            "C1": last["SMA150"] > last["EMA220"],
            "C2": c              > last["SMA50"],
            "C3": last["SMA50"]  > last["SMA150"],
            "C4": c              > 1.25 * last["Low52W"],
            "C5": bool(last["DippedRecently"]),
            "C6": c              > last["High52W_prior"],
        }
        score = sum(cond.values())
        rows.append({
            "Symbol":          sym,
            "Price":           c,
            "Day Δ%":          (c / prev["Close"] - 1) * 100 if len(df) > 1 else 0.0,
            "% from 52w High": (c / last["High52W"] - 1) * 100,
            "% above 52w Low": (c / last["Low52W"]  - 1) * 100,
            "SMA50":           last["SMA50"],
            "SMA150":          last["SMA150"],
            "EMA220":          last["EMA220"],
            "C1":              cond["C1"],
            "C2":              cond["C2"],
            "C3":              cond["C3"],
            "C4":              cond["C4"],
            "C5":              cond["C5"],
            "C6":              cond["C6"],
            "Score":           score,
            "Qualifies":       score == 6,
            "Volume":          int(last["Volume"]) if not pd.isna(last["Volume"]) else 0,
            "Last Bar":        df.index[-1].strftime("%Y-%m-%d"),
        })
    out = pd.DataFrame(rows)
    return out.sort_values(["Qualifies", "Score", "Symbol"],
                           ascending=[False, False, True]).reset_index(drop=True)


# ============================================================================
# CHART
# ============================================================================
def make_chart(symbol: str, raw: pd.DataFrame) -> go.Figure:
    df = compute_indicators(raw).iloc[-300:]            # last ~14 months
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
        name=symbol, showlegend=False,
        increasing_line_color="#22c55e", decreasing_line_color="#ef4444",
    ))
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA50"],  name="SMA 50",
                             line=dict(color="#3b82f6", width=1.6)))
    fig.add_trace(go.Scatter(x=df.index, y=df["SMA150"], name="SMA 150",
                             line=dict(color="#a855f7", width=1.6)))
    fig.add_trace(go.Scatter(x=df.index, y=df["EMA220"], name="EMA 220",
                             line=dict(color="#f59e0b", width=2, dash="dot")))
    # prior 52-week high level (rule 9 reference)
    if not pd.isna(df["High52W_prior"].iloc[-1]):
        fig.add_hline(y=df["High52W_prior"].iloc[-1], line_dash="dash",
                      line_color="#94a3b8", opacity=0.6,
                      annotation_text="prior 52w high", annotation_position="top left")
    fig.update_layout(
        height=480, hovermode="x unified",
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h", y=1.04, yanchor="bottom", x=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(148,163,184,0.15)"),
    )
    return fig


# ============================================================================
# UI HELPERS
# ============================================================================
COND_LABELS = {
    "C1": "SMA(150) > EMA(220)",
    "C2": "Close > SMA(50)",
    "C3": "SMA(50) > SMA(150)",
    "C4": "Close > 1.25 × 52w Low",
    "C5": "Dipped < EMA(220) in 90d",
    "C6": "Close at new 52w high",
}


def render_condition_list(row: pd.Series) -> None:
    for key, label in COND_LABELS.items():
        ok = bool(row[key])
        icon = "🟢" if ok else "🔴"
        st.markdown(f"<div class='cond-row'>{icon}&nbsp;&nbsp;{label}</div>",
                    unsafe_allow_html=True)


# ============================================================================
# MAIN
# ============================================================================
def main() -> None:
    # ---- header ------------------------------------------------------------
    h1, h2 = st.columns([5, 1])
    with h1:
        st.title("📈 NASDAQ 100 — Strategy Scanner")
        st.caption("Live momentum-breakout filter · Yahoo Finance · 5-minute cache")
    with h2:
        st.write("")
        if st.button("🔄 Refresh", use_container_width=True, type="primary"):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Loaded: {datetime.now().strftime('%H:%M:%S')}")

    # ---- sidebar -----------------------------------------------------------
    with st.sidebar:
        st.header("⚙️ Filters")
        symbols_path = st.text_input("Symbols file", SYMBOLS_FILE,
                                     help="CSV with a Symbol column. Falls back to a built-in list if missing.")
        view = st.radio(
            "Show",
            ["Qualifying only (6/6)", "Score ≥ 5", "Score ≥ 4", "All"],
            index=0,
        )
        min_price = st.number_input("Min price ($)", value=0.0, step=1.0)
        st.markdown("---")
        st.subheader("Strategy rules")
        st.markdown(
            "1. SMA(150) > EMA(220)\n"
            "2. Close > SMA(50)\n"
            "3. SMA(50) > SMA(150)\n"
            "4. Close > 1.25 × 52w Low\n"
            "5. Dipped < EMA(220) in past 90d\n"
            "6. Close > prior 252-day max close"
        )
        st.markdown("---")
        st.caption(
            "Live = today's most recent bar from Yahoo. During RTH the last bar "
            "updates as the trading day progresses; signals confirm at the close."
        )

    # ---- data --------------------------------------------------------------
    syms = load_symbols(symbols_path)
    if not syms:
        st.error("No symbols available."); return

    data = fetch_universe(tuple(syms), HIST_PERIOD)
    if not data:
        st.error("Failed to fetch market data from Yahoo Finance. Try Refresh.")
        return

    scan_df = scan(data)
    if min_price > 0:
        scan_df = scan_df[scan_df["Price"] >= min_price].reset_index(drop=True)

    # ---- KPI strip ---------------------------------------------------------
    qualifying  = scan_df[scan_df["Qualifies"]]
    near_misses = scan_df[(scan_df["Score"] == 5) & (~scan_df["Qualifies"])]
    avg_score   = scan_df["Score"].mean() if len(scan_df) else 0
    median_chg  = scan_df["Day Δ%"].median() if len(scan_df) else 0
    advancers   = (scan_df["Day Δ%"] > 0).sum()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Stocks scanned",        f"{len(scan_df)}")
    k2.metric("✅ Qualifying (6/6)",   f"{len(qualifying)}",
              f"{len(qualifying)/max(len(scan_df),1)*100:.0f}% of universe")
    k3.metric("⚠️ Near misses (5/6)",  f"{len(near_misses)}")
    k4.metric("Avg conditions met",    f"{avg_score:.2f} / 6")
    k5.metric("Median day Δ",          f"{median_chg:+.2f}%",
              f"{advancers} advancing")

    # quick chips for qualifying tickers
    if len(qualifying):
        st.markdown(
            "**Qualifying now:** &nbsp;" +
            " &nbsp;".join(f"`{s}`" for s in qualifying["Symbol"].tolist())
        )

    st.divider()

    # ---- view filter -------------------------------------------------------
    if view.startswith("Qualifying"):
        view_df = qualifying
    elif view.startswith("Score ≥ 5"):
        view_df = scan_df[scan_df["Score"] >= 5]
    elif view.startswith("Score ≥ 4"):
        view_df = scan_df[scan_df["Score"] >= 4]
    else:
        view_df = scan_df

    # ---- main two-column layout -------------------------------------------
    left, right = st.columns([3, 2], gap="large")

    with left:
        st.subheader(f"Scanner · {len(view_df)} stocks")
        if view_df.empty:
            st.info("No stocks match the selected filter right now.")
        else:
            display = view_df.copy()
            for col in ["Price", "SMA50", "SMA150", "EMA220"]:
                display[col] = display[col].round(2)
            for col in ["Day Δ%", "% from 52w High", "% above 52w Low"]:
                display[col] = display[col].round(2)

            st.dataframe(
                display.drop(columns=["Last Bar"]),
                use_container_width=True, hide_index=True, height=560,
                column_config={
                    "Symbol":          st.column_config.TextColumn(width="small"),
                    "Price":           st.column_config.NumberColumn(format="$%.2f"),
                    "Day Δ%":          st.column_config.NumberColumn(format="%.2f%%"),
                    "% from 52w High": st.column_config.NumberColumn(format="%.1f%%"),
                    "% above 52w Low": st.column_config.NumberColumn(format="%.1f%%"),
                    "SMA50":           st.column_config.NumberColumn(format="%.2f"),
                    "SMA150":          st.column_config.NumberColumn(format="%.2f"),
                    "EMA220":          st.column_config.NumberColumn(format="%.2f"),
                    "Volume":          st.column_config.NumberColumn(format="%d"),
                    "Score":           st.column_config.ProgressColumn(
                                          min_value=0, max_value=6, format="%d/6"),
                    "Qualifies":       st.column_config.CheckboxColumn(),
                    "C1": st.column_config.CheckboxColumn("C1", help=COND_LABELS["C1"]),
                    "C2": st.column_config.CheckboxColumn("C2", help=COND_LABELS["C2"]),
                    "C3": st.column_config.CheckboxColumn("C3", help=COND_LABELS["C3"]),
                    "C4": st.column_config.CheckboxColumn("C4", help=COND_LABELS["C4"]),
                    "C5": st.column_config.CheckboxColumn("C5", help=COND_LABELS["C5"]),
                    "C6": st.column_config.CheckboxColumn("C6", help=COND_LABELS["C6"]),
                },
            )

            st.download_button(
                "📥 Download scan as CSV",
                view_df.to_csv(index=False).encode("utf-8"),
                f"scan_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                "text/csv",
                use_container_width=True,
            )

    with right:
        st.subheader("Stock detail")
        options = (view_df["Symbol"].tolist()
                   if not view_df.empty else scan_df["Symbol"].tolist())
        if not options:
            st.info("Nothing to chart in the current filter.")
        else:
            # remember selection across reruns
            default_idx = 0
            if "selected_symbol" in st.session_state and st.session_state.selected_symbol in options:
                default_idx = options.index(st.session_state.selected_symbol)
            sel = st.selectbox("Symbol", options, index=default_idx, key="selected_symbol")

            if sel and sel in data:
                row = scan_df[scan_df["Symbol"] == sel].iloc[0]
                m1, m2, m3 = st.columns(3)
                m1.metric("Price",  f"${row['Price']:.2f}", f"{row['Day Δ%']:+.2f}%")
                m2.metric("Score",  f"{int(row['Score'])}/6",
                          "Qualifies" if row["Qualifies"] else "Not yet")
                m3.metric("From 52w high", f"{row['% from 52w High']:+.1f}%",
                          f"+{row['% above 52w Low']:.1f}% vs 52w low")
                st.caption(f"Last bar: {row['Last Bar']}")

                with st.expander("Condition breakdown", expanded=True):
                    render_condition_list(row)

                fig = make_chart(sel, data[sel])
                st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()