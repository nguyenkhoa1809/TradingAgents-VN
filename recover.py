"""recover.py — Re-run missing phases from saved state and render HTML.

Loads the state JSON from the last pipeline run, identifies which phases produced
empty output, re-runs only those phases, then renders the HTML report.
Currently recoverable: Trader

Usage:
    python recover.py --ticker VPB --date 2026-07-02
    python recover.py --ticker VPB --date 2026-07-02 --provider deepseek
    python recover.py --ticker VPB --date 2026-07-02 --render-only
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse, json, webbrowser
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

LOGS_DIR = Path.home() / ".tradingagents" / "logs"

# JSON log key → sections key.
# trading_graph.py saves trader output under "trader_investment_decision" (legacy remap).
_JSON_TO_SECTION = {
    "market_report":              "market_report",
    "sentiment_report":           "sentiment_report",
    "news_report":                "news_report",
    "fundamentals_report":        "fundamentals_report",
    "investment_plan":            "investment_plan",
    "trader_investment_decision": "trader_investment_plan",
    "final_trade_decision":       "final_trade_decision",
}

_PROVIDER_PRESETS = {
    "claude": {
        "llm_provider":    "anthropic",
        "deep_think_llm":  "claude-sonnet-4-6",
        "quick_think_llm": "claude-haiku-4-5-20251001",
    },
    "claude-opus": {
        "llm_provider":    "anthropic",
        "deep_think_llm":  "claude-opus-4-8",
        "quick_think_llm": "claude-haiku-4-5-20251001",
    },
    "deepseek": {
        "llm_provider":    "deepseek",
        "deep_think_llm":  "deepseek-v4-flash",
        "quick_think_llm": "deepseek-v4-flash",
    },
    "deepseek-pro": {
        "llm_provider":    "deepseek",
        "deep_think_llm":  "deepseek-v4-pro",
        "quick_think_llm": "deepseek-v4-flash",
    },
    "openai": {
        "llm_provider":    "openai",
        "deep_think_llm":  "gpt-5.5",
        "quick_think_llm": "gpt-5.4-mini",
    },
    "openai-cheap": {
        "llm_provider":    "openai",
        "deep_think_llm":  "gpt-5.4",
        "quick_think_llm": "gpt-5.4-nano",
    },
    "openrouter": {
        "llm_provider":    "openrouter",
        "deep_think_llm":  "google/gemini-2.5-pro",
        "quick_think_llm": "google/gemini-2.5-flash",
    },
    "openrouter-free": {
        "llm_provider":    "openrouter",
        "deep_think_llm":  "meta-llama/llama-3.3-70b-instruct",
        "quick_think_llm": "meta-llama/llama-3.3-70b-instruct",
    },
    "glm": {
        "llm_provider":    "openrouter",
        "deep_think_llm":  "z-ai/glm-5.2",
        "quick_think_llm": "z-ai/glm-5.2",
    },
}


def _load_state(ticker: str, date: str) -> dict:
    path = LOGS_DIR / ticker / "TradingAgentsStrategy_logs" / f"full_states_log_{date}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No saved state found for {ticker} @ {date}\n  Expected: {path}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _build_sections(state_json: dict) -> dict:
    sections = {}
    for json_key, sec_key in _JSON_TO_SECTION.items():
        v = state_json.get(json_key, "")
        if isinstance(v, str) and v.strip():
            sections[sec_key] = v
    return sections


def _run_trader(state_json: dict, preset: dict) -> dict:
    """Instantiate quick LLM and re-run the Trader node with saved investment_plan.

    Returns the full result dict so callers can access trader_rating and trader_reason.
    """
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.agents.trader.trader import create_trader

    config = DEFAULT_CONFIG.copy()
    config.update(preset)

    # TradingAgentsGraph.__init__ creates the LLM clients — no full pipeline run.
    ta = TradingAgentsGraph(debug=False, config=config)
    trader_fn = create_trader(ta.quick_thinking_llm)

    minimal_state = {
        "company_of_interest": state_json["company_of_interest"],
        "investment_plan":     state_json.get("investment_plan", ""),
        "asset_type":          state_json.get("asset_type", "stock"),
        # financials_block not saved in log JSON — trader degrades gracefully
    }

    return trader_fn(minimal_state)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TradingAgents-VN — recover missing phases from saved state"
    )
    parser.add_argument("--ticker",      required=True, help="VN ticker, e.g. VPB")
    parser.add_argument("--date",        required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--provider",    default="deepseek-pro",
                        help=f"LLM provider for recovery run ({', '.join(_PROVIDER_PRESETS)})")
    parser.add_argument("--render-only", action="store_true",
                        help="Skip re-running phases; render whatever is in the saved state")
    args = parser.parse_args()

    ticker = args.ticker.upper()

    if not args.render_only and args.provider not in _PROVIDER_PRESETS:
        raise ValueError(f"Unknown provider {args.provider!r}. Choose from: {list(_PROVIDER_PRESETS)}")

    print(f"\nLoading saved state: {ticker} @ {args.date}")
    state_json = _load_state(ticker, args.date)
    sections = _build_sections(state_json)

    present  = [k for k in _JSON_TO_SECTION.values() if sections.get(k)]
    missing  = [k for k in _JSON_TO_SECTION.values() if not sections.get(k)]
    print(f"  Present : {present}")
    print(f"  Missing : {missing or '(none)'}")

    if not args.render_only and missing:
        preset = _PROVIDER_PRESETS[args.provider]

        if "trader_investment_plan" in missing:
            print(f"\n[Trader] Re-running with {args.provider} ({preset['quick_think_llm']})...")
            trader_result = _run_trader(state_json, preset)
            plan = trader_result.get("trader_investment_plan", "")
            if plan and len(plan.strip()) >= 20:
                sections["trader_investment_plan"] = plan
                # Persist recovered rating/reason into state_json for agent_ratings below
                if trader_result.get("trader_rating"):
                    state_json["trader_rating"] = trader_result["trader_rating"]
                if trader_result.get("trader_reason"):
                    state_json["trader_reason"] = trader_result["trader_reason"]
                print(f"  ✓ Trader recovered: {len(plan):,} chars")
            else:
                print(f"  ✗ Trader still produced empty/short output ({len(plan)} chars)")

    from render_report import build_html, validate_report

    warnings = validate_report(sections)
    if warnings:
        print(f"\n  ⚠ Validator ({len(warnings)} warning(s)):")
        for w in warnings:
            print(f"      - {w}")
    else:
        print("\n  ✓ Validator: no issues")

    out_dir = Path(__file__).parent / "reports" / ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix  = "render" if args.render_only else "recover"
    _hhmm = datetime.now().strftime("%H%M")
    out_path = out_dir / f"{ticker}_{args.date}_{args.provider}_{suffix}_{_hhmm}.html"

    provider_str  = args.provider if not args.render_only else "saved-state"
    preset_labels = _PROVIDER_PRESETS.get(args.provider, {})
    html = build_html(
        ticker, args.date, sections,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        model_info={
            "deep_think_llm":  preset_labels.get("deep_think_llm",  provider_str),
            "quick_think_llm": preset_labels.get("quick_think_llm", provider_str),
        },
        cost_str=f"(recovery run — cost not tracked)",
        agent_ratings={
            "market":             state_json.get("market_analyst_rating"),
            "news":               state_json.get("news_analyst_rating"),
            "fundamentals":       state_json.get("fundamentals_analyst_rating"),
            "market_reason":      state_json.get("market_analyst_reason"),
            "news_reason":        state_json.get("news_analyst_reason"),
            "fundamentals_reason": state_json.get("fundamentals_analyst_reason"),
            "rm":                 state_json.get("rm_rating"),
            "rm_reason":          state_json.get("rm_reason"),
            "trader":             state_json.get("trader_rating"),
            "trader_reason":      state_json.get("trader_reason"),
            "pm":                 state_json.get("pm_rating"),
            "pm_reason":          state_json.get("pm_reason"),
        },
    )
    out_path.write_text(html, encoding="utf-8")
    print(f"\nReport saved: {out_path.resolve()}")
    webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
