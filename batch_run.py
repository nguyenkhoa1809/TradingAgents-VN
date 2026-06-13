import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import webbrowser
from datetime import date, datetime
from pathlib import Path

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from render_report import build_html

# ── Batch config — edit these ─────────────────────────────────────────────────
TICKERS = [
    "VCB", "TCB", "BID",    # Ngân hàng
    "FPT", "MWG",            # Công nghệ / Bán lẻ
    "HPG", "HSG",            # Thép
    "DCM", "DPM",            # Phân bón
    "VNM",                   # Tiêu dùng
]

TRADE_DATE = date.today().strftime("%Y-%m-%d")

# Provider — same options as main.py
# "claude" ~$0.35/ticker | "deepseek" ~$0.03/ticker | "openrouter" ~$0.05/ticker
PROVIDER = "deepseek"

ANALYSTS = ["market", "fundamentals", "news", "social"]
# ANALYSTS = ["market", "fundamentals"]  # cheaper, faster

OPEN_BROWSER = False  # True = mở browser sau mỗi báo cáo

# ── Provider presets ──────────────────────────────────────────────────────────
_PRESETS = {
    "claude":        {"llm_provider": "anthropic",  "deep_think_llm": "claude-sonnet-4-6",          "quick_think_llm": "claude-haiku-4-5-20251001"},
    "claude-opus":   {"llm_provider": "anthropic",  "deep_think_llm": "claude-opus-4-8",             "quick_think_llm": "claude-haiku-4-5-20251001"},
    "deepseek":      {"llm_provider": "deepseek",   "deep_think_llm": "deepseek-reasoner",           "quick_think_llm": "deepseek-chat"},
    "openai":        {"llm_provider": "openai",     "deep_think_llm": "gpt-5.5",                     "quick_think_llm": "gpt-5.4-mini"},
    "openai-cheap":  {"llm_provider": "openai",     "deep_think_llm": "gpt-5.4",                     "quick_think_llm": "gpt-5.4-nano"},
    "openrouter":    {"llm_provider": "openrouter", "deep_think_llm": "google/gemini-2.5-pro",       "quick_think_llm": "google/gemini-2.5-flash"},
}

SECTION_KEYS = [
    "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "investment_plan", "final_trade_decision",
]

# ── Run ───────────────────────────────────────────────────────────────────────
config = DEFAULT_CONFIG.copy()
config.update(_PRESETS[PROVIDER])

n = len(TICKERS)
results = []
start_time = datetime.now()

print(f"\n{'='*65}")
print(f"  BATCH RUN — {n} tickers — {TRADE_DATE} — provider: {PROVIDER}")
print(f"  Est. time: {n * 6} min  |  Est. cost: ${n * (0.03 if PROVIDER=='deepseek' else 0.35):.2f}")
print(f"{'='*65}\n")

for i, ticker in enumerate(TICKERS, 1):
    ticker_start = datetime.now()
    print(f"[{i}/{n}] {ticker} — started {ticker_start.strftime('%H:%M:%S')}")

    try:
        ta = TradingAgentsGraph(debug=False, config=config, selected_analysts=ANALYSTS)
        state, decision = ta.propagate(ticker, TRADE_DATE)

        sections = {k: state.get(k, "") for k in SECTION_KEYS if state.get(k, "").strip()}
        trader = state.get("trader_investment_decision", "")
        if isinstance(trader, str) and trader.strip():
            sections["trader_investment_plan"] = trader

        if sections:
            out_dir = Path("reports") / ticker
            out_dir.mkdir(parents=True, exist_ok=True)
            v = 1
            while (out_dir / f"{ticker}_{TRADE_DATE}_{PROVIDER}_v{v}.html").exists():
                v += 1
            out_path = out_dir / f"{ticker}_{TRADE_DATE}_{PROVIDER}_v{v}.html"
            html = build_html(ticker, TRADE_DATE, sections, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            out_path.write_text(html, encoding="utf-8")
            if OPEN_BROWSER:
                webbrowser.open(out_path.resolve().as_uri())

        elapsed = (datetime.now() - ticker_start).seconds // 60
        results.append({"ticker": ticker, "signal": decision, "ok": True, "min": elapsed})
        print(f"       ✓ {decision}  ({elapsed} min)  → {out_path.name}\n")

    except Exception as exc:
        elapsed = (datetime.now() - ticker_start).seconds // 60
        results.append({"ticker": ticker, "signal": "ERROR", "ok": False, "error": str(exc), "min": elapsed})
        print(f"       ✗ ERROR: {exc}\n")

# ── Summary ───────────────────────────────────────────────────────────────────
total_min = (datetime.now() - start_time).seconds // 60
print(f"\n{'='*65}")
print(f"  BATCH COMPLETE — {total_min} min total")
print(f"{'='*65}")
print(f"  {'TICKER':<8}  SIGNAL")
print(f"  {'──────':<8}  ──────")
for r in results:
    mark = "✓" if r["ok"] else "✗"
    print(f"  {mark} {r['ticker']:<8}  {r['signal']}")
print(f"{'='*65}\n")
print(f"Reports saved in: reports/")
