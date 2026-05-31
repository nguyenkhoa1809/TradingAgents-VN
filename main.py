import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import webbrowser
from datetime import date, datetime
from pathlib import Path

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from render_report import build_html

# ── Config ────────────────────────────────────────────────────────────────────
TICKER     = "VHM"
TRADE_DATE = date.today().strftime("%Y-%m-%d")  # or fixed: "2024-05-10"

# ANALYST SELECTION — controls cost vs quality
# All 4:  ~$0.35/run — full coverage
# 2 (VN): ~$0.15/run — leaner, good for VN stocks
ANALYSTS = ["market", "fundamentals", "news", "social"]  # "social" = sentiment analyst
# ANALYSTS = ["market", "fundamentals"]                   # cheaper for VN

# ── Run analysis ──────────────────────────────────────────────────────────────
config = DEFAULT_CONFIG.copy()
ta = TradingAgentsGraph(debug=True, config=config, selected_analysts=ANALYSTS)
state, decision = ta.propagate(TICKER, TRADE_DATE)
print(decision)

# ── Save HTML report to reports/{TICKER}/ ─────────────────────────────────────
SECTION_KEYS = [
    "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "investment_plan", "final_trade_decision",
]

sections = {k: state.get(k, "") for k in SECTION_KEYS if state.get(k, "").strip()}
trader = state.get("trader_investment_decision", "")
if isinstance(trader, str) and trader.strip():
    sections["trader_investment_plan"] = trader

if sections:
    out_dir = Path("reports") / TICKER
    out_dir.mkdir(parents=True, exist_ok=True)
    # Auto-increment version so reruns never overwrite previous reports
    v = 1
    while (out_dir / f"{TICKER}_{TRADE_DATE}_v{v}.html").exists():
        v += 1
    out_path = out_dir / f"{TICKER}_{TRADE_DATE}_v{v}.html"
    html = build_html(TICKER, TRADE_DATE, sections, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    out_path.write_text(html, encoding="utf-8")
    print(f"\nReport saved: {out_path.resolve()}")
    webbrowser.open(out_path.resolve().as_uri())

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000)  # pass realized P&L in VND/USD
