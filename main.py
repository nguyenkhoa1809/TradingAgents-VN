"""
main.py — Quick Reference
──────────────────────────────────────────────────────────────────
Thay đổi thường gặp:

  TICKERS           dòng 152  — mã CK cần phân tích, vd: ["VCB", "TCB"]
  TRADE_DATE        dòng 153  — ngày phân tích (mặc định: hôm nay)
  OUTPUT_LANGUAGE   dòng 157  — "Vietnamese" / "English" — ngôn ngữ mọi báo cáo
  PROVIDER          dòng 186  — model LLM chính (deepseek-pro / claude / openrouter / ...)
  ANALYSTS          dòng 234  — bật/tắt analyst (market / fundamentals / news / social)
  VN_PROSE_REFINE   dòng 162  — True/False — bật/tắt GLM prose refinement tiếng Việt

Thêm LLM provider / model mới:
  _PROVIDER_PRESETS dòng 188  — thêm preset { llm_provider, deep_think_llm, quick_think_llm }
  _PRICING_PER_M    dòng 44   — thêm giá ($/M tokens) để tính cost hiển thị

Sửa prompt phân tích:
  Fundamentals    tradingagents/agents/analysts/fundamentals_analyst.py
  Market/News/Social  tradingagents/agents/analysts/<name>_analyst.py
  Ngôn ngữ output  tradingagents/agents/utils/agent_utils.py → get_language_instruction()

API keys (.env):
  DEEPSEEK_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY  — LLM chính
  ZHIPU_API_KEY  — Z.AI free tier, dùng cho GLM prose refinement
──────────────────────────────────────────────────────────────────
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os
import re as _re
import webbrowser
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.market_router import is_vn_ticker
from cli.stats_handler import StatsCallbackHandler
from render_report import build_html, validate_report

# Validator gate (A7/A8): production CHẶN report mâu thuẫn (không tạo HTML) và thử
# regenerate tối đa MAX_REGEN lần. Chạy với cờ --dev để chỉ cảnh báo + render kèm banner.
DEV_MODE  = "--dev" in sys.argv
MAX_REGEN = 2

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
    # Z.AI / GLM models (via OpenRouter)
    "z-ai/glm-5.2":             (1.40,  4.40),
    "z-ai/glm-4.6":             (0.60,  2.20),
    "z-ai/glm-4.5-air":         (0.20,  1.10),
    # Z.AI direct (bigmodel.cn) — free tier
    "glm-4.5-flash":            (0.00,  0.00),
    "glm-4-flash":              (0.00,  0.00),
}

def _calc_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = _PRICING_PER_M.get(model, (0.0, 0.0))
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


_CHART_COMMENT_RE = _re.compile(r"<!--\s*(?:VN_CHART_DATA|VN_TECH_DATA)\s+\{.*?\}\s*-->", _re.DOTALL)


def _refine_vn_prose(sections: dict[str, str], target_keys: list[str]) -> tuple[dict[str, str], dict]:
    """Post-process selected VN sections through GLM for better Vietnamese prose quality.

    Uses ZHIPU_API_KEY (bigmodel.cn, free glm-4-flash) if available,
    otherwise falls back to OPENROUTER_API_KEY (z-ai/glm-4.5-air, paid).
    Returns (refined_sections, glm_stats) where glm_stats = {model, tokens_in, tokens_out}.
    """
    zhipu_key = os.getenv("ZHIPU_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    if zhipu_key:
        base_url = "https://open.bigmodel.cn/api/paas/v4/"
        api_key  = zhipu_key
        model    = "glm-4.5-flash"
        print("  [GLM] Using Z.AI direct (free tier)")
    elif openrouter_key:
        base_url = "https://openrouter.ai/api/v1"
        api_key  = openrouter_key
        model    = "z-ai/glm-4.5-air"
        print("  [GLM] Using OpenRouter (z-ai/glm-4.5-air)")
    else:
        print("  [GLM] Skipped: set ZHIPU_API_KEY (free) or OPENROUTER_API_KEY in .env")
        return sections, {}

    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
    except ImportError:
        print("  [GLM] Skipped: openai package not installed")
        return sections, {}

    refined = dict(sections)
    glm_tok_in = glm_tok_out = 0
    for key in target_keys:
        content = sections.get(key, "")
        if not content.strip():
            continue

        # Preserve embedded chart data comment (must survive the rewrite)
        chart_comment = ""
        m = _CHART_COMMENT_RE.search(content)
        if m:
            chart_comment = m.group(0)
            content_clean = _CHART_COMMENT_RE.sub("", content).strip()
        else:
            content_clean = content

        try:
            print(f"  [GLM] Refining {key} ({len(content_clean):,} chars)...")
            # GLM-4.5 mặc định bật "thinking" — tắt đi vì viết lại văn phong không cần reasoning
            # (nếu bật, reasoning ăn hết token output → content rỗng)
            extra = {"thinking": {"type": "disabled"}} if model.startswith("glm-4.5") else {}
            resp = client.chat.completions.create(
                model=model,
                max_tokens=8192,
                extra_body=extra,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Bạn là chuyên gia biên tập báo cáo phân tích tài chính tiếng Việt. "
                            "Viết lại đoạn phân tích sau với văn phong chuyên nghiệp, tự nhiên, "
                            "như một chuyên viên phân tích người Việt viết cho đồng nghiệp đọc.\n\n"
                            "Quy tắc bắt buộc:\n"
                            "1. Giữ nguyên 100% số liệu tài chính và tất cả chỉ số định lượng\n"
                            "2. Giữ nguyên cấu trúc heading markdown (##, ###) và **bold**\n"
                            "3. Giữ nguyên toàn bộ bảng markdown\n"
                            "4. Không rút gọn nội dung — giữ đủ chiều sâu phân tích\n"
                            "5. Chỉ cải thiện văn phong, cách diễn đạt, độ tự nhiên của tiếng Việt\n"
                            "6. Giữ nguyên các marker trong ngoặc vuông như [TÍCH CỰC], "
                            "[TRUNG LẬP], [TIÊU CỰC] — KHÔNG xóa, KHÔNG đổi vị trí\n"
                            "7. Giữ nguyên các comment HTML dạng <!-- ... --> nếu có\n"
                            "8. Trả về markdown thuần túy, không thêm lời giải thích hay tiêu đề mới"
                        ),
                    },
                    {"role": "user", "content": content_clean},
                ],
            )
            refined_text = resp.choices[0].message.content or content_clean
            if chart_comment:
                refined_text += f"\n{chart_comment}"
            refined[key] = refined_text
            if resp.usage:
                glm_tok_in  += resp.usage.prompt_tokens or 0
                glm_tok_out += resp.usage.completion_tokens or 0
            print(f"  [GLM] Done {key} → {len(refined_text):,} chars")
        except Exception as e:
            print(f"  [GLM] Skipped {key}: {e}")

    return refined, {"model": model, "tokens_in": glm_tok_in, "tokens_out": glm_tok_out}

# ── Config ────────────────────────────────────────────────────────────────────
# Single ticker:  TICKERS = ["VCB"]
# Multiple:       TICKERS = ["VCB", "TCB", "BID", "GMD", "TCB", "MBB", "FPT", "HPG", "PHR", "GVR", "VPB"]]
TICKERS    = ["VCB"]
TRADE_DATE = date.today().strftime("%Y-%m-%d")  # or fixed: "2026-01-28"

# OUTPUT LANGUAGE — ngôn ngữ của TẤT CẢ báo cáo (tranh luận nội bộ vẫn English)
# "Vietnamese" → mọi analyst viết tiếng Việt từ gốc. "English" → mặc định.
OUTPUT_LANGUAGE = "Vietnamese"

# VN PROSE REFINEMENT — optional GLM post-processing (only for VN tickers)
# Priority: ZHIPU_API_KEY (bigmodel.cn, free tier) → OPENROUTER_API_KEY (paid)
# Add ZHIPU_API_KEY to .env to use free glm-4-flash. Get key at: bigmodel.cn
VN_PROSE_REFINE = True
VN_PROSE_REFINE_SECTIONS = [
    "market_report", "sentiment_report", "news_report",
    "fundamentals_report", "investment_plan", "trader_investment_plan",
    "final_trade_decision",
]


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
# "glm"             z-ai/glm-5.2                      z-ai/glm-5.2                  ~$0.00*
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
    "glm": {
        "llm_provider":   "openrouter",
        "deep_think_llm": "z-ai/glm-5.2",
        "quick_think_llm":"z-ai/glm-5.2",
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
    "fundamentals_report", "investment_plan", "trader_investment_plan",
    "final_trade_decision",
]

config = DEFAULT_CONFIG.copy()
config.update(_PROVIDER_PRESETS[PROVIDER])
config["output_language"] = OUTPUT_LANGUAGE

# Research Manager + Portfolio Manager → Claude Sonnet (deep tier only).
# Quick-tier agents (analysts, researchers, trader, risk) stay on PROVIDER above.
# Rollback: uncomment the 2 lines below.
# config["deep_think_provider"] = "anthropic"
# config["deep_think_llm"] = "claude-sonnet-4-6"

# Derive from config AFTER overrides so model chips + cost calc reflect actual models used.
_model_info = {
    "deep_think_llm":  config["deep_think_llm"],
    "quick_think_llm": config["quick_think_llm"],
}

for TICKER in TICKERS:
    print(f"\n{'='*55}\n  Analyzing {TICKER} ({TICKERS.index(TICKER)+1}/{len(TICKERS)})\n{'='*55}")

    sections, cost_str, warnings = None, "", []
    # Block + regenerate (A8): chạy lại pipeline tối đa MAX_REGEN lần nếu validator
    # phát hiện mâu thuẫn. Dev mode bỏ qua gate ngay từ lần đầu.
    for attempt in range(MAX_REGEN + 1):
        if attempt > 0:
            print(f"\n  ↻ Regenerate (lần {attempt+1}/{MAX_REGEN+1}) — validator/fact-check phát hiện lỗi")
        deep_stats  = StatsCallbackHandler()
        quick_stats = StatsCallbackHandler()
        ta = TradingAgentsGraph(
            debug=True, config=config, selected_analysts=ANALYSTS,
            deep_callbacks=[deep_stats], quick_callbacks=[quick_stats],
        )
        state, decision = ta.propagate(TICKER, TRADE_DATE, run_type="production")
        print(decision)

        # ── Cost summary ───────────────────────────────────────────────────────
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

        # ── Build sections ───────────────────────────────────────────────────────
        sections = {k: state.get(k, "") for k in SECTION_KEYS if state.get(k, "").strip()}

        # ── VN Prose Refinement via GLM (Z.AI on OpenRouter) ──────────────────────
        glm_stats = {}
        if VN_PROSE_REFINE and is_vn_ticker(TICKER) and sections:
            print(f"\n[GLM] Refining Vietnamese prose...")
            sections, glm_stats = _refine_vn_prose(sections, VN_PROSE_REFINE_SECTIONS)
            if glm_stats:
                glm_cost = _calc_cost(glm_stats["model"], glm_stats["tokens_in"], glm_stats["tokens_out"])
                total_cost += glm_cost
                total_tok_in  += glm_stats["tokens_in"]
                total_tok_out += glm_stats["tokens_out"]
                cost_str = (
                    f"${total_cost:.4f} · {total_tok_in+total_tok_out:,} tokens "
                    f"({total_tok_in:,}in / {total_tok_out:,}out)"
                )
                print(f"  [GLM] {glm_stats['tokens_in']:,}in / {glm_stats['tokens_out']:,}out"
                      f" · ${glm_cost:.4f}")

        # ── Fact-check gate (C3) — log contradictions ────────────────────────────
        fact_corrections = state.get("fact_check_corrections", "")
        has_contradictions = "❌ **BÁC BỎ**" in (fact_corrections or "")
        if has_contradictions:
            print(f"\n  ⚠  FactCheck: entity claim(s) CONTRADICTED — corrections injected into Phase II")
            if not DEV_MODE:
                print(f"      Phase II đã nhận corrections; nếu report vẫn dùng claim sai → regenerate")

        # ── Validator gate (A8) ───────────────────────────────────────────────────
        warnings = validate_report(sections)
        # Thêm fact-check contradiction vào điều kiện regenerate (C3)
        should_regenerate = bool(warnings) or (has_contradictions and attempt == 0)
        if not should_regenerate or DEV_MODE:
            break
        if warnings:
            print(f"\n  ✖ Validator phát hiện {len(warnings)} mâu thuẫn:")
            for w in warnings:
                print(f"      - {w}")

    # Sau khi thoát loop: chặn render nếu vẫn còn mâu thuẫn (production).
    if warnings and not DEV_MODE:
        print(f"\n  ⛔ BLOCKED: {TICKER} vẫn fail validator sau {MAX_REGEN+1} lần — "
              f"KHÔNG tạo HTML. Chạy lại với cờ --dev để render kèm banner cảnh báo.")
        continue

    if sections:
        out_dir = Path(__file__).parent / "reports" / TICKER
        out_dir.mkdir(parents=True, exist_ok=True)
        _hhmm = datetime.now().strftime("%H%M")
        out_path = out_dir / f"{TICKER}_{TRADE_DATE}_{PROVIDER}_{_hhmm}.html"
        run_model_info = dict(_model_info)
        if glm_stats.get("model"):
            run_model_info["refine_llm"] = glm_stats["model"]
        html = build_html(
            TICKER, TRADE_DATE, sections,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            model_info=run_model_info,
            cost_str=cost_str,
            agent_ratings={
                "market":             state.get("market_analyst_rating"),
                "news":               state.get("news_analyst_rating"),
                "fundamentals":       state.get("fundamentals_analyst_rating"),
                "market_reason":      state.get("market_analyst_reason"),
                "news_reason":        state.get("news_analyst_reason"),
                "fundamentals_reason": state.get("fundamentals_analyst_reason"),
                "rm":                 state.get("rm_rating"),
                "rm_reason":          state.get("rm_reason"),
                "trader":             state.get("trader_rating"),
                "trader_reason":      state.get("trader_reason"),
                "pm":                 state.get("pm_rating"),
                "pm_reason":          state.get("pm_reason"),
            },
        )
        out_path.write_text(html, encoding="utf-8")
        print(f"\nReport saved: {out_path.resolve()}")
        webbrowser.open(out_path.resolve().as_uri())

# Memorize mistakes and reflect
# ta.reflect_and_remember(1000)  # pass realized P&L in VND/USD
