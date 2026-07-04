from langchain_core.tools import tool
from typing import Annotated, Optional
from pathlib import Path
from tradingagents.dataflows.interface import route_to_vendor

_MW_DB = Path(__file__).parent.parent.parent.parent.parent / "marketwire" / "data" / "marketwire.db"

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[Optional[int], "Days to look back; omit to use the configured default"] = None,
    limit: Annotated[Optional[int], "Max articles to return; omit to use the configured default"] = None,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news data
    """
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)


@tool
def get_marketwire_news(
    ticker: Annotated[str, "VN ticker symbol, e.g. VCB, HPG, ACB"],
    days: Annotated[int, "Days to look back (default 3)"] = 3,
) -> str:
    """
    Retrieve recent news from local MarketWire database for a Vietnamese ticker.
    Covers both RSS macro/market news (with LLM importance scores) and sell-side
    broker notes ingested from WhatsApp/Outlook.  Returns up to 15 most relevant
    articles sorted by importance then recency.  Use this BEFORE get_news for VN
    tickers — it is faster, offline, and includes internal broker analysis.
    """
    import sqlite3, json
    from datetime import timedelta
    from tradingagents.dataflows.run_context import effective_end_datetime

    if not _MW_DB.exists():
        return f"MarketWire DB not found at {_MW_DB}. Run MarketWire pipeline first."

    # Task 4B: chặn CẢ HAI đầu theo ngày phân tích. Backtest (ContextVar set) →
    # upper = cuối ngày trade_date, không lấy tin sau đó. Production (None) →
    # upper = now() như cũ. published lưu ISO ⇒ so sánh chuỗi ISO là đúng thứ tự.
    end_dt = effective_end_datetime()
    start_dt = end_dt - timedelta(days=days)
    lower = start_dt.isoformat()
    upper = end_dt.isoformat()
    ticker_upper = ticker.strip().upper()

    try:
        con = sqlite3.connect(str(_MW_DB), timeout=10)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT a.title, a.summary_vi, a.thesis, a.importance,
                   a.published, a.url, a.tickers,
                   s.name AS source_name, s.expert_name
            FROM articles a JOIN sources s ON a.source_id = s.id
            WHERE a.processed = 1
              AND a.published >= ?
              AND a.published <= ?
              AND (
                    a.tickers LIKE ?
                 OR s.name = 'Sell-side Notes'
              )
            ORDER BY COALESCE(a.importance, 0) DESC, a.published DESC
            LIMIT 15
            """,
            (lower, upper, f'%"{ticker_upper}"%'),
        ).fetchall()
        con.close()
    except Exception as e:
        return f"MarketWire query error: {e}"

    if not rows:
        return f"No MarketWire articles found for {ticker_upper} in [{lower[:10]}, {upper[:10]}]."

    lines = [f"=== MarketWire: {ticker_upper} (last {days} days, {len(rows)} articles) ===\n"]
    for r in rows:
        tickers = json.loads(r["tickers"] or "[]")
        if ticker_upper not in tickers and r["source_name"] != "Sell-side Notes":
            continue
        imp = f"*{r['importance']}" if r["importance"] else ""
        pub = r["published"][:16].replace("T", " ")
        expert = f" [{r['expert_name']}]" if r["expert_name"] else ""
        lines.append(f"[{pub}] {imp} {r['source_name']}{expert}")
        lines.append(f"  {r['title']}")
        if r["summary_vi"]:
            lines.append(f"  → {r['summary_vi'][:300]}")
        if r["thesis"]:
            lines.append(f"  Thesis: {r['thesis'][:200]}")
        lines.append("")
    return "\n".join(lines)
