"""
vnstock_data_adapter.py
=======================
Thin adapter that maps the TradingAgents data interface onto vnstock_data
(sponsored library, v3.0.0+ Unified UI).

All public functions in this module have the SAME signature as their
counterparts in y_finance.py and alpha_vantage.py so that interface.py can
call them transparently through the vendor dispatch table.

Availability guard
------------------
If vnstock_data is not installed the module still imports cleanly — every
function raises ``VnstockDataUnavailableError`` at call time.  This keeps the
import graph intact and lets interface.py fall through to the next vendor in
the fallback chain.

Supported functions (mirrors interface.py VENDOR_METHODS keys)
--------------------------------------------------------------
- get_stock_data(symbol, start_date, end_date)  -> str
- get_indicators(symbol, indicator, curr_date, look_back_days)  -> str  [best-effort]
- get_fundamentals(ticker, curr_date)  -> str
- get_balance_sheet(ticker, freq, curr_date)  -> str
- get_cashflow(ticker, freq, curr_date)  -> str
- get_income_statement(ticker, freq, curr_date)  -> str
- get_news(ticker, start_date, end_date)  -> str
- get_global_news(curr_date, look_back_days, limit)  -> str  [not supported, raises]
- get_insider_transactions(ticker)  -> str  [not supported, raises]
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def _ensure_home_venv_on_path() -> None:
    """Bridge ~/.venv site-packages into sys.path.

    Sponsored vnstock libs (vnstock_ta, vnstock_news, etc.) are installed in
    ~/.venv per the recommended setup. TradingAgents uses its own project venv,
    so we insert the home venv's site-packages to make them importable here.
    """
    home_venv = Path.home() / ".venv"
    if not home_venv.exists():
        return
    # Windows: Lib/site-packages  |  Unix: lib/python*/site-packages
    candidates = list(home_venv.glob("Lib/site-packages"))
    candidates += list(home_venv.glob("lib/python*/site-packages"))
    for sp in candidates:
        sp_str = str(sp)
        if sp_str not in sys.path:
            sys.path.insert(1, sp_str)
            break


_ensure_home_venv_on_path()

# ---------------------------------------------------------------------------
# Availability guard
# ---------------------------------------------------------------------------

class VnstockDataUnavailableError(Exception):
    """Raised when vnstock_data is not installed in the active environment."""


def _require_vnstock_data():
    """Return the vnstock_data module or raise VnstockDataUnavailableError."""
    try:
        return importlib.import_module("vnstock_data")
    except ImportError as exc:
        raise VnstockDataUnavailableError(
            "vnstock_data is not installed. "
            "Install it in your ~/.venv to enable VN market data: "
            "pip install vnstock_data"
        ) from exc


def _get_version() -> str:
    """Return installed vnstock_data version string, or '0.0.0' if unknown."""
    try:
        vnd = importlib.import_module("vnstock_data")
        ver = getattr(vnd, "__version__", None)
        if ver:
            return ver
        from importlib.metadata import version
        return version("vnstock_data")
    except Exception:
        return "0.0.0"


def _assert_unified_ui():
    """Ensure vnstock_data >= 3.0.0 (Unified UI) is installed."""
    ver = _get_version()
    # Simple string prefix check — avoids packaging dependency
    major = int(ver.split(".")[0]) if ver[0].isdigit() else 0
    if major < 3:
        raise VnstockDataUnavailableError(
            f"vnstock_data {ver} is too old. Upgrade: pip install -U vnstock_data"
        )


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

import re as _re


def _parse_article_datetime(raw) -> Optional[datetime]:
    """Parse ngày publish của bài crawl RSS (vnstock_news / feedparser).

    Field ``publish_time`` là RFC-822 (vd 'Sat, 04 Jul 2026 21:45:00 +07'),
    KHÔNG phải ISO-8601 — ``datetime.fromisoformat()`` fail 100% trên format
    này (đã verify: toàn bộ 60/60 bài trong 1 lần crawl bị parse fail), khiến
    mọi bài bị coi "undated" và vô hiệu hoá filter theo ticker phía dưới.

    Offset trong dữ liệu này chỉ có 2 chữ số ('+07') thay vì chuẩn RFC-822
    4 chữ số ('+0700') — nếu đưa thẳng vào ``email.utils.parsedate_to_datetime``
    sẽ bị hiểu sai thành +00:07 (7 PHÚT) chứ không phải +07:00 (giờ VN). Chuẩn
    hoá offset trước khi parse. Trả về datetime NAIVE (giờ địa phương VN, đã
    bỏ tzinfo) để so sánh thẳng với ngày dạng YYYY-MM-DD.
    """
    if not raw:
        return None
    raw = str(raw).strip()
    normalized = _re.sub(r"([+-]\d{2})$", r"\g<1>00", raw)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(normalized)
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    except Exception:
        pass
    try:
        return datetime.fromisoformat(raw[:19])
    except Exception:
        return None


def _parse_quarter_period(series):
    """Parse 'YYYY-Qn' period strings to their estimated publication dates.

    CRITICAL — avoids look-ahead bias: financial statements are published
    AFTER the quarter ends. Using the quarter start date (Jan 1 for Q1)
    incorrectly includes data that wasn't available at the analysis date.

    Vietnamese regulatory publication deadlines:
      Q1 (Jan–Mar): available by Apr 30  (30-day deadline for listed cos.)
      Q2 (Apr–Jun): available by Jul 31
      Q3 (Jul–Sep): available by Oct 31
      Q4 (Oct–Dec): available by Feb 28 next year (annual audit required)

    Example: analysis date = 2026-01-28
      '2026-Q1' → pub date May 15, 2026  → Jan 28 < May 15 → EXCLUDED ✓
      '2025-Q4' → pub date Feb 28, 2026  → Jan 28 < Feb 28 → EXCLUDED ✓
      '2025-Q3' → pub date Oct 31, 2025  → Jan 28 > Oct 31 → INCLUDED ✓
    """
    import pandas as _pd

    _QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    _PUB_LAG_DAYS = {1: 30, 2: 31, 3: 31, 4: 60}  # days after quarter end

    def _one(s):
        m = _re.match(r"(\d{4})-Q(\d)", str(s))
        if m:
            yr, q = int(m.group(1)), int(m.group(2))
            end_month, end_day = _QUARTER_END[q]
            quarter_end = _pd.Timestamp(yr, end_month, end_day)
            return quarter_end + _pd.DateOffset(days=_PUB_LAG_DAYS[q])
        return _pd.to_datetime(s, errors="coerce")

    return series.apply(_one)


def _financial_to_billion(df):
    """Convert raw-VND financial statement values to tỷ đồng (VND billion).

    vnstock_data balance sheet / income statement / cash flow return values
    in raw VND (đồng). Dividing by 1e9 produces tỷ đồng, matching the scale
    analysts expect (e.g. 870,000 tỷ total assets for VHM Q1 2026).
    Only applies to numeric columns; non-numeric (period, labels) are kept.
    """
    df = df.copy()
    for col in df.columns:
        if col == "period":
            continue
        try:
            df[col] = df[col].astype(float) / 1_000_000_000
        except (TypeError, ValueError):
            pass  # skip non-numeric columns
    return df


def _filter_and_limit_periods(df, curr_date: str, n: int = 4):
    """Filter rows to curr_date and return the n most-recent periods.

    Handles the 'YYYY-Qn' period format that pd.to_datetime cannot parse.
    Data from vnstock_data is sorted newest-first; head(n) after ensuring
    date ≤ cutoff returns the n most recent valid periods.
    """
    import pandas as _pd
    date_col = next(
        (c for c in df.columns
         if c in ("period",) or "date" in c.lower() or "time" in c.lower()),
        None,
    )
    if date_col is None:
        return df.head(n)

    parsed = _parse_quarter_period(df[date_col])
    cutoff = _pd.Timestamp(curr_date)
    mask = parsed.notna() & (parsed <= cutoff)
    filtered = df[mask].copy()
    if filtered.empty:
        # Never fall back to unfiltered data — that would inject future periods
        # and violate look-ahead-bias safeguards. Return empty instead.
        return filtered
    # Data arrives newest-first; head(n) gives n most-recent periods.
    return filtered.head(n)


def _df_to_str(df, header: str = "") -> str:
    """Convert a DataFrame to a readable CSV string with an optional header."""
    if df is None or df.empty:
        return header + "No data available.\n"
    csv = df.to_csv()
    return header + csv


def _parse_freq(freq: str) -> str:
    """Map TradingAgents freq ('annual'/'quarterly') to vnstock_data period."""
    mapping = {
        "annual": "year",
        "yearly": "year",
        "year": "year",
        "quarterly": "quarter",
        "quarter": "quarter",
        "q": "quarter",
        "a": "year",
    }
    return mapping.get((freq or "quarterly").strip().lower(), "quarter")


# ---------------------------------------------------------------------------
# Core stock data
# ---------------------------------------------------------------------------

def get_stock_data(
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch OHLCV price history for a VN equity via vnstock_data Unified UI.

    Args:
        symbol:     VN ticker, e.g. "VCB", "FPT".
        start_date: ISO date string, "YYYY-MM-DD".
        end_date:   ISO date string, "YYYY-MM-DD".

    Returns:
        CSV-formatted string of OHLCV data with a descriptive header.

    Raises:
        VnstockDataUnavailableError: if vnstock_data is not installed.
    """
    _require_vnstock_data()
    _assert_unified_ui()

    from vnstock_data import Market  # type: ignore

    mkt = Market()
    try:
        df = mkt.equity(symbol.upper()).ohlcv(start=start_date, end=end_date)
    except Exception as exc:
        return (
            f"vnstock_data could not fetch OHLCV for {symbol} "
            f"({start_date} to {end_date}): {exc}"
        )

    # Get the last close for a quick reference note
    last_close = df["close"].iloc[-1] if not df.empty else "N/A"
    last_date  = str(df["time"].iloc[-1])[:10] if not df.empty else "N/A"

    header = (
        f"# VN stock data for {symbol.upper()} from {start_date} to {end_date}\n"
        f"# Source: vnstock_data (Unified UI)\n"
        f"# Total records: {len(df)}\n"
        f"# IMPORTANT — PRICE UNIT: all price columns (open/high/low/close) are in\n"
        f"#   THOUSANDS of VND (nghìn đồng). Multiply by 1,000 for the actual VND price.\n"
        f"#   Example: close=62.0 means 62,000 VND per share (NOT 62 VND).\n"
        f"#   Latest close: {last_close} (= {float(last_close)*1000:,.0f} VND/share) on {last_date}\n\n"
    )
    return _df_to_str(df, header)


