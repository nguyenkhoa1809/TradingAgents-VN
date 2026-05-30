"""
interface.py
============
Central dispatch layer for all data-vendor calls in TradingAgents.

Routing logic
-------------
1. ``route_to_vendor(method, *args, **kwargs)`` is the single entry point
   used by every agent tool (core_stock_tools, fundamental_data_tools, etc.).

2. When the first positional argument looks like a ticker symbol the router
   calls ``detect_market()`` from ``market_router`` to decide whether to
   prefer the vnstock_data vendor (Vietnamese equities: VCB, FPT, HPG …) or
   the user-configured global vendor (yfinance / alpha_vantage).

3. The fallback chain always continues through the remaining vendors so that
   a missing vnstock_data installation is invisible to the caller — yfinance
   silently takes over.

4. Existing global-stock behaviour is unchanged: no config key needs to be
   set; vnstock is only injected for VN tickers detected at call time.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Vendor-specific imports
# ---------------------------------------------------------------------------
from .y_finance import (
    get_YFin_data_online,
    get_stock_stats_indicators_window,
    get_fundamentals as get_yfinance_fundamentals,
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
)
from .yfinance_news import get_news_yfinance, get_global_news_yfinance
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage_common import AlphaVantageRateLimitError

# vnstock_data adapter — imported lazily-safe: the module always loads even
# when vnstock_data is not installed; individual functions raise
# VnstockDataUnavailableError at call time, which triggers vendor fallback.
from .vnstock_data_adapter import (
    VnstockDataUnavailableError,
    get_stock_data as get_vnstock_stock_data,
    get_indicators as get_vnstock_indicators,
    get_fundamentals as get_vnstock_fundamentals,
    get_balance_sheet as get_vnstock_balance_sheet,
    get_cashflow as get_vnstock_cashflow,
    get_income_statement as get_vnstock_income_statement,
    get_news as get_vnstock_news,
    get_global_news as get_vnstock_global_news,
    get_insider_transactions as get_vnstock_insider_transactions,
)

# Market-detection utilities
from .market_router import get_vendor_for_ticker

# Configuration and routing logic
from .config import get_config

# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    }
}

VENDOR_LIST = [
    "vnstock",       # Vietnamese equities — auto-selected by market_router
    "yfinance",
    "alpha_vantage",
]

# ---------------------------------------------------------------------------
# Vendor dispatch table
# ---------------------------------------------------------------------------
# Each entry maps a method name to a dict of {vendor_name: callable}.
# "vnstock" entries use the thin adapter in vnstock_data_adapter.py.
# Order within each dict does NOT matter — ordering is driven at call time
# by the fallback chain built in route_to_vendor().

VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "vnstock":        get_vnstock_stock_data,
        "alpha_vantage":  get_alpha_vantage_stock,
        "yfinance":       get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "vnstock":        get_vnstock_indicators,
        "alpha_vantage":  get_alpha_vantage_indicator,
        "yfinance":       get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "vnstock":        get_vnstock_fundamentals,
        "alpha_vantage":  get_alpha_vantage_fundamentals,
        "yfinance":       get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "vnstock":        get_vnstock_balance_sheet,
        "alpha_vantage":  get_alpha_vantage_balance_sheet,
        "yfinance":       get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "vnstock":        get_vnstock_cashflow,
        "alpha_vantage":  get_alpha_vantage_cashflow,
        "yfinance":       get_yfinance_cashflow,
    },
    "get_income_statement": {
        "vnstock":        get_vnstock_income_statement,
        "alpha_vantage":  get_alpha_vantage_income_statement,
        "yfinance":       get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "vnstock":        get_vnstock_news,
        "alpha_vantage":  get_alpha_vantage_news,
        "yfinance":       get_news_yfinance,
    },
    "get_global_news": {
        # vnstock does not support global news — it raises immediately so the
        # fallback chain skips it transparently.
        "vnstock":        get_vnstock_global_news,
        "yfinance":       get_global_news_yfinance,
        "alpha_vantage":  get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        # vnstock does not support insider transactions — same transparent skip.
        "vnstock":        get_vnstock_insider_transactions,
        "alpha_vantage":  get_alpha_vantage_insider_transactions,
        "yfinance":       get_yfinance_insider_transactions,
    },
}

# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def get_category_for_method(method: str) -> str:
    """Return the category key that contains *method*.

    Raises:
        ValueError: if the method is not registered in TOOLS_CATEGORIES.
    """
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")


def get_vendor(category: str, method: str = None) -> str:
    """Return the configured vendor string for a category (or specific method).

    Tool-level configuration in ``tool_vendors`` takes precedence over the
    category-level ``data_vendors`` setting.  This function does NOT consider
    market-based routing; that is handled in ``route_to_vendor``.

    Args:
        category: One of the TOOLS_CATEGORIES keys.
        method:   Optional method name for tool-level override lookup.

    Returns:
        A comma-separated vendor priority string, e.g. ``"yfinance"`` or
        ``"alpha_vantage,yfinance"``.
    """
    config = get_config()

    # Tool-level override takes precedence
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Category-level default
    return config.get("data_vendors", {}).get(category, "yfinance")


def _is_ticker_arg(method: str) -> bool:
    """Return True if the first positional arg for *method* is a ticker symbol.

    Used to decide whether market auto-detection should be applied.
    Methods whose first argument is NOT a ticker (e.g. get_global_news whose
    first arg is curr_date) return False so market routing is skipped.
    """
    # get_global_news first arg is curr_date, not a ticker
    return method != "get_global_news"


def route_to_vendor(method: str, *args, **kwargs):
    """Route a method call to the best available vendor implementation.

    Routing algorithm
    -----------------
    1. Determine the tool category for *method*.
    2. If the method takes a ticker as its first argument and the caller
       supplied one, call ``detect_market()`` to identify VN vs. global.
       - VN ticker  -> prepend "vnstock" to the vendor priority list.
       - Global     -> use the user-configured vendor (yfinance by default).
    3. Any explicit ``tool_vendors`` override in the config always wins and
       bypasses market auto-detection entirely.
    4. Execute the primary vendor; on ``AlphaVantageRateLimitError`` or
       ``VnstockDataUnavailableError`` move to the next vendor in the chain.
    5. Raise ``RuntimeError`` only if all vendors in the chain are exhausted.

    Args:
        method:  Name of the tool method (must be in VENDOR_METHODS).
        *args:   Positional arguments forwarded verbatim to the vendor impl.
        **kwargs: Keyword arguments forwarded verbatim to the vendor impl.

    Returns:
        Whatever the vendor implementation returns (typically a str).

    Raises:
        ValueError:    Unknown method or category.
        RuntimeError:  All vendors exhausted without a successful result.
    """
    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    category = get_category_for_method(method)
    config = get_config()

    # --- Determine primary vendor list ---

    # Check for explicit tool-level override in config (highest priority).
    explicit_override = config.get("tool_vendors", {}).get(method)

    if explicit_override:
        # User explicitly wired this tool to a vendor; honour it exactly,
        # no market auto-detection.
        primary_vendors = [v.strip() for v in explicit_override.split(",") if v.strip()]
    else:
        # Auto-detect market from the first positional arg when applicable.
        ticker_arg = args[0] if args and _is_ticker_arg(method) else None
        market_mode = config.get("market_mode", "auto")

        if ticker_arg and market_mode != "global":
            # get_vendor_for_ticker returns "vnstock,yfinance" for VN tickers
            # and the config-defined vendor string for global tickers.
            vendor_str = get_vendor_for_ticker(ticker_arg, category, config)
        else:
            # market_mode == "global" or no ticker in first position
            vendor_str = get_vendor(category, method)

        primary_vendors = [v.strip() for v in vendor_str.split(",") if v.strip()]

    # --- Build full fallback chain ---
    # Start with primary vendors, then append any remaining available vendors
    # so we always have a complete fallback sequence.
    all_available = list(VENDOR_METHODS[method].keys())
    fallback_chain = list(primary_vendors)
    for vendor in all_available:
        if vendor not in fallback_chain:
            fallback_chain.append(vendor)

    # --- Execute with fallback ---
    last_exc: Exception | None = None
    for vendor in fallback_chain:
        if vendor not in VENDOR_METHODS[method]:
            # Vendor not wired for this method — skip silently
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            return impl_func(*args, **kwargs)
        except AlphaVantageRateLimitError as exc:
            last_exc = exc
            continue  # Rate limit — try next vendor
        except VnstockDataUnavailableError as exc:
            last_exc = exc
            continue  # vnstock_data not installed or method unsupported — fall through

    raise RuntimeError(
        f"No available vendor succeeded for '{method}'. "
        f"Last error: {last_exc}"
    )