"""
market_router.py
================
Auto-detects whether a ticker belongs to the Vietnamese market or the
global market, and resolves the correct data vendor for each category.

Design contract
---------------
- Vietnamese equities:  2-3 uppercase letters, letters only (A-Z), listed
  on HOSE or HNX.  Examples: VCB, FPT, HPG, VNM, ACB, SSI, MSN.
- VN-derived symbols that are NOT plain equities: VNINDEX (index),
  VN30F1M (futures), VFMVF1 (fund with digit).  These are routed to
  "global" so Yahoo Finance handles them, because vnstock_data only
  covers plain equities.
- Everything else (NVDA, AAPL, BRK.B, ^GSPC, BTC-USD, ETH-USD, TSLA,
  .NS / .HK suffixes) routes to "global".

Vendor resolution
-----------------
- "vn"     market  -> vnstock_data (primary), yfinance (fallback)
- "global" market  -> honours the data_vendors / tool_vendors config
                      (yfinance primary, alpha_vantage fallback, by default)

The vnstock_data vendor is intentionally NOT wired into the category-level
config so existing global-only deployments are unaffected.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# Explicit exclusions: tickers that are 2-3 uppercase letters but are NOT
# VN equities. US tickers (IBM, AMD...) don't need listing here — if
# vnstock_data can't find them it raises VnstockDataUnavailableError and
# falls back to yfinance automatically. Only list symbols that ARE valid
# vnstock_data lookups but should route to "global" anyway.
_VN_EXCLUSIONS: frozenset[str] = frozenset({
    "VNINDEX", "HNX", "UPCOM", "VN30", "HNX30",  # VN indices, not equities
    "BTC", "ETH", "XRP", "BNB", "SOL",            # common crypto 3-letter
})

# Regex: exactly 2 or 3 ASCII uppercase letters, nothing else.
_VN_EQUITY_RE = re.compile(r"^[A-Z]{2,3}$")

# Regex patterns for obvious non-VN tickers — matched BEFORE the letter rule.
_GLOBAL_PATTERNS = [
    re.compile(r"^\^"),           # index: ^GSPC, ^VIX, ^N225
    re.compile(r"-USD$"),         # crypto: BTC-USD, ETH-USD
    re.compile(r"\.[A-Z]{1,3}$"), # exchange suffix: AAPL.T, 0700.HK, REL.NS
    re.compile(r"\d"),            # contains any digit: GOOGL, BRK.B (dot), VN30F1M
]


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def is_vn_ticker(ticker: str) -> bool:
    """Return True if *ticker* should be resolved as a Vietnamese equity.

    Rules (evaluated in order):
    1. Strip whitespace; empty string -> False.
    2. If the ticker matches any global pattern (index prefix, crypto suffix,
       exchange suffix, contains a digit) -> False.
    3. If the ticker is in the explicit exclusion list -> False.
    4. If the ticker matches exactly 2-3 uppercase ASCII letters -> True.
    5. Otherwise -> False.

    Args:
        ticker: Raw ticker string as provided by the user or LLM.

    Returns:
        bool: True if the ticker is a Vietnamese equity.
    """
    if not ticker or not ticker.strip():
        return False

    t = ticker.strip().upper()

    # Step 2 — obvious global patterns
    for pattern in _GLOBAL_PATTERNS:
        if pattern.search(t):
            return False

    # Step 3 — explicit exclusions
    if t in _VN_EXCLUSIONS:
        return False

    # Step 4 — 2–3 letter rule
    return bool(_VN_EQUITY_RE.match(t))


def detect_market(ticker: str) -> str:
    """Detect the market for *ticker* and return "vn" or "global".

    This is the primary entry point used by ``route_to_vendor`` in
    ``interface.py``.  The classification is purely lexical — no network
    call is made.

    Args:
        ticker: Ticker symbol, e.g. "VCB", "NVDA", "BTC-USD", "^GSPC".

    Returns:
        "vn"     if the ticker is a Vietnamese equity on HOSE/HNX.
        "global" for everything else (US, international, crypto, indices).
    """
    return "vn" if is_vn_ticker(ticker) else "global"


def get_vendor_for_ticker(
    ticker: str,
    category: str,
    config: Optional[dict] = None,
) -> str:
    """Resolve the ordered vendor string for (ticker, category).

    For VN tickers the returned string is always ``"vnstock,yfinance"``
    regardless of what the user has set in ``data_vendors`` — vnstock_data
    covers VN equities best, with yfinance as silent fallback.

    For global tickers the function defers entirely to the category-level
    ``data_vendors`` config (and tool-level overrides handled separately in
    ``interface.get_vendor``), so existing behaviour is fully preserved.

    Args:
        ticker:   Ticker symbol.
        category: One of the TOOLS_CATEGORIES keys defined in interface.py.
        config:   Optional pre-fetched config dict; if None, fetched lazily.

    Returns:
        A comma-separated vendor priority string, e.g. ``"vnstock,yfinance"``
        or ``"yfinance,alpha_vantage"``.
    """
    market = detect_market(ticker)

    if market == "vn":
        # vnstock_data is primary; yfinance is a silent fallback for anything
        # vnstock_data cannot serve (global news, insider transactions, etc.).
        return "vnstock,yfinance"

    # Global: use configured vendor (lazy import to avoid circular dependency)
    if config is None:
        from tradingagents.dataflows.config import get_config
        config = get_config()

    return config.get("data_vendors", {}).get(category, "yfinance")
