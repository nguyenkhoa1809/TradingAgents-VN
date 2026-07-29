import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import logging
import time
import traceback
import webbrowser
from datetime import date, datetime
from pathlib import Path

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.dataflows.symbol_utils import NoMarketDataError
from tradingagents.agents.utils.agent_utils import _get_vn_listing
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

# Retry cho lỗi TRANSIENT (rate limit/timeout/connection) — KHÔNG retry lỗi
# data-not-found (ticker sai/delisted — NoMarketDataError), skip ngay.
MAX_RETRIES = 2
RETRY_BACKOFFS = (5, 15)  # giây, theo attempt (attempt 0 → 5s, attempt 1 → 15s)

# vnstock_data Guardian raise SystemExit khi rate-limit (không phải Exception —
# KHÔNG bị except Exception bắt) — đây chính là nguyên nhân đã xác nhận cho lần
# batch trước mất 1 mã không log gì (SystemExit giết cả process, không kịp print).
_TRANSIENT_KEYWORDS = (
    "timeout", "timed out", "rate limit", "connection", "429",
    "too many requests", "temporarily unavailable", "reset by peer",
)

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


# ── Logging: file riêng cho mỗi batch run — không silent-fail ──────────────────

def _setup_batch_logger(log_dir: Path) -> tuple:
    """Logger CHỈ ghi file (không trùng console print đã có). Traceback đầy đủ
    khi FAILED, không chỉ str(exc) — để điều tra được sau khi batch chạy xong."""
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y.%m.%d_%H%M")
    log_path = log_dir / f"batch_run_{ts}.log"
    logger = logging.getLogger(f"batch_run_{ts}_{id(log_path)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # không in trùng ra console — console đã có print() riêng
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    logger.addHandler(fh)
    return logger, log_path


# ── Phân loại lỗi transient vs permanent ────────────────────────────────────

def _is_transient(exc: BaseException) -> bool:
    """True = lỗi tạm thời (nên retry): rate limit/timeout/connection, kể cả
    SystemExit (vnstock Guardian). False = lỗi data-not-found (skip ngay,
    KHÔNG retry — ticker sai/delisted sẽ không tự khỏi sau vài giây)."""
    if isinstance(exc, NoMarketDataError):
        return False
    if isinstance(exc, SystemExit):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in _TRANSIENT_KEYWORDS)


def _propagate_with_retry(ta: TradingAgentsGraph, ticker: str, trade_date: str,
                          logger: logging.Logger, run_type: str = "production"):
    """ta.propagate() với retry cho lỗi transient (tối đa MAX_RETRIES lần,
    backoff RETRY_BACKOFFS). Bắt CẢ (Exception, SystemExit) — SystemExit từ
    vnstock rate-limit Guardian KHÔNG bị `except Exception` bắt (BaseException,
    không phải Exception) — đây là nguyên nhân đã xác nhận cho lần batch trước
    mất 1 mã không log gì (process bị kill ngay, không kịp ghi log/print).

    checkpoint_enabled trong config (bật riêng cho batch_run) giúp retry ĐỠ TỐN
    hơn full re-run: nếu crash giữa graph.invoke(), lần retry sau resume từ
    node cuối thành công thay vì chạy lại từ đầu.
    """
    attempt = 0
    while True:
        try:
            logger.info("START %s (attempt %d/%d)", ticker, attempt + 1, MAX_RETRIES + 1)
            result = ta.propagate(ticker, trade_date, run_type=run_type)
            logger.info("SUCCESS %s (attempt %d)", ticker, attempt + 1)
            return result
        except (Exception, SystemExit) as exc:
            transient = _is_transient(exc)
            tb = traceback.format_exc()
            if not transient or attempt >= MAX_RETRIES:
                logger.error(
                    "FAILED %s (attempt %d/%d, transient=%s): %s\n%s",
                    ticker, attempt + 1, MAX_RETRIES + 1, transient, exc, tb,
                )
                raise
            wait = RETRY_BACKOFFS[min(attempt, len(RETRY_BACKOFFS) - 1)]
            logger.warning(
                "TRANSIENT %s (attempt %d/%d): %s — retry sau %ds\n%s",
                ticker, attempt + 1, MAX_RETRIES + 1, exc, wait, tb,
            )
            print(f"       ⏳ Lỗi tạm thời ({exc}) — retry sau {wait}s "
                  f"(lần {attempt + 2}/{MAX_RETRIES + 1})")
            time.sleep(wait)
            attempt += 1


# ── Validate ticker trước khi tốn tiền chạy LLM ─────────────────────────────

def validate_tickers(tickers: list) -> tuple:
    """Đối chiếu danh sách ticker với vnstock listing THẬT (1 API call, cache
    sẵn qua _get_vn_listing — không tốn thêm gọi mạng nếu đã fetch trước đó
    trong process này). Trả (valid, invalid). Best-effort: listing rỗng/lỗi
    fetch → không chặn gì (coi mọi ticker hợp lệ, tránh false-positive chặn
    nhầm cả rổ vì lỗi mạng lúc validate)."""
    listing = _get_vn_listing()
    if not listing:
        return list(tickers), []
    valid = [t for t in tickers if t in listing]
    invalid = [t for t in tickers if t not in listing]
    return valid, invalid


# ── Verify sau khi batch chạy xong: so khớp ticker yêu cầu vs report thật ────

def find_reports(reports_dir: Path, ticker: str, trade_date: str) -> list:
    d = reports_dir / ticker
    if not d.exists():
        return []
    return sorted(d.glob(f"{ticker}_{trade_date}_*.html"))


