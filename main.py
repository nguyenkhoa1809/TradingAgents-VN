import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import webbrowser
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from cli.stats_handler import StatsCallbackHandler
from render_report import build_html

# Pricing per million tokens (input, output) — update as providers change rates
_PRICING_PER_M: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash":        (0.14,  0.28),
    "deepseek-v4-pro":          (1.74,  3.48),
    "deepseek-reasoner":        (0.55,  2.19),
    "deepseek-chat":            (0.27,  1.10),
    "claude-sonnet-4-6":        (3.00, 15.00),
    "claude-opus-4-8":         (15.00, 75.00),
    "claude-haiku-4-5-20251001":(0.80,  4.00),
    "gpt-5.5":                  (2.50, 10.00),
    "gpt-5.4":                  (1.25,  5.00),
    "gpt-5.4-mini":             (0.15,  0.60),
    "gpt-5.4-nano":             (0.075, 0.30),
}

def _calc_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = _PRICING_PER_M.get(model, (0.0, 0.0))
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000

# ── Config ────────────────────────────────────────────────────────────────────
# Single ticker:  TICKERS = ["VCB"]
# Multiple:       TICKERS = ["VCB", "TCB", "BID", "GMD", "TCB", "MBB", "FPT", "HPG", "PHR", "GVR", "VPB"]]
TICKERS    = ["VHM"]
TRADE_DATE = date.today().strftime("%Y-%m-%d")  # or fixed: "2026-01-28"


# PROVIDER — change this one line to switch LLM provider
#
# Preset            deep_think model                  quick_think model             ~cost/run
# ───────────────────────────────────────────────────────────────────────────────────────────
# "claude"          claude-sonnet-4-6                 claude-haiku-4-5              ~$0.35
# "claude-opus"     claude-opus-4-8                   claude-haiku-4-5              ~$1.50
# "deepseek"        deepseek-v4-flash                 deepseek-v4-flash             ~$0.03
# "deepseek-pro"    deepseek-v4-pro                   deepseek-v4-flash             ~$0.35
# "openai"          gpt-5.5                           gpt-5.4-mini                  ~$0.40
# "openai-cheap"    gpt-5.4                           gpt-5.4-nano                  ~$0.10
# "openrouter"      google/gemini-2.5-pro             google/gemini-2.5-flash       ~$0.05
# "openrouter-free" meta-llama/llama-3.3-70b          meta-llama/llama-3.3-70b      ~$0.00*
#                   * free models have rate limits, may be slow
#
# OpenRouter model IDs: see https://openrouter.ai/models  (format: provider/model-name)
# Requires OPENROUTER_API_KEY in .env
PROVIDER = "deepseek-pro"

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
        "deep_think_llm": "deepseek-v4-flash",
        "quick_think_llm":"deepseek-v4-flash",
    },
    "deepseek-pro": {
        "llm_provider":   "deepseek",
        "deep_think_llm": "deepseek-v4-pro",
        "quick_think_llm":"deepseek-v4-flash",
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
SECTION_KEYS = [
    "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "investment_plan", "final_trade_decision",
]

config = DEFAULT_CONFIG.copy()
config.update(_PROVIDER_PRESETS[PROVIDER])

_model_info = _PROVIDER_PRESETS[PROVIDER]

for TICKER in TICKERS:
    print(f"\n{'='*55}\n  Analyzing {TICKER} ({TICKERS.index(TICKER)+1}/{len(TICKERS)})\n{'='*55}")
    deep_stats  = StatsCallbackHandler()
    quick_stats = StatsCallbackHandler()
    ta = TradingAgentsGraph(
        debug=True, config=config, selected_analysts=ANALYSTS,
        deep_callbacks=[deep_stats], quick_callbacks=[quick_stats],
    )
    state, decision = ta.propagate(TICKER, TRADE_DATE)
    print(decision)

    # ── Cost summary ───────────────────────────────────────────────────────────
    ds = deep_stats.get_stats()
    qs = quick_stats.get_stats()
    deep_cost  = _calc_cost(_model_info["deep_think_llm"],  ds["tokens_in"], ds["tokens_out"])
    quick_cost = _calc_cost(_model_info["quick_think_llm"], qs["tokens_in"], qs["tokens_out"])
    total_cost  = deep_cost + quick_cost
    total_tok_in  = ds["tokens_in"]  + qs["tokens_in"]
    total_tok_out = ds["tokens_out"] + qs["tokens_out"]
    cost_str = (
        f"${total_cost:.4f} · {total_tok_in+total_tok_out:,} tokens "
        f"({total_tok_in:,}in / {total_tok_out:,}out)"
    )
    print(f"\nTokens — deep: {ds['tokens_in']:,}in / {ds['tokens_out']:,}out  "
          f"| quick: {qs['tokens_in']:,}in / {qs['tokens_out']:,}out")
    print(f"Cost   — {cost_str}")

    # ── Save HTML report ───────────────────────────────────────────────────────
    sections = {k: state.get(k, "") for k in SECTION_KEYS if state.get(k, "").strip()}
    trader = state.get("trader_investment_decision", "")
    if isinstance(trader, str) and trader.strip():
        sections["trader_investment_plan"] = trader

    if sections:
        out_dir = Path(__file__).parent / "reports" / TICKER
        out_dir.mkdir(parents=True, exist_ok=True)
        v = 1
        while (out_dir / f"{TICKER}_{TRADE_DATE}_{PROVIDER}_v{v}.html").exists():
            v += 1
        out_path = out_dir / f"{TICKER}_{TRADE_DATE}_{PROVIDER}_v{v}.html"
        html = build_html(
            TICKER, TRADE_DATE, sections,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            model_info=_model_info,
            cost_str=cost_str,
        )
        out_path.write_text(html, encoding="utf-8")
        print(f"\nReport saved: {out_path.resolve()}")
        webbrowser.open(out_path.resolve().as_uri())

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000)  # pass realized P&L in VND/USD