# ---------------------------------------------------------------------------
# Technical indicators (best-effort via vnstock_ta or yfinance fallback hint)
# ---------------------------------------------------------------------------

def _compute_vn_ta_indicator(ta, indicator: str):
    """Map stockstats-style indicator names to vnstock_ta category API."""
    _map = {
        "rsi":          lambda: ta.momentum.rsi(length=14),
        "macd":         lambda: ta.momentum.macd(),
        "macdh":        lambda: ta.momentum.macd(),
        "macds":        lambda: ta.momentum.macd(),
        "close_50_sma": lambda: ta.trend.sma(length=50),
        "close_200_sma":lambda: ta.trend.sma(length=200),
        "close_10_ema": lambda: ta.trend.ema(length=10),
        "sma":          lambda: ta.trend.sma(length=20),
        "ema":          lambda: ta.trend.ema(length=20),
        "boll":         lambda: ta.volatility.bbands(),
        "boll_ub":      lambda: ta.volatility.bbands(),
        "boll_lb":      lambda: ta.volatility.bbands(),
        "atr":          lambda: ta.volatility.atr(length=14),
        "obv":          lambda: ta.volume.obv(),
        "vwma":         lambda: ta.volume.obv(),
    }
    fn = _map.get(indicator)
    return fn() if fn else None


