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

    sections.append(
        "## UNIT NOTES (read before interpreting any numbers below)\n"
        "- Stock price / market price: in THOUSANDS of VND (nghìn đồng).\n"
        "  e.g. price=62.0 means 62,000 VND/share. Use 62,000 VND when calculating P/E.\n"
        "- EPS, book value per share: in full VND (e.g. EPS=4,500 means 4,500 VND/share).\n"
        "- Financial statement line items (revenue, profit, assets): in BILLIONS of VND (tỷ đồng).\n"
        "- To compute P/E manually: P/E = (price_in_VND) / EPS = (close × 1,000) / EPS\n\n"
    )

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

    # ── 2. CafeF RSS headlines (free, no auth) ───────────────────────────────
    news_block = _fetch_cafef_headlines(sym)

    # ── 3. vnstock_news (optional sponsored library) ─────────────────────────
    vnstock_news_block = "vnstock_news: not installed."
    try:
        from vnstock_news import StockNews  # type: ignore
        df_news = StockNews(symbol=sym).articles(
            start=(end_dt - timedelta(days=7)).strftime("%Y-%m-%d"),
            end=curr_date,
        )
        if df_news is not None and not df_news.empty:
            vnstock_news_block = (
                f"## vnstock_news headlines ({len(df_news)} articles)\n"
                + df_news.to_string(index=False, max_rows=15)
            )
    except ImportError:
        vnstock_news_block = "vnstock_news: not installed (install for richer VN news)."
    except Exception as exc:
        vnstock_news_block = f"vnstock_news: error — {exc}"

    return (
        f"# VN Sentiment Data for {sym} — {curr_date}\n\n"
        f"{price_block}\n\n"
        f"## CafeF Headlines\n{news_block}\n\n"
        f"{vnstock_news_block}\n"
    )


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
