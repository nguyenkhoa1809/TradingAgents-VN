import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_PROVIDER":  "deep_think_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE":          "temperature",
    "TRADINGAGENTS_ANALYST_TEMPERATURE":  "analyst_temperature",
    "TRADINGAGENTS_ANALYST_SEED":         "analyst_seed",
    "TRADINGAGENTS_MEMORY_LOG_DIR":       "memory_log_dir",
    "TRADINGAGENTS_MEMORY_LOG_HOSTNAME":  "memory_log_hostname",
}


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value."""
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Multi-machine mode: when set (e.g. a shared OneDrive folder), each
    # machine writes ONLY to its own trading_memory_<hostname>.md inside this
    # directory (single-writer-per-file — safe under cloud file sync), while
    # reads merge every machine's file so lessons/pending trades carry over
    # when you alternate between devices. Overrides memory_log_path when set.
    # None (default) = legacy single-file mode at memory_log_path.
    "memory_log_dir": None,
    # Override the auto-detected hostname used for the per-machine filename
    # above. None = socket.gethostname().
    "memory_log_hostname": None,
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    # When set, deep-think agents (Research Manager, Portfolio Manager) use this
    # provider instead of llm_provider. Enables mixing providers per tier.
    # None = use llm_provider for both tiers (backward-compatible default).
    "deep_think_provider": None,
    "deep_think_llm": "gpt-5.5",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # Determinism tier for Phase I analysts (Market/News/Fundamentals/Sentiment)
    # and Phase V Portfolio Manager.  These phases produce the primary inputs
    # and the final decision — low temperature reduces run-to-run variance.
    # Phase II–IV (debate, trader, risk) continue to use "temperature" above.
    # Set to None to disable the override and fall back to provider default.
    "analyst_temperature": 0.3,
    # Seed for Phase I analyst LLM.  DeepSeek (OpenAI-compatible) honours this;
    # Anthropic does not expose a seed parameter.  None disables seed.
    "analyst_seed": None,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    "analyst_concurrency_limit": 1,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance
        "fundamental_data": "yfinance",      # Options: alpha_vantage, yfinance
        "news_data": "yfinance",             # Options: alpha_vantage, yfinance
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    # Định giá deterministic (Task 8 / valuation_engine.py).
    # COE = risk_free + beta × ERP. Các tham số vĩ mô cập nhật ĐỊNH KỲ tại đây,
    # KHÔNG hardcode rải rác trong code.
    "valuation": {
        "risk_free":    0.030,   # lợi suất TPCP 10Y VN — cập nhật hàng quý
        "erp":          0.085,   # ERP + country risk premium (Damodaran EM) — cập nhật/năm
        "default_beta": 1.0,     # beta mặc định (Task 10 R1 sẽ wire beta thật từ price history)
        "g_cap":        0.05,    # trần tăng trưởng bền vững perpetuity (gần lạm phát dài hạn)
        "g_coe_buffer": 0.02,    # spread tối thiểu COE−g để công thức Gordon ổn định
        "payout_max_gd": 0.80,   # payout tối đa vẫn coi bền vững cho GD-eligible
        "streak_min_gd": 2,      # số năm tăng cổ tức tối thiểu (depth data events() ~2y)
        "ddm_min_payout": 0.30,  # payout tối thiểu để DDM có ý nghĩa (mã cổ tức thực)
        "sector_level":  3,      # cấp ICB dùng làm peer group cho sector median
        "max_peers":     25,     # trần số mã peer fetch (bound API + wall-clock)
        "peer_sleep":    0.3,    # nghỉ giữa call peer (golden tier 500/min)
        "dcf_horizon":   10,     # số năm high-growth cho reverse-DCF
    },
    # Risk metrics deterministic (Task 10 / vn_risk_metrics.py).
    # Tính beta/VaR/drawdown/ADTV/days-to-liquidate từ price history.
    "risk_metrics": {
        "var_confidence":    0.95,   # mức tin cậy VaR
        "var_horizon_days":  20,     # chân trời VaR (ngày giao dịch)
        "adtv_window":       30,     # cửa sổ tính ADTV
        "drawdown_years":    3,      # cửa sổ max drawdown
        "position_size_vnd": 50e9,   # quy mô vị thế KDEF giả định (tỷ VND) — cập nhật theo quỹ
        "participation_rate": 0.20,  # % ADTV có thể tham gia/ngày khi thanh lý
        "benchmark":         "VNINDEX",
    },
    # Band ánh xạ Expected Value → rating cho Portfolio Manager (Task 9 EV1).
    # Ngưỡng CỐ ĐỊNH theo khẩu vị quỹ — sửa trực tiếp tại đây; PM phải giải trình
    # mọi rating lệch khỏi band. Đây là band Task 4 backtest đo direction_correct.
    "ev_rating_band_text": (
        "EV > +12%  & Conviction ≥ TB  → Buy\n"
        "+5% đến +12%                   → Overweight\n"
        "−5% đến +5%                    → Hold\n"
        "−12% đến −5%                   → Underweight\n"
        "EV < −12%                      → Sell"
    ),
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
})