def _compute_indicator_pandas(df, indicator: str):
    """Compute technical indicator from OHLCV using pure pandas — always works.

    Used as fallback when vnstock_ta is unavailable or raises.
    df must have columns: open, high, low, close, volume
    """
    import pandas as pd
    import numpy as np

    close  = df["close"].astype(float).reset_index(drop=True)
    high   = df["high"].astype(float).reset_index(drop=True)
    low    = df["low"].astype(float).reset_index(drop=True)
    volume = df["volume"].astype(float).reset_index(drop=True)
    ind = indicator.lower()

    if ind == "rsi":
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        return pd.Series(100 - 100 / (1 + rs), name="rsi")

    elif ind in ("macd", "macdh", "macds"):
        ema12  = close.ewm(span=12, adjust=False).mean()
        ema26  = close.ewm(span=26, adjust=False).mean()
        macd_l = ema12 - ema26
        signal = macd_l.ewm(span=9, adjust=False).mean()
        return pd.DataFrame({"macd": macd_l, "macds": signal, "macdh": macd_l - signal})

    elif ind == "close_50_sma":
        return close.rolling(50).mean().rename("close_50_sma")

    elif ind == "close_200_sma":
        return close.rolling(200).mean().rename("close_200_sma")

    elif ind == "close_10_ema":
        return close.ewm(span=10, adjust=False).mean().rename("close_10_ema")

    elif ind == "sma":
        return close.rolling(20).mean().rename("sma_20")

    elif ind == "ema":
        return close.ewm(span=20, adjust=False).mean().rename("ema_20")

    elif ind in ("boll", "boll_ub", "boll_lb"):
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        return pd.DataFrame({"boll": mid, "boll_ub": mid + 2*std, "boll_lb": mid - 2*std})

    elif ind == "atr":
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low  - close.shift()).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=14, adjust=False).mean().rename("atr")

    elif ind == "obv":
        direction = np.sign(close.diff()).fillna(0)
        return (direction * volume).cumsum().rename("obv")

    elif ind == "vwma":
        return ((close * volume).rolling(20).sum() / volume.rolling(20).sum()).rename("vwma")

    return None


def get_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 30,
) -> str:
    """Compute a technical indicator for a VN equity.

    Attempts to use vnstock_ta if installed; otherwise fetches raw OHLCV from
    vnstock_data and returns it with a note that the caller should compute the
    indicator manually or install vnstock_ta.

    Args:
        symbol:         VN ticker.
        indicator:      Indicator name (e.g. "rsi", "macd", "sma").
        curr_date:      Reference date "YYYY-MM-DD".
        look_back_days: Window size in calendar days.

    Returns:
        String report with indicator values or raw OHLCV as fallback.
    """
    _require_vnstock_data()
    _assert_unified_ui()

    from vnstock_data import Market  # type: ignore

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    # Warm-up đủ cho rolling dài nhất (SMA200 cần 200 phiên ~ 290 ngày lịch) +
    # cửa sổ hiển thị. Indicator tính trên full series, chỉ .tail(look_back_days) khi trả về.
    start_dt = end_dt - timedelta(days=look_back_days + 380)
    start_date = start_dt.strftime("%Y-%m-%d")

    try:
        mkt = Market()
        df = mkt.equity(symbol.upper()).ohlcv(start=start_date, end=curr_date)
    except Exception as exc:
        return f"vnstock_data could not fetch data for indicator '{indicator}' on {symbol}: {exc}"

    if df is None or df.empty:
        return f"No price data available for {symbol} to compute '{indicator}'."

    import pandas as pd
    ind_lower = indicator.strip().lower()

    # Build base DataFrame with time + close for output
    time_col = "time" if "time" in df.columns else df.columns[0]
    base = df[[time_col, "open", "high", "low", "close", "volume"]].reset_index(drop=True)

    result = None
    source_tag = "pandas"

    # Attempt vnstock_ta first (more accurate, handles edge cases)
    try:
        from vnstock_ta import Indicator  # type: ignore
        ta = Indicator(data=df)
        result = _compute_vn_ta_indicator(ta, ind_lower)
        if result is not None:
            source_tag = "vnstock_ta"
    except Exception:
        pass

    # Fallback: pure-pandas computation — works for any OHLCV
    if result is None:
        result = _compute_indicator_pandas(df, ind_lower)

    if result is not None:
        result_df = base[[time_col, "close"]].copy()
        if hasattr(result, "columns"):
            for col in result.columns:
                result_df[col] = result[col].values
        else:
            result_df[result.name if result.name else ind_lower] = result.values
        header = (
            f"# Indicator '{indicator}' for {symbol.upper()}\n"
            f"# Lookback: {look_back_days} sessions ending {curr_date}\n"
            f"# Source: {source_tag}\n"
            f"# Price unit: thousands VND (close=157.7 means 157,700 VND/share)\n\n"
        )
        return header + result_df.tail(look_back_days).to_csv(index=False)

    # Only reached for truly unknown indicator names
    header = (
        f"# OHLCV for {symbol.upper()} — indicator '{indicator}' not recognised\n"
        f"# Available: rsi, macd, close_50_sma, close_200_sma, close_10_ema,\n"
        f"#            boll/boll_ub/boll_lb, atr, obv, vwma\n\n"
    )
    return header + base.tail(look_back_days).to_csv(index=False)


