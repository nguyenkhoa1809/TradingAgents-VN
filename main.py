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

# PROVIDER — change this one line to switch LLM provider
#
# PROVIDER — change this one line to switch LLM provider
#
# Preset            deep_think model                  quick_think model             ~cost/run
# ───────────────────────────────────────────────────────────────────────────────────────────
# "claude"          claude-sonnet-4-6                 claude-haiku-4-5              ~$0.35
# "claude-opus"     claude-opus-4-8                   claude-haiku-4-5              ~$1.50
# "deepseek"        deepseek-reasoner                 deepseek-chat                 ~$0.03
# "openai"          gpt-5.5                           gpt-5.4-mini                  ~$0.40
# "openai-cheap"    gpt-5.4                           gpt-5.4-nano                  ~$0.10
# "openrouter"      google/gemini-2.5-pro             google/gemini-2.5-flash       ~$0.05
# "openrouter-free" meta-llama/llama-3.3-70b          meta-llama/llama-3.3-70b      ~$0.00*
#                   * free models have rate limits, may be slow
#
# OpenRouter model IDs: see https://openrouter.ai/models  (format: provider/model-name)
# Requires OPENROUTER_API_KEY in .env
PROVIDER = "claude"

_PROVIDER_PRESETS = {
    "claude": {
        "llm_provider":   "anthropic",
        "deep_think_llm": "claude-sonnet-4-6",
        "quick_think_llm":"claude-haiku-4-5-20251001",
    },
    "claude-opus": {
        "llm_provider":   "anthropic",
        "deep_think_llm": "claude-opus-4-8",
        "quick_think_llm":"claude-haiku-4-5-20251001",
    },
    "deepseek": {
        "llm_provider":   "deepseek",
        "deep_think_llm": "deepseek-reasoner",
        "quick_think_llm":"deepseek-chat",
    },
    "openai": {
        "llm_provider":   "openai",
        "deep_think_llm": "gpt-5.5",
        "quick_think_llm":"gpt-5.4-mini",
    },
    "openai-cheap": {
        "llm_provider":   "openai",
        "deep_think_llm": "gpt-5.4",
        "quick_think_llm":"gpt-5.4-nano",
    },
    "openrouter": {
        "llm_provider":   "openrouter",
        "deep_think_llm": "google/gemini-2.5-pro",
        "quick_think_llm":"google/gemini-2.5-flash",
    },
    "openrouter-free": {
        "llm_provider":   "openrouter",
        "deep_think_llm": "meta-llama/llama-3.3-70b-instruct",
        "quick_think_llm":"meta-llama/llama-3.3-70b-instruct",
    },
}

# ANALYST SELECTION — controls cost vs quality
# All 4:  ~$0.35/run Claude | ~$0.03/run DeepSeek
# 2 (VN): ~$0.15/run Claude | ~$0.01/run DeepSeek
ANALYSTS = ["market", "fundamentals", "news", "social"]  # "social" = sentiment analyst
# ANALYSTS = ["market", "fundamentals"]                   # cheaper option

# ── Run analysis ──────────────────────────────────────────────────────────────
config = DEFAULT_CONFIG.copy()
config.update(_PROVIDER_PRESETS[PROVIDER])
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
