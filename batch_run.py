import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import webbrowser
from datetime import date, datetime
from pathlib import Path

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from render_report import build_html

# ── Default ticker list (dùng khi không truyền --ticker / --tickers-file) ──────
DEFAULT_TICKERS = [
    "VCB", "TCB", "BID",    # Ngân hàng
    "FPT", "MWG",            # Công nghệ / Bán lẻ
    "HPG", "HSG",            # Thép
    "DCM", "DPM",            # Phân bón
    "VNM",                   # Tiêu dùng
]

ANALYSTS = ["market", "fundamentals", "news", "social"]
OPEN_BROWSER = False

# ── Provider presets ──────────────────────────────────────────────────────────
_PRESETS = {
    "claude":        {"llm_provider": "anthropic",  "deep_think_llm": "claude-sonnet-4-6",          "quick_think_llm": "claude-haiku-4-5-20251001"},
    "claude-opus":   {"llm_provider": "anthropic",  "deep_think_llm": "claude-opus-4-8",             "quick_think_llm": "claude-haiku-4-5-20251001"},
    "deepseek":      {"llm_provider": "deepseek",   "deep_think_llm": "deepseek-reasoner",           "quick_think_llm": "deepseek-chat"},
    "deepseek-pro":  {"llm_provider": "deepseek",   "deep_think_llm": "deepseek-v4-pro",             "quick_think_llm": "deepseek-v4-flash"},
    "openai":        {"llm_provider": "openai",     "deep_think_llm": "gpt-5.5",                     "quick_think_llm": "gpt-5.4-mini"},
    "openai-cheap":  {"llm_provider": "openai",     "deep_think_llm": "gpt-5.4",                     "quick_think_llm": "gpt-5.4-nano"},
    "openrouter":    {"llm_provider": "openrouter", "deep_think_llm": "google/gemini-2.5-pro",       "quick_think_llm": "google/gemini-2.5-flash"},
}

SECTION_KEYS = [
    "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "investment_plan", "trader_investment_plan",
    "risk_review", "final_trade_decision",
]


def _load_tickers_file(path: str) -> list:
    """Đọc danh sách mã từ file text: mỗi dòng một mã, bỏ dòng trống / bắt đầu '#'."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip().upper()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingAgents-VN Batch Run")
    parser.add_argument("--ticker", help="Danh sách mã phân tách bởi dấu phẩy (vd VCB,HPG)")
    parser.add_argument("--tickers-file", help="File text, mỗi dòng một mã")
    parser.add_argument("--provider", default="deepseek",
                        help=f"LLM provider preset ({', '.join(_PRESETS)})")
    parser.add_argument("--date", default=None, help="Ngày phân tích YYYY-MM-DD (mặc định hôm nay)")
    parser.add_argument("--samples", type=int, default=None,
                        help="Self-consistency samples (mode rating). Mặc định theo config.")
    args = parser.parse_args()

    if args.tickers_file:
        tickers = _load_tickers_file(args.tickers_file)
    elif args.ticker:
        tickers = [t.strip().upper() for t in args.ticker.split(",") if t.strip()]
    else:
        tickers = list(DEFAULT_TICKERS)

    provider = args.provider
    if provider not in _PRESETS:
        raise SystemExit(f"Unknown provider {provider!r}. Choose from: {list(_PRESETS)}")
    trade_date = args.date or date.today().strftime("%Y-%m-%d")

    config = DEFAULT_CONFIG.copy()
    config.update(_PRESETS[provider])
    if args.samples is not None:
        config["consistency_samples"] = int(args.samples)
    n_samples = int(config.get("consistency_samples", 1) or 1)

    n = len(tickers)
    results = []
    start_time = datetime.now()

    print(f"\n{'='*65}")
    print(f"  BATCH RUN — {n} tickers — {trade_date} — provider: {provider} — samples: {n_samples}")
    print(f"  Mode: {config.get('pipeline_mode', 'rating')}  |  Est. time: ~{n * 6 * n_samples} min")
    print(f"{'='*65}\n")

    for i, ticker in enumerate(tickers, 1):
        ticker_start = datetime.now()
        print(f"[{i}/{n}] {ticker} — started {ticker_start.strftime('%H:%M:%S')}")

        # Skip-and-continue: LỖI 1 mã (data bẩn/typo/không resolve) KHÔNG được giết
        # cả rổ — log lỗi, đi tiếp, tổng hợp cuối vòng.
        try:
            ta = TradingAgentsGraph(debug=False, config=config, selected_analysts=ANALYSTS)
            state, decision = ta.propagate(ticker, trade_date, run_type="production")

            sections = {k: state.get(k, "") for k in SECTION_KEYS if (state.get(k) or "").strip()}
            agent_ratings = {
                "market": state.get("market_analyst_rating"),
                "news": state.get("news_analyst_rating"),
                "fundamentals": state.get("fundamentals_analyst_rating"),
                "rm": state.get("rm_rating"), "pm": state.get("pm_rating"),
            }

            out_path = None
            if sections:
                out_dir = Path(__file__).parent / "reports" / ticker
                out_dir.mkdir(parents=True, exist_ok=True)
                hhmm = datetime.now().strftime("%H%M")
                out_path = out_dir / f"{ticker}_{trade_date}_{provider}_{hhmm}.html"
                html = build_html(ticker, trade_date, sections,
                                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                  agent_ratings=agent_ratings)
                out_path.write_text(html, encoding="utf-8")
                if OPEN_BROWSER:
                    webbrowser.open(out_path.resolve().as_uri())

            agg = state.get("consistency_summary") or {}
            consensus = agg.get("consensus", "")
            elapsed = (datetime.now() - ticker_start).seconds // 60
            results.append({"ticker": ticker, "signal": decision, "ok": True,
                            "min": elapsed, "consensus": consensus})
            print(f"       ✓ {decision}  consensus {consensus or 'n/a'}  ({elapsed} min)"
                  f"  → {out_path.name if out_path else '(no report)'}\n")

        except Exception as exc:
            elapsed = (datetime.now() - ticker_start).seconds // 60
            results.append({"ticker": ticker, "signal": "ERROR", "ok": False,
                            "error": str(exc), "min": elapsed, "consensus": ""})
            print(f"       ✗ ERROR: {exc}\n")

    # ── Summary ─────────────────────────────────────────────────────────────
    total_min = (datetime.now() - start_time).seconds // 60
    n_ok = sum(1 for r in results if r["ok"])
    n_err = n - n_ok
    print(f"\n{'='*65}")
    print(f"  BATCH COMPLETE — {total_min} min — OK {n_ok}/{n}, lỗi {n_err}")
    print(f"{'='*65}")
    print(f"  {'TICKER':<8}  {'SIGNAL':<12}  CONSENSUS")
    print(f"  {'──────':<8}  {'──────':<12}  ─────────")
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {mark} {r['ticker']:<8}  {str(r['signal']):<12}  {r.get('consensus') or ''}")
    if n_err:
        print(f"\n  ⚠ {n_err} mã lỗi (đã bỏ qua, không chặn rổ):")
        for r in results:
            if not r["ok"]:
                print(f"    - {r['ticker']}: {r.get('error')}")
    print(f"{'='*65}\n")
    print(f"Reports saved in: {Path(__file__).parent / 'reports'}")


if __name__ == "__main__":
    main()