# ---------------------------------------------------------------------------
# Fundamental data
# ---------------------------------------------------------------------------

def _compute_ttm_metrics(sym: str, curr_date: str, fun) -> str:
    """Compute Trailing Twelve Month (TTM) profitability metrics.

    CRITICAL: These metrics MUST use 4-quarter sums for income items, not
    a single quarter. Using one quarter and treating it as annual understates
    ROE by ~4x and leads to completely wrong valuation conclusions.

    Metrics computed here are authoritative — the LLM must NOT recompute
    them from raw quarterly figures.
    """
    try:
        # ── Income statement: sum 4 quarters for TTM ──────────────────────
        is_df = fun.equity(sym).income_statement(period="quarter")
        is_df = _filter_and_limit_periods(is_df, curr_date, n=4)
        is_df = _financial_to_billion(is_df)

        # ── Balance sheet: latest quarter for point-in-time items ─────────
        bs_df = fun.equity(sym).balance_sheet(period="quarter")
        bs_df = _filter_and_limit_periods(bs_df, curr_date, n=1)
        bs_df = _financial_to_billion(bs_df)

        if is_df.empty or bs_df.empty:
            return "## TTM Profitability Metrics\nInsufficient data.\n"

        periods_used = is_df["period"].tolist() if "period" in is_df.columns else []

        def _ttm(col):
            return is_df[col].sum() if col in is_df.columns else None

        def _latest(df, col):
            return df[col].iloc[0] if col in df.columns and not df.empty else None

        ttm_net    = _ttm("net_profit_after_tax")
        ttm_rev    = _ttm("net_revenue")
        ttm_op     = _ttm("operating_profit")
        equity     = _latest(bs_df, "owners_equity")
        assets     = _latest(bs_df, "total_assets")

        lines = [
            "## ⚠ PRE-COMPUTED TTM PROFITABILITY METRICS — USE THESE, DO NOT RECOMPUTE",
            f"# Periods summed: {', '.join(periods_used)} (Trailing Twelve Months)",
            f"# UNIT: tỷ đồng (VND billion)",
            "",
        ]

        if ttm_net is not None and equity and equity > 0:
            roe = ttm_net / equity
            lines.append(f"ROE  (TTM, annualized) : {roe:.2%}   ← USE THIS, not quarterly figure")
        if ttm_net is not None and assets and assets > 0:
            roa = ttm_net / assets
            lines.append(f"ROA  (TTM, annualized) : {roa:.2%}")
        if ttm_net is not None and ttm_rev and ttm_rev > 0:
            npm = ttm_net / ttm_rev
            lines.append(f"Net Profit Margin (TTM): {npm:.2%}")
        if ttm_op is not None and ttm_rev and ttm_rev > 0:
            opm = ttm_op / ttm_rev
            lines.append(f"Operating Margin  (TTM): {opm:.2%}")

        lines += [
            "",
            f"TTM Net Income    : {ttm_net:,.1f} tỷ  (sum of {len(is_df)} quarters)" if ttm_net else "",
            f"TTM Revenue       : {ttm_rev:,.1f} tỷ" if ttm_rev else "",
            f"TTM Operating P&L : {ttm_op:,.1f} tỷ" if ttm_op else "",
            f"Latest Equity     : {equity:,.1f} tỷ  (point-in-time, {bs_df['period'].iloc[0] if 'period' in bs_df.columns else ''})" if equity else "",
            f"Latest Total Assets: {assets:,.1f} tỷ" if assets else "",
            "",
            "⚠ WARNING: Do NOT divide a single quarter's net income by equity to get ROE.",
            "   Doing so underestimates ROE by ~4x and leads to wrong valuation conclusions.",
        ]

        return "\n".join(l for l in lines if l is not None) + "\n"

    except Exception as exc:
        return f"## TTM Profitability Metrics\nError: {exc}\n"


