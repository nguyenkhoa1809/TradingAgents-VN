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
import textwrap
from datetime import datetime, timedelta
from typing import Optional

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
    from packaging.version import Version  # packaging ships with pip
    ver = _get_version()
    try:
        if Version(ver) < Version("3.0.0"):
            raise VnstockDataUnavailableError(
                f"vnstock_data {ver} is too old. "
                "Upgrade to >= 3.0.0 for the Unified UI: pip install -U vnstock_data"
            )
    except Exception as exc:
        # packaging might not be available — be lenient
        if "is too old" in str(exc):
            raise
        # version comparison failed, proceed optimistically


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

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

    header = (
        f"# VN stock data for {symbol.upper()} from {start_date} to {end_date}\n"
        f"# Source: vnstock_data (Unified UI)\n"
        f"# Total records: {len(df)}\n\n"
    )
    return _df_to_str(df, header)


# ---------------------------------------------------------------------------
# Technical indicators (best-effort via vnstock_ta or yfinance fallback hint)
# ---------------------------------------------------------------------------

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
    start_dt = end_dt - timedelta(days=look_back_days + 60)  # extra buffer for weekends
    start_date = start_dt.strftime("%Y-%m-%d")

    try:
        mkt = Market()
        df = mkt.equity(symbol.upper()).ohlcv(start=start_date, end=curr_date)
    except Exception as exc:
        return f"vnstock_data could not fetch data for indicator '{indicator}' on {symbol}: {exc}"

    if df is None or df.empty:
        return f"No price data available for {symbol} to compute '{indicator}'."

    # Attempt vnstock_ta
    try:
        from vnstock_ta import Indicators  # type: ignore
        ta = Indicators(df)
        ind_lower = indicator.strip().lower()
        compute = getattr(ta, ind_lower, None)
        if compute is not None:
            df[ind_lower] = compute()
            header = (
                f"# Technical indicator '{indicator}' for {symbol.upper()}\n"
                f"# Lookback: {look_back_days} days ending {curr_date}\n"
                f"# Source: vnstock_data + vnstock_ta\n\n"
            )
            cols = [c for c in df.columns if c in ("time", "date", "close", ind_lower)]
            return header + df[cols].tail(look_back_days).to_csv(index=False)
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: return raw OHLCV with a note
    header = (
        f"# Raw OHLCV for {symbol.upper()} (indicator '{indicator}' not computed — "
        f"install vnstock_ta for full indicator support)\n"
        f"# Lookback: {look_back_days} days ending {curr_date}\n\n"
    )
    return header + df.tail(look_back_days).to_csv(index=False)


# ---------------------------------------------------------------------------
# Fundamental data
# ---------------------------------------------------------------------------

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

    # Financial ratios
    try:
        df_ratio = fun.equity(sym).ratio()
        if df_ratio is not None and not df_ratio.empty:
            # Filter to rows on or before curr_date if a date column exists
            date_col = next(
                (c for c in df_ratio.columns if "date" in c.lower() or "time" in c.lower() or "period" in c.lower()),
                None,
            )
            if date_col:
                try:
                    import pandas as pd
                    df_ratio[date_col] = pd.to_datetime(df_ratio[date_col], errors="coerce")
                    cutoff = pd.to_datetime(curr_date)
                    df_ratio = df_ratio[df_ratio[date_col] <= cutoff]
                except Exception:
                    pass
            sections.append("## Financial Ratios\n" + df_ratio.tail(8).to_csv(index=False) + "\n")
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

    if df is not None and not df.empty and curr_date:
        date_col = next(
            (c for c in df.columns if "date" in c.lower() or "time" in c.lower() or "period" in c.lower()),
            None,
        )
        if date_col:
            try:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                df = df[df[date_col] <= pd.to_datetime(curr_date)]
            except Exception:
                pass

    header = (
        f"# Balance sheet for {sym} ({freq})\n"
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

    if df is not None and not df.empty and curr_date:
        date_col = next(
            (c for c in df.columns if "date" in c.lower() or "time" in c.lower() or "period" in c.lower()),
            None,
        )
        if date_col:
            try:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                df = df[df[date_col] <= pd.to_datetime(curr_date)]
            except Exception:
                pass

    header = (
        f"# Cash flow for {sym} ({freq})\n"
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

    if df is not None and not df.empty and curr_date:
        date_col = next(
            (c for c in df.columns if "date" in c.lower() or "time" in c.lower() or "period" in c.lower()),
            None,
        )
        if date_col:
            try:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                df = df[df[date_col] <= pd.to_datetime(curr_date)]
            except Exception:
                pass

    header = (
        f"# Income statement for {sym} ({freq})\n"
        f"# Source: vnstock_data\n\n"
    )
    return _df_to_str(df, header)


# ---------------------------------------------------------------------------
# News — VN-specific
# ---------------------------------------------------------------------------

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

    # Attempt vnstock_news (optional sponsored library)
    try:
        from vnstock_news import StockNews  # type: ignore
        news = StockNews(symbol=sym)
        df = news.articles(start=start_date, end=end_date)
        if df is not None and not df.empty:
            header = (
                f"# News for {sym} from {start_date} to {end_date}\n"
                f"# Source: vnstock_news\n\n"
            )
            return header + df.to_string(index=False)
    except ImportError:
        pass
    except Exception as exc:
        return (
            f"# News for {sym}\n"
            f"vnstock_news error: {exc}\n"
            f"Install vnstock_news for VN-specific news, or use yfinance fallback.\n"
        )

    return (
        f"# News for {sym}\n"
        f"vnstock_news is not installed. "
        f"Install it for Vietnamese stock news, or the system will fall back to yfinance.\n"
    )


def get_global_news(
    curr_date: str,
    look_back_days: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Not supported by vnstock_data — always raises to trigger fallback."""
    raise VnstockDataUnavailableError(
        "vnstock_data does not provide global macro news. "
        "Falling back to the next available vendor."
    )


def get_insider_transactions(ticker: str) -> str:
    """Not supported by vnstock_data — always raises to trigger fallback."""
    raise VnstockDataUnavailableError(
        "vnstock_data does not provide insider transaction data for VN equities. "
        "Falling back to the next available vendor."
    )
