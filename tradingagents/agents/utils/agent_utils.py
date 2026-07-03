import functools
import logging
from typing import Any, Mapping, Optional

import yfinance as yf
from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)
from tradingagents.agents.utils.market_data_validation_tools import (
    get_verified_market_snapshot
)

logger = logging.getLogger(__name__)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def _clean_identity_value(value: Any) -> Optional[str]:
    """Return a trimmed string, or None for empty / placeholder-ish values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned


@functools.lru_cache(maxsize=1)
def _get_vn_listing() -> dict:
    """Return {symbol -> organ_name} for all VN equities. Cached once per process."""
    try:
        from vnstock_data import Listing
        df = Listing(source="kbs").all_symbols(show=False)
        return dict(zip(df["symbol"], df["organ_name"]))
    except Exception:
        return {}


@functools.lru_cache(maxsize=256)
def resolve_instrument_identity(ticker: str) -> dict:
    """Resolve deterministic identity metadata (company name, sector, …) for a ticker.

    This exists to stop the pipeline from hallucinating a *different* company
    when a chart pattern suggests a different industry than the real one
    (#814): without a ground-truth name, the market analyst would pattern-match
    the price action to a narrative and invent an identity that then cascaded
    through every downstream agent.

    For Vietnamese tickers (2-3 uppercase letters, HOSE/HNX equities), yfinance
    is skipped entirely — it returns wrong foreign companies sharing the same
    ticker symbol (e.g. MWG→Multi Ways Holdings SG, HDB→HDFC Bank IN,
    MBB→iShares MBS ETF US). Instead we look up the name from vnstock_data.

    Best-effort by design: returns ``{}`` on any failure so the caller falls
    back to ticker-only context. Cached so the lookup runs at most once per
    ticker per process.
    """
    from tradingagents.dataflows.market_router import is_vn_ticker

    if is_vn_ticker(ticker):
        listing = _get_vn_listing()
        name = listing.get(ticker.upper())
        identity: dict[str, str] = {"exchange": "HOSE", "quote_type": "EQUITY"}
        if name:
            identity["company_name"] = name
        return identity

    try:
        info = yf.Ticker(ticker.upper()).info or {}
    except Exception as exc:  # noqa: BLE001 — fail open, never block the run
        logger.debug("Could not resolve instrument identity for %s: %s", ticker, exc)
        return {}

    identity = {}
    company_name = _clean_identity_value(info.get("longName")) or _clean_identity_value(
        info.get("shortName")
    )
    if company_name:
        identity["company_name"] = company_name
    for source_key, target_key in (
        ("sector", "sector"),
        ("industry", "industry"),
        ("exchange", "exchange"),
        ("quoteType", "quote_type"),
    ):
        value = _clean_identity_value(info.get(source_key))
        if value:
            identity[target_key] = value
    return identity


def build_instrument_context(
    ticker: str,
    asset_type: str = "stock",
    identity: Optional[Mapping[str, str]] = None,
) -> str:
    """Describe the exact instrument so agents preserve identity and ticker.

    When ``identity`` is provided (resolved deterministically via
    :func:`resolve_instrument_identity`), the company name and business
    classification are injected so agents anchor to the real company rather
    than pattern-matching the price chart to a wrong one (#814).
    """
    is_crypto = asset_type == "crypto"
    instrument_label = "asset" if is_crypto else "instrument"
    context = (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`)."
    )

    details = []
    if identity:
        name = identity.get("company_name") or identity.get("name")
        if name:
            details.append(f"{'Name' if is_crypto else 'Company'}: {name}")
        sector, industry = identity.get("sector"), identity.get("industry")
        if sector and industry:
            details.append(f"Business classification: {sector} / {industry}")
        elif sector:
            details.append(f"Sector: {sector}")
        elif industry:
            details.append(f"Industry: {industry}")
        if identity.get("exchange"):
            details.append(f"Exchange: {identity['exchange']}")

    if details:
        context += (
            f" Resolved identity: {'; '.join(details)}. "
            "Do not substitute a different company or ticker unless a tool "
            "result explicitly disproves this resolved identity."
        )

    if is_crypto:
        context += (
            " Treat it as a crypto asset rather than a company, and do not "
            "assume company fundamentals are available."
        )
    return context


def get_instrument_context_from_state(state: Mapping[str, Any]) -> str:
    """Return the instrument context for the current run.

    Prefers the identity-resolved context computed once at run start and
    stored on the state (see ``TradingAgentsGraph.resolve_instrument_context``).
    Falls back to a ticker-only context — with no network lookup — when the
    state was constructed without it (bare programmatic states, tests), so a
    consumer is never forced to make a yfinance call mid-graph.
    """
    context = state.get("instrument_context")
    if isinstance(context, str) and context.strip():
        return context
    return build_instrument_context(
        str(state["company_of_interest"]),
        state.get("asset_type", "stock"),
    )


# Single source of truth (A1): canonical financials injected into every
# number-touching agent. Computed once in Python at run start (A2/A3).
_FINANCIALS_CITE_RULE = (
    "\n\n[QUY TẮC SỐ LIỆU — BẮT BUỘC] Mọi con số tài chính (doanh thu, LNST, EPS, "
    "margin, ROE/ROA/ROIC, P/E, P/B, D/E, FCF, dòng tiền...) trong phần phân tích "
    "của bạn PHẢI trích nguyên văn từ block 'SỐ LIỆU TÀI CHÍNH CHÍNH THỐNG' được "
    "cung cấp. TUYỆT ĐỐI không tự tính lại, không ước lượng, không lấy từ trí nhớ. "
    "Nếu một chỉ số không có trong block, ghi rõ 'không có dữ liệu' thay vì bịa số.\n"
    "[ĐỊNH DẠNG SỐ — BẮT BUỘC] Giữ NGUYÊN định dạng như trong block: dấu phẩy ngăn "
    "nghìn (16,013), dấu chấm thập phân (1.06). Giá cổ phiếu luôn ghi 'X.X nghìn đồng'. "
    "Dấu '%' CHỈ dùng cho tỷ lệ/biến động (vd +48% YoY), TUYỆT ĐỐI không gắn '%' vào "
    "giá trị tuyệt đối (319 tỷ là số tiền, KHÔNG phải '319%').\n"
    "[LẬP LUẬN SỐ — BẮT BUỘC] (1) Lợi nhuận forward: ưu tiên TTM (4 quý gần nhất). "
    "KHÔNG annualize ngây thơ kiểu 'quý × 4'; nếu buộc phải annualize, ghi rõ caveat "
    "mùa vụ. (2) Mọi so sánh 'cao nhất/thấp nhất/cao hơn' phải xét cửa sổ ≥8 quý (gồm "
    "cả năm trước), nêu rõ phạm vi; KHÔNG dùng kiểu 'cao hơn mọi quý (trừ X)' tự mâu "
    "thuẫn. (3) Thuật ngữ: dùng 'lợi nhuận sau thuế (LNST)', KHÔNG viết 'lợi nhuận sau "
    "thu nhập'.\n"
)


def get_financials_block(state: Mapping[str, Any]) -> str:
    """Return the canonical financials block (single source of truth) for the run.

    Empty string when unavailable (non-VN tickers, fetch failure) — callers
    should inject nothing in that case rather than fabricate.
    """
    block = state.get("financials_block")
    return block if isinstance(block, str) and block.strip() else ""


def financials_section(state: Mapping[str, Any]) -> str:
    """Block + cite-only rule, ready to splice into an agent prompt.

    Returns '' when no canonical data exists, so prompts degrade cleanly.
    """
    block = get_financials_block(state)
    if not block:
        return ""
    return f"\n\n{block}{_FINANCIALS_CITE_RULE}"


def fact_check_section(state: Mapping[str, Any]) -> str:
    """Corrections từ C3 gate — inject vào Phase II agents để ngăn lan nhiễm factual.

    Returns '' khi không có corrections, prompt không thay đổi.
    """
    corrections = state.get("fact_check_corrections", "")
    if not corrections or not corrections.strip():
        return ""
    return f"\n\n{corrections.strip()}\n"


def extract_analyst_rating(llm, report_text: str) -> "tuple[str | None, str | None]":
    """Second-pass extraction of PortfolioRating + reasoning_summary from a prose analyst report.

    Uses the LLM's with_structured_output to get a reliable enum value and one-sentence reason.
    Returns (rating_value, reasoning_summary) or (None, None) on any failure.
    A failure here never blocks the main agent flow — the summary table will show
    'chưa có dữ liệu' for that row.
    """
    if not report_text or not report_text.strip():
        return None, None
    try:
        from langchain_core.messages import HumanMessage
        from tradingagents.agents.schemas import AnalystSignal
        structured_llm = llm.with_structured_output(AnalystSignal)
        signal = structured_llm.invoke([HumanMessage(content=(
            "Extract the overall investment recommendation from the analyst report below. "
            "Choose the single best fit: Buy / Overweight / Hold / Underweight / Sell. "
            "Summarize the primary reason in ≤15 words. Viết lý do bằng tiếng Việt.\n\n"
            f"{report_text[:4000]}"
        ))])
        return signal.recommendation.value, signal.reasoning_summary
    except Exception:
        return None, None


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add a context-anchored placeholder.

        The placeholder must not be a bare ``"Continue"``: some
        OpenAI-compatible providers interpret that literally as the user task
        and produce output about the word "continue" instead of analysing the
        instrument (#888). Anchoring it to the resolved instrument context and
        date keeps the next analyst on-task even if the provider treats the
        placeholder as a standalone request.
        """
        messages = state["messages"]
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        instrument_context = get_instrument_context_from_state(state)
        trade_date = state.get("trade_date", "the requested date")
        placeholder = HumanMessage(
            content=(
                f"Proceed with your assigned analysis for this workflow. "
                f"{instrument_context} The analysis date is {trade_date}."
            )
        )
        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