def get_fundamentals(ticker: str, curr_date: str) -> str:
    """Fetch key financial ratios and fundamental metrics for a VN equity.

    Args:
        ticker:    VN ticker symbol.
        curr_date: Reference date "YYYY-MM-DD" (used to filter historical data).

    Returns:
        Formatted string with fundamental metrics.
    """
    _require_vnstock_data()
    _assert_unified_ui()

    from vnstock_data import Fundamental  # type: ignore

    sym = ticker.upper()
    sections: list[str] = [
        f"# Fundamental data for {sym} as of {curr_date}\n"
        f"# Source: vnstock_data (Unified UI)\n\n"
    ]

    fun = Fundamental()

    sections.append(
        "## UNIT NOTES — READ CAREFULLY BEFORE INTERPRETING ANY NUMBER\n"
        "- Stock price (OHLCV): THOUSANDS of VND. close=68.8 → 68,800 VND/share.\n"
        "- EPS, BVPS (from ratio section): full VND per share. EPS=4,184 → 4,184 VND/share (NOT 4,184 tỷ).\n"
        "- P/E, P/B (from ratio section): UNITLESS RATIOS. pe=6.61 means price is 6.61× earnings.\n"
        "- Balance sheet / Income statement / Cash flow: TỶ ĐỒNG (VND billion). 869,974 → 869,974 tỷ.\n"
        "- TTM metrics below are pre-computed — use them directly, do NOT recompute from raw quarterly data.\n\n"
    )

    import pandas as pd

    # TTM metrics — computed first, placed at top so LLM sees them before raw data
    sections.append(_compute_ttm_metrics(sym, curr_date, fun))

    # Financial health scorecard — auto-detects bank / securities / generic
    try:
        from vnstock_data.ui.fundamental import Fundamental as _FunUI  # type: ignore
        df_health = _FunUI(source="mas").equity(sym).financial_health(
            scorecard="auto", lang="vi", limit=8  # fetch extra so filter has room
        )
        if df_health is not None and not df_health.empty:
            df_health = _filter_and_limit_periods(df_health, curr_date, n=4)
            if not df_health.empty:
                sections.append(
                    "## Financial Health Scorecard (industry-adjusted, VAS) — 4 most recent periods\n"
                    + df_health.to_csv(index=False) + "\n"
                )
    except Exception as exc:
        sections.append(f"## Financial Health Scorecard\nNot available: {exc}\n")

    # Financial ratios — limit to 8 most recent periods to prevent LLM using stale data
    try:
        df_ratio = fun.equity(sym).ratio()
        if df_ratio is not None and not df_ratio.empty:
            # Use _filter_and_limit_periods to handle 'YYYY-Qn' format correctly
            df_ratio = _filter_and_limit_periods(df_ratio, curr_date, n=4)
            # Snapshot of the single most recent period (data is newest-first → row 0)
            latest = df_ratio.iloc[[0]]
            sections.append(
                "## Key Ratios — Most Recent Period (use these for current valuation)\n"
                + latest.to_csv(index=False) + "\n"
            )
            sections.append(
                "## Financial Ratios — Last 8 Periods\n"
                + df_ratio.to_csv(index=False) + "\n"
            )
    except Exception as exc:
        sections.append(f"## Financial Ratios\nNot available: {exc}\n")

    return "\n".join(sections)