def verify_batch(tickers: list, trade_date: str, reports_dir: Path) -> dict:
    """So khớp danh sách ticker YÊU CẦU với report THẬT trên đĩa — không chỉ
    dựa vào kết quả in-memory của loop (đề phòng crash không kịp ghi lại kết
    quả, dù giờ đã bắt SystemExit nên trường hợp đó không còn xảy ra)."""
    present, missing = [], []
    for t in tickers:
        (present if find_reports(reports_dir, t, trade_date) else missing).append(t)
    return {"total": len(tickers), "present": present, "missing": missing}


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

    reports_dir = Path(__file__).parent / "reports"
    logger, log_path = _setup_batch_logger(Path(__file__).parent / "logs")
    print(f"Log file: {log_path}")

    # Validate ticker TRƯỚC khi tốn tiền — loại ticker không tồn tại trong
    # vnstock listing khỏi danh sách chạy, in rõ để user sửa --tickers-file.
    valid_tickers, invalid_tickers = validate_tickers(tickers)
    if invalid_tickers:
        print(f"⚠ {len(invalid_tickers)} ticker KHÔNG có trong vnstock listing — "
              f"loại khỏi batch: {', '.join(invalid_tickers)}")
        logger.warning("Invalid tickers (not in vnstock listing): %s", invalid_tickers)
    tickers = valid_tickers
    if not tickers:
        print("Không còn ticker hợp lệ nào để chạy.")
        return

    config = DEFAULT_CONFIG.copy()
    config.update(_PRESETS[provider])
    if args.samples is not None:
        config["consistency_samples"] = int(args.samples)
    n_samples = int(config.get("consistency_samples", 1) or 1)
    # Bật checkpoint cho batch run: nếu 1 mã crash giữa chừng và được retry,
    # graph resume từ node cuối thành công thay vì chạy lại từ đầu (đỡ tốn
    # tiền/thời gian cho đúng trường hợp retry vừa thêm ở trên).
    config["checkpoint_enabled"] = True

    n = len(tickers)
    results = []
    start_time = datetime.now()

    print(f"\n{'='*65}")
    print(f"  BATCH RUN — {n} tickers — {trade_date} — provider: {provider} — samples: {n_samples}")
    print(f"  Mode: {config.get('pipeline_mode', 'rating')}  |  Est. time: ~{n * 6 * n_samples} min")
    if n_samples > 1:
        from tradingagents.graph.consistency import estimate_sampling_cost, format_sampling_estimate
        base_cost = {
            "claude": 0.35, "claude-opus": 1.50, "deepseek": 0.03, "deepseek-pro": 0.35,
            "openai": 0.40, "openai-cheap": 0.10, "openrouter": 0.05,
        }.get(provider, 0.05)
        print(f"  {format_sampling_estimate(estimate_sampling_cost(n, n_samples, base_cost))}")
    print(f"{'='*65}\n")
    logger.info("BATCH START — %d tickers, provider=%s, samples=%d, date=%s",
               n, provider, n_samples, trade_date)

    for i, ticker in enumerate(tickers, 1):
        ticker_start = datetime.now()
        print(f"[{i}/{n}] {ticker} — started {ticker_start.strftime('%H:%M:%S')}")

        # Skip-and-continue: LỖI 1 mã (sau khi đã retry hết cho lỗi transient,
        # hoặc lỗi data-not-found không retry) KHÔNG được giết cả rổ — log lỗi
        # ĐẦY ĐỦ traceback vào file, đi tiếp, tổng hợp + verify cuối vòng.
        try:
            ta = TradingAgentsGraph(debug=False, config=config, selected_analysts=ANALYSTS)
            state, decision = _propagate_with_retry(ta, ticker, trade_date, logger)

            sections = {k: state.get(k, "") for k in SECTION_KEYS if (state.get(k) or "").strip()}
            agent_ratings = {
                "market": state.get("market_analyst_rating"),
                "news": state.get("news_analyst_rating"),
                "fundamentals": state.get("fundamentals_analyst_rating"),
                "rm": state.get("rm_rating"), "pm": state.get("pm_rating"),
            }

            out_path = None
            if sections:
                out_dir = reports_dir / ticker
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

        except (Exception, SystemExit) as exc:
            elapsed = (datetime.now() - ticker_start).seconds // 60
            results.append({"ticker": ticker, "signal": "ERROR", "ok": False,
                            "error": str(exc), "min": elapsed, "consensus": ""})
            print(f"       ✗ ERROR: {exc}  (chi tiết trong {log_path.name})\n")

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

    # ── Verify: so khớp ticker yêu cầu vs report THẬT trên đĩa ──────────────
    verify = verify_batch(tickers, trade_date, reports_dir)
    print(f"VERIFY — yêu cầu {verify['total']} mã · report thành công "
          f"{len(verify['present'])} mã · thiếu {len(verify['missing'])} mã")
    if verify["missing"]:
        missing_str = ",".join(verify["missing"])
        print(f"  Mã thiếu: {', '.join(verify['missing'])}")
        print(f"  → Chạy lại riêng: python batch_run.py --ticker {missing_str} --samples {n_samples}")
        logger.warning("VERIFY MISSING: %s", verify["missing"])
    else:
        print("  Tất cả ticker yêu cầu đều có report — không thiếu mã nào.")
    logger.info("BATCH COMPLETE — OK %d/%d, missing %d", n_ok, n, len(verify["missing"]))

    print(f"\nReports saved in: {reports_dir}")
    print(f"Log file: {log_path}")


if __name__ == "__main__":
    main()