def get_balance_sheet(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Fetch balance sheet data for a VN equity.

    Args:
        ticker:    VN ticker symbol.
        freq:      "annual" or "quarterly".
        curr_date: Optional reference date for filtering.

    Returns:
        Formatted string with balance sheet data.
    """
    _require_vnstock_data()
    _assert_unified_ui()

    from vnstock_data import Fundamental  # type: ignore
    import pandas as pd

    sym = ticker.upper()
    period = _parse_freq(freq)

    try:
        fun = Fundamental()
        df = fun.equity(sym).balance_sheet(period=period)
    except Exception as exc:
        return f"# Balance sheet for {sym}\nNot available: {exc}\n"

    if df is not None and not df.empty:
        df = _filter_and_limit_periods(df, curr_date or "2099-12-31", n=8)
        df = _financial_to_billion(df)

    header = (
        f"# Balance sheet for {sym} ({freq}) — 8 most recent periods\n"
        f"# UNIT: tỷ đồng (VND billion). Multiply by 1,000 for VND million.\n"
        f"# Source: vnstock_data\n\n"
    )
    return _df_to_str(df, header)


def get_cashflow(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Fetch cash flow statement for a VN equity.

    Args:
        ticker:    VN ticker symbol.
        freq:      "annual" or "quarterly".
        curr_date: Optional reference date for filtering.

    Returns:
        Formatted string with cash flow data.
    """
    _require_vnstock_data()
    _assert_unified_ui()

    from vnstock_data import Fundamental  # type: ignore
    import pandas as pd

    sym = ticker.upper()
    period = _parse_freq(freq)

    try:
        fun = Fundamental()
        df = fun.equity(sym).cash_flow(period=period)
    except Exception as exc:
        return f"# Cash flow for {sym}\nNot available: {exc}\n"

    if df is not None and not df.empty:
        df = _filter_and_limit_periods(df, curr_date or "2099-12-31", n=8)
        df = _financial_to_billion(df)

    header = (
        f"# Cash flow for {sym} ({freq}) — 8 most recent periods\n"
        f"# UNIT: tỷ đồng (VND billion). Multiply by 1,000 for VND million.\n"
        f"# Source: vnstock_data\n\n"
    )
    return _df_to_str(df, header)


def get_income_statement(
    ticker: str,
    freq: str = "quarterly",
    curr_date: Optional[str] = None,
) -> str:
    """Fetch income statement for a VN equity.

    Args:
        ticker:    VN ticker symbol.
        freq:      "annual" or "quarterly".
        curr_date: Optional reference date for filtering.

    Returns:
        Formatted string with income statement data.
    """
    _require_vnstock_data()
    _assert_unified_ui()

    from vnstock_data import Fundamental  # type: ignore
    import pandas as pd

    sym = ticker.upper()
    period = _parse_freq(freq)

    try:
        fun = Fundamental()
        df = fun.equity(sym).income_statement(period=period)
    except Exception as exc:
        return f"# Income statement for {sym}\nNot available: {exc}\n"

    if df is not None and not df.empty:
        df = _filter_and_limit_periods(df, curr_date or "2099-12-31", n=8)
        df = _financial_to_billion(df)

    header = (
        f"# Income statement for {sym} ({freq}) — 8 most recent periods\n"
        f"# UNIT: tỷ đồng (VND billion). Multiply by 1,000 for VND million.\n"
        f"# Source: vnstock_data\n\n"
    )
    return _df_to_str(df, header)


# ---------------------------------------------------------------------------
# News — VN-specific
# ---------------------------------------------------------------------------

# Working vnstock_news sites (verified: cafebiz, vietstock have RSS feeds)
_VN_NEWS_SITES = ["cafebiz", "vietstock", "vnexpress"]


def _crawl_vn_news(limit_per_feed: int = 20) -> list:
    """Fetch articles from working VN financial news sites via vnstock_news.

    Tries each site in priority order and returns combined article list.
    Silently skips any site that errors — degrades to fewer sources, not failure.
    """
    from vnstock_news import Crawler  # type: ignore
    articles: list = []
    for site in _VN_NEWS_SITES:
        try:
            batch = Crawler(site_name=site).get_articles_from_feed(
                limit_per_feed=limit_per_feed
            ) or []
            articles.extend(batch)
        except Exception:
            continue
    return articles


def get_news(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch news for a VN equity.

    Uses vnstock_news if available; otherwise falls back to a polite error
    message so the caller can cascade to yfinance.

    Args:
        ticker:     VN ticker symbol.
        start_date: "YYYY-MM-DD"
        end_date:   "YYYY-MM-DD"

    Returns:
        Formatted news string.
    """
    _require_vnstock_data()

    sym = ticker.upper()

    # Attempt vnstock_news (Crawler API) — try financial sites in priority order
    # NOTE: RSS/crawler fetches CURRENT news only, not historical archives.
    # For historical analysis dates, treat news as approximate context only.
    try:
        from vnstock_news import Crawler  # type: ignore
        articles = _crawl_vn_news(limit_per_feed=20)

        # Task 4B: RSS crawler chỉ trả tin HIỆN TẠI. Khi backtest (ContextVar set),
        # clamp upper bound về trade_date để không lấy tin sau ngày phân tích.
        from tradingagents.dataflows.run_context import get_trade_date
        _bt_date = get_trade_date()
        _is_backtest = _bt_date is not None
        end_cap = min(end_date, _bt_date) if _is_backtest else end_date

        # Best-effort date filter: keep articles with published date ≤ end_cap.
        # Dùng _parse_article_datetime (RFC-822 + chuẩn hoá offset) — trước đây
        # dùng fromisoformat() nên 100% bài bị coi "undated" (publish_time thực
        # tế là RFC-822, không phải ISO), khiến filter theo ticker dưới đây
        # luôn thấy `dated` rỗng và rơi vào nhánh fallback.
        end_dt_obj = datetime.strptime(end_cap, "%Y-%m-%d")
        def _article_date(a) -> Optional[datetime]:
            raw = a.get("publish_time") or a.get("published") or a.get("date") or ""
            return _parse_article_datetime(raw)

        dated   = [a for a in articles if _article_date(a) is not None and _article_date(a) <= end_dt_obj]
        undated = [a for a in articles if _article_date(a) is None]

        # Candidate pool để tìm ticker khác nhau theo mode:
        # - Backtest: CHỈ `dated` (đã verify ngày ≤ cutoff) — bài "undated" có
        #   thể là tin xuất bản SAU trade_date, đưa vào sẽ rò rỉ tương lai.
        # - Production: `dated + undated` — không có rủi ro rò rỉ, tìm rộng
        #   trên toàn bộ crawl để không bỏ lỡ tin ticker chỉ vì thiếu ngày.
        candidates = dated if _is_backtest else (dated + undated)
        relevant = [a for a in candidates if sym in str(a.get("title", "")).upper()]

        if not relevant:
            if _is_backtest:
                # Backtest: KHÔNG bơm tin hiện tại làm fallback (rò rỉ tương lai).
                # RSS live không có kho lịch sử ⇒ báo thiếu, để agent dựa vào FA/TA.
                return (
                    f"# News for {sym} (target window: {start_date} to {end_cap})\n"
                    f"# vnstock_news RSS chỉ có tin hiện tại — KHÔNG có tin lịch sử ≤ {end_cap}.\n"
                    f"# Backtest mode: bỏ qua fallback tin hiện tại để tránh data leakage.\n"
                    f"# Dựa vào phân tích cơ bản và kỹ thuật.\n"
                )
            # Production: KHÔNG bơm "top N bất kỳ chủ đề" làm fallback nữa —
            # đã gây báo cáo lẫn tin macro/vàng thế giới hoàn toàn không liên
            # quan ticker (vd PNJ), khiến agent hiểu lầm đó là tin về PNJ và
            # bỏ lỡ tin thật (vụ giám đốc bị bắt, cổ phiếu bị bán tháo) không
            # nằm trong 60 bài crawl từ cafebiz/vietstock/vnexpress. Báo rõ
            # KHÔNG tìm thấy, để agent dựa vào get_marketwire_news/FA/TA thay
            # vì tự suy diễn từ tin không liên quan.
            return (
                f"# News for {sym} (target window: {start_date} to {end_cap})\n"
                f"# Không tìm thấy tin nào chứa '{sym}' trong {len(candidates)} bài "
                f"crawl gần nhất (cafebiz/vietstock/vnexpress).\n"
                f"# Nguồn RSS này KHÔNG có cafef.vn — nếu tin quan trọng chỉ đăng ở "
                f"cafef, sẽ không xuất hiện ở đây.\n"
                f"# Dựa vào phân tích cơ bản/kỹ thuật; get_marketwire_news hoặc "
                f"get_global_news có thể có thêm ngữ cảnh vĩ mô (KHÔNG phải tin "
                f"riêng về {sym}).\n"
            )

        lines = [
            f"[{a.get('publish_time') or a.get('published', 'date unknown')}] {a.get('title', 'No title')}"
            for a in relevant[:15]
        ]
        note = ("⚠ News source provides current articles only; "
                f"historical filtering applied where publication date available.\n")
        header = (
            f"# News for {sym} (target window: {start_date} to {end_cap})\n"
            f"# Source: vnstock_news Crawler ({len(relevant)} articles)\n"
            f"# {note}\n"
        )
        return header + "\n".join(lines)
    except ImportError:
        pass
    except Exception as exc:
        return f"# News for {sym}\nvnstock_news error: {exc}\n"

    # CRITICAL: Do NOT fall back to yfinance for VN tickers.
    # Plain ticker "MBB" on yfinance resolves to iShares MBS ETF (US), not
    # Military Bank Vietnam. "VNM" resolves to VanEck Vietnam ETF, etc.
    # Return a structured placeholder so the LLM knows news is unavailable
    # but does NOT fabricate content from wrong-market sources.
    return (
        f"# News for {sym} (Vietnamese equity, HOSE/HNX listed)\n"
        f"# vnstock_news data unavailable for this ticker.\n"
        f"# WARNING: Do NOT use yfinance/Yahoo Finance data for ticker '{sym}' —\n"
        f"#   '{sym}' on US markets is a DIFFERENT security (e.g. MBB = iShares MBS ETF).\n"
        f"# Rely on fundamental and technical analysis instead.\n"
    )


def get_vn_sentiment(ticker: str, curr_date: str, look_back_days: int = 20) -> str:
    """Build a VN-specific sentiment block from price-band behavior + news.

    Combines three signals without duplicating TA indicator work:
      1. Ceiling/floor hit counts  → crowd psychology (distinct from RSI/MACD)
      2. Volume anomaly detection  → institutional vs retail behavior
      3. CafeF headlines + vnstock_news (best-effort, degrades gracefully)

    Args:
        ticker:         VN ticker, e.g. "VCB".
        curr_date:      Reference date "YYYY-MM-DD".
        look_back_days: Window for price-band and volume analysis.
    """
    _require_vnstock_data()
    sym = ticker.upper()
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days + 10)

    # ── 1. Price-band sentiment from OHLCV ──────────────────────────────────
    price_block = "Price-band sentiment: data unavailable."
    try:
        from vnstock_data import Market
        df = Market().equity(sym).ohlcv(
            start=start_dt.strftime("%Y-%m-%d"), end=curr_date
        )
        if df is not None and not df.empty:
            import pandas as pd
            df = df.tail(look_back_days).copy()
            n = len(df)
            # Ceiling hit: close ≈ high AND gain ≥ +6.5% (within ±0.5% of +7% band)
            df["pct_chg"] = df["close"].pct_change() * 100
            ceiling_hits = int((df["pct_chg"] >= 6.5).sum())
            floor_hits   = int((df["pct_chg"] <= -6.5).sum())
            # Consecutive ceiling/floor at end of window
            recent = df.tail(5)["pct_chg"]
            consec_ceil  = int(recent.ge(6.5).sum())
            consec_floor = int(recent.le(-6.5).sum())
            # Volume anomaly: days with volume > 2× rolling 20-day mean
            vol_mean = df["volume"].mean()
            vol_spikes = int((df["volume"] > 2 * vol_mean).sum())
            last_close = float(df["close"].iloc[-1])
            last_vol   = int(df["volume"].iloc[-1])
            vol_ratio  = last_vol / vol_mean if vol_mean > 0 else 1.0

            price_block = (
                f"## Price-Band Sentiment ({sym}, last {n} sessions ending {curr_date})\n"
                f"NOTE: prices in thousands VND — last close {last_close} = {last_close*1000:,.0f} VND/share\n\n"
                f"**Ceiling hits (gia tran, ≥+6.5%)**: {ceiling_hits}/{n} sessions"
                + (f" — incl. {consec_ceil}/5 in last 5 sessions (STRONG BULLISH retail rush)\n"
                   if consec_ceil >= 2 else "\n")
                + f"**Floor hits (gia san, ≤-6.5%)**: {floor_hits}/{n} sessions"
                + (f" — incl. {consec_floor}/5 in last 5 sessions (PANIC / margin-call cascade)\n"
                   if consec_floor >= 2 else "\n")
                + f"**Volume spikes (>2× avg)**: {vol_spikes}/{n} sessions\n"
                + f"**Latest session volume**: {last_vol:,} ({vol_ratio:.1f}× 20-day avg)\n\n"
                + _interpret_price_band(ceiling_hits, floor_hits, consec_ceil, consec_floor,
                                        vol_ratio, n)
            )
    except Exception as exc:
        price_block = f"Price-band sentiment: error — {exc}"

    # ── 2. Dòng tiền khối ngoại (NN ròng) — tín hiệu sentiment mạnh nhất TT VN ──
    foreign_block = _fetch_foreign_flow(sym, curr_date, look_back_days)

    # ── 3. Tin tức theo mã (vnstock_data Company.news — theo đúng ticker) ──────
    company_news_block = _fetch_vn_company_news(sym)

    # ── 4. CafeF / Google News RSS (bổ sung, best-effort) ─────────────────────
    cafef_block = _fetch_cafef_headlines(sym)

    return (
        f"# VN Sentiment Data for {sym} — {curr_date}\n\n"
        f"{price_block}\n\n"
        f"{foreign_block}\n\n"
        f"{company_news_block}\n\n"
        f"## CafeF / Google News Headlines\n{cafef_block}\n"
    )


def _fetch_foreign_flow(sym: str, curr_date: str, look_back_days: int = 20) -> str:
    """Tóm tắt dòng tiền khối ngoại (NN mua/bán ròng) — best-effort, fallback source."""
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days + 16)
    for src in ("VCI", "KBS"):
        try:
            from vnstock_data import Trading
            import pandas as pd
            fgn = Trading(symbol=sym, source=src).foreign_trade(
                start=start_dt.strftime("%Y-%m-%d"), end=curr_date
            )
            if fgn is None or fgn.empty or "fr_net_value_total" not in fgn.columns:
                continue
            fgn = fgn.sort_values("trading_date").tail(look_back_days)
            net = pd.to_numeric(fgn["fr_net_value_total"], errors="coerce").fillna(0) / 1e9  # tỷ đ
            n = len(net)
            total_net = float(net.sum())
            last5 = float(net.tail(5).sum())
            buy_days = int((net > 0).sum())
            sell_days = int((net < 0).sum())
            own_txt = ""
            if "fr_owned_percentage" in fgn.columns:
                own = pd.to_numeric(fgn["fr_owned_percentage"], errors="coerce").dropna() * 100
                if len(own) >= 2:
                    own_txt = f"\n- Sở hữu NN: {own.iloc[-1]:.2f}% ({own.iloc[-1] - own.iloc[0]:+.2f} điểm % trong kỳ)"

            if total_net > 0 and last5 >= 0:
                label = "TÍCH CỰC — khối ngoại MUA RÒNG, dòng tiền ngoại đang vào"
            elif total_net < 0 and last5 <= 0:
                label = "TIÊU CỰC — khối ngoại BÁN RÒNG, dòng tiền ngoại đang rút"
            elif last5 > 0:
                label = "ĐANG CẢI THIỆN — NN quay lại mua ròng 5 phiên gần nhất"
            elif last5 < 0:
                label = "ĐANG XẤU ĐI — NN bán ròng 5 phiên gần nhất dù luỹ kế còn dương"
            else:
                label = "TRUNG LẬP — dòng tiền NN cân bằng"

            return (
                f"## Dòng tiền khối ngoại (NN, {n} phiên gần nhất)\n"
                f"- NN ròng luỹ kế: **{total_net:+,.1f} tỷ đồng** "
                f"({buy_days} phiên mua ròng / {sell_days} phiên bán ròng)\n"
                f"- 5 phiên gần nhất: {last5:+,.1f} tỷ đồng{own_txt}\n"
                f"- **Tín hiệu NN**: {label}\n"
            )
        except Exception:
            continue
    return "## Dòng tiền khối ngoại\nKhông lấy được dữ liệu khối ngoại."


def _fetch_vn_company_news(sym: str, n: int = 12) -> str:
    """Tin tức theo đúng mã từ vnstock_data Company.news (title + ngày + nguồn)."""
    for src in ("VCI", "KBS"):
        try:
            from vnstock_data import Company
            df = Company(symbol=sym, source=src).news()
            if df is None or df.empty:
                continue
            tcol = next((c for c in ("news_title", "friendly_title", "title") if c in df.columns), None)
            dcol = next((c for c in ("public_date", "publish_time", "date") if c in df.columns), None)
            if not tcol:
                continue
            if dcol:
                import pandas as pd
                df = df.copy()
                df[dcol] = pd.to_datetime(df[dcol], errors="coerce")
                df = df.sort_values(dcol, ascending=False)
            lines = []
            for _, r in df.head(n).iterrows():
                title = str(r.get(tcol) or "").strip()
                if not title:
                    continue
                d = str(r.get(dcol))[:10] if dcol else ""
                src_name = str(r.get("news_source") or "").strip()
                src_tag = f" ({src_name})" if src_name and src_name.lower() != "none" else ""
                lines.append(f"- [{d}] {title}{src_tag}")
            if lines:
                return f"## Tin tức theo mã {sym} (vnstock_data, {len(lines)} tin mới nhất)\n" + "\n".join(lines)
        except Exception:
            continue
    return f"## Tin tức theo mã {sym}\nKhông lấy được tin tức công ty."


def _interpret_price_band(
    ceiling_hits: int, floor_hits: int,
    consec_ceil: int, consec_floor: int,
    vol_ratio: float, n: int,
) -> str:
    """Translate price-band counts into a plain-language sentiment label."""
    if consec_ceil >= 3:
        label = "STRONGLY BULLISH — retail demand overwhelming supply (3+ ceiling days)"
    elif consec_ceil >= 2 or ceiling_hits >= n * 0.3:
        label = "BULLISH — consistent upward pressure hitting daily band limit"
    elif consec_floor >= 3:
        label = "STRONGLY BEARISH — panic / forced selling (3+ floor days)"
    elif consec_floor >= 2 or floor_hits >= n * 0.3:
        label = "BEARISH — persistent selling pressure at lower band"
    elif vol_ratio >= 3:
        label = "ELEVATED ACTIVITY — volume spike; direction unclear, watch next session"
    else:
        label = "NEUTRAL — no extreme band behavior in observation window"
    return f"**Crowd-psychology signal**: {label}\n"


def _fetch_cafef_headlines(ticker: str) -> str:
    """Scrape recent CafeF headlines for a VN ticker. Degrades silently."""
    try:
        import urllib.request, urllib.parse, re
        # Google News RSS — reliable, no scraping, Vietnamese results
        query = urllib.parse.quote(f"{ticker} cổ phiếu")
        url = f"https://news.google.com/rss/search?q={query}&hl=vi&gl=VN&ceid=VN:vi"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            xml = resp.read().decode("utf-8", errors="replace")
        titles = re.findall(r"<title><!\[CDATA\[(.+?)\]\]></title>", xml)
        titles = [t for t in titles if ticker in t or "VCB" in t or len(t) > 20][1:9]
        if titles:
            lines = "\n".join(f"- {t.strip()}" for t in titles)
            return f"({len(titles)} headlines via Google News)\n{lines}"
        return "No relevant headlines found."
    except Exception as exc:
        return f"News fetch skipped: {exc}"


def get_global_news(*_, **__) -> str:
    """Not supported by vnstock_data — always raises to trigger fallback."""
    raise VnstockDataUnavailableError(
        "vnstock_data does not provide global macro news. "
        "Falling back to the next available vendor."
    )


def get_insider_transactions(*_, **__) -> str:
    """Not supported by vnstock_data — always raises to trigger fallback."""
    raise VnstockDataUnavailableError(
        "vnstock_data does not provide insider transaction data for VN equities. "
        "Falling back to the next available vendor."
    )
