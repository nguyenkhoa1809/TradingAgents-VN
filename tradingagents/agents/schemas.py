"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Reserve Hold for situations where the "
            "evidence on both sides is genuinely balanced; otherwise commit to "
            "the side with the stronger arguments."
        ),
    )
    evidence_assessment: str = Field(
        description=(
            "Đánh giá bằng chứng ĐỐI XỨNG cho cả hai phía trước khi kết luận. "
            "Với MỖI bên (bull và bear), liệt kê rõ: data point nào là ĐÃ XÁC NHẬN "
            "(confirmed — số liệu lịch sử/đã công bố) vs DỰ BÁO (forecast — kỳ vọng "
            "tương lai chưa chứng minh). Áp dụng CÙNG MỘT tiêu chuẩn bằng chứng cho "
            "cả hai — không được bắt một bên 'phải chứng minh' trong khi bên kia "
            "cũng chỉ đang dự báo. Số liệu phải khớp block 'SỐ LIỆU TÀI CHÍNH CHÍNH THỐNG'."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate. Chỉ bổ sung thông tin/lập luận "
            "MỚI — không lặp lại nguyên văn các luận điểm đã nêu ở phần phân tích "
            "trước."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Đánh Giá Bằng Chứng (confirmed vs forecast)**: {plan.evidence_assessment}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell. Rating PHẢI suy ra TỪ Expected Value + mức tin cậy "
            "(KHÔNG phải từ 'bên nào thắng tranh luận'). Nếu rating ngược dấu với EV "
            "thì phần expected_value phải giải thích rõ lý do (rủi ro đuôi, thanh "
            "khoản...) — nếu không, rating bị coi là sai."
        ),
    )
    expected_value: str = Field(
        description=(
            "Khung Expected Value — BẮT BUỘC: (1) lấy đúng các kịch bản + xác "
            "suất đã sinh ở phase trước; (2) tính EV có trọng số = Σ(xác suất × "
            "payoff), ghi rõ phép tính; (3) nêu rating suy ra từ EV. "
            "Đồng thời bắc cầu định giá và khuyến nghị: nếu định giá cho upside "
            "X%, phải có một câu dạng 'Định giá cho upside X%, nhưng tôi khuyến nghị "
            "Y vì...' — không được để định giá và khuyến nghị mâu thuẫn mà không "
            "giải thích. Số liệu trích từ block 'SỐ LIỆU TÀI CHÍNH CHÍNH THỐNG'."
        ),
    )
    ev_sensitivity: str = Field(
        description=(
            "Sensitivity của EV trong 1 lần chạy — tính thuần túy bằng số học, KHÔNG "
            "gọi LLM thêm. Dùng bộ xác suất Bull/Base/Bear và payoff đã chốt ở "
            "expected_value. "
            "Bước 1 — EV_low: chuyển +5 điểm % từ Base sang Bear (Bear tăng 5pp, "
            "Base giảm 5pp, Bull giữ nguyên). Tính lại EV_low = Σ(xác suất mới × payoff). "
            "Bước 2 — EV_high: chuyển +5 điểm % từ Base sang Bull (Bull tăng 5pp, "
            "Base giảm 5pp, Bear giữ nguyên). Tính lại EV_high = Σ(xác suất mới × payoff). "
            "Bước 3 — báo cáo: 'EV sensitivity: [EV_low%, EV_high%]' kèm phép tính rõ. "
            "Ví dụ: xác suất 45/35/20, payoff +18/0/-15 → "
            "EV_low (40/30/30): 0.40×18+0.30×0+0.30×(-15)=7.2+0-4.5=+2.7%; "
            "EV_high (50/30/20): 0.50×18+0.30×0+0.20×(-15)=9+0-3=+6.0%. "
            "EV sensitivity: [+2.7%, +6.0%]."
        ),
    )
    ev_rating_band: str = Field(
        default="",
        description=(
            "EV1 — Ánh xạ EV → rating theo BAND CỐ ĐỊNH đã cho trong prompt. BẮT BUỘC: "
            "(1) nêu EV thô rơi vào band nào của bảng; (2) rating đề xuất theo band là gì; "
            "(3) nếu rating cuối LỆCH khỏi band → giải trình rõ lý do (rủi ro đuôi, thanh "
            "khoản, mandate benchmark-relative) — nếu không giải trình, rating bị coi là sai. "
            "Format: 'EV +8% → band Overweight; rating cuối = Overweight (khớp band)' hoặc "
            "'EV +8% → band Overweight; nhưng hạ xuống Hold vì [lý do]'."
        ),
    )
    payoff_horizon: str = Field(
        default="",
        description=(
            "EV2 — Payoff gắn horizon + xác suất hội tụ (chống value trap). BẮT BUỘC: "
            "(1) mỗi kịch bản payoff ghi kèm KHUNG THỜI GIAN (vd '+18% trong 12 tháng'); "
            "(2) nếu upside phụ thuộc re-rating mà KHÔNG có xúc tác gần (nối mục why-now) → "
            "hạ payoff kỳ vọng, ghi rõ 'giá có thể đúng fair value nhưng thị trường chưa "
            "công nhận trong X tháng'; (3) ghi rõ giả định hội tụ: 'EV này giả định hội tụ "
            "[hoàn toàn/một phần] trong [horizon]'."
        ),
    )
    ev_risk_adjusted: str = Field(
        default="",
        description=(
            "EV3 — EV điều chỉnh rủi ro. BẮT BUỘC hiển thị CẢ HAI: EV thô và "
            "EV_risk_adjusted = EV / (độ rộng dải payoff) — dạng Sharpe thô để so chéo mã. "
            "Độ rộng dải payoff = payoff_Bull − payoff_Bear (điểm %). "
            "Format: 'EV thô: +5% | EV điều chỉnh rủi ro: 0.19 | dải payoff: [−8%, +18%] "
            "(rộng 26pp)'. Vị thế EV cao nhưng dải payoff rất rộng phải được đánh dấu là "
            "rủi ro cao, không đối xử ngang vị thế EV thấp dải hẹp."
        ),
    )
    conviction: str = Field(
        description=(
            "Nhãn độ tin cậy DỰA TRÊN dải EV vừa tính — KHÔNG phải cảm tính. "
            "Quy tắc bắt buộc: "
            "(1) THẤP: nếu dải [EV_low, EV_high] chứa EV = 0% (ranh giới Hold/Underweight) "
            "HOẶC nếu EV_low và EV_high sẽ dẫn đến rating KHÁC với rating đã chốt — "
            "kết luận không ổn định, thay đổi nhỏ xác suất có thể đổi rating. "
            "(2) CAO: nếu cả EV_low và EV_high đều nằm trong cùng vùng rating với EV, "
            "VÀ EV_low cách ranh giới gần nhất ≥3 điểm % EV. "
            "(3) TRUNG BÌNH: mọi trường hợp còn lại. "
            "Format đầu ra: 'CAO — [lý do 1 câu]' / "
            "'TRUNG BÌNH — [lý do 1 câu]' / 'THẤP — [lý do 1 câu]'."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis. "
            "Chỉ bổ sung thông tin MỚI, không lặp lại nguyên văn luận điểm đã nêu "
            "ở các phase trước."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: Optional[str] = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        f"**Conviction**: {decision.conviction}",
        "",
        f"**Phân Tích Expected Value & Định Giá**: {decision.expected_value}",
        f"**EV Sensitivity**: {decision.ev_sensitivity}",
    ]
    if decision.ev_rating_band:
        parts.append(f"**EV → Rating (band)**: {decision.ev_rating_band}")
    if decision.ev_risk_adjusted:
        parts.append(f"**EV điều chỉnh rủi ro**: {decision.ev_risk_adjusted}")
    if decision.payoff_horizon:
        parts.append(f"**Payoff & Horizon**: {decision.payoff_horizon}")
    parts += [
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Phase-I Analyst Signal (Market / News / Fundamentals)
# ---------------------------------------------------------------------------


class AnalystSignal(BaseModel):
    """Structured rating extracted from a Phase-I analyst report.

    Produced by a second, lightweight LLM call after the analyst's prose
    report is complete.  Kept separate from the prose so build_html can
    read a reliable enum field instead of running heuristic regex.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "Overall investment recommendation based on the analyst's domain. "
            "Buy/Overweight = bullish; Hold = neutral; Underweight/Sell = bearish. "
            "Pick the best fit for the analyst's overall conclusion."
        ),
    )
    reasoning_summary: str = Field(
        description=(
            "One sentence (≤15 words) capturing the primary reason for this "
            "recommendation. Be specific: name a concrete factor (metric, trend, "
            "catalyst, or risk)."
        ),
    )


def render_analyst_signal(signal: AnalystSignal) -> str:
    return f"**Signal**: {signal.recommendation.value} — {signal.reasoning_summary}"


# ---------------------------------------------------------------------------
# Sentiment Analyst
# ---------------------------------------------------------------------------


class SentimentBand(str, Enum):
    """Discrete sentiment direction produced by the Sentiment Analyst.

    Six tiers keep the signal granular enough to be actionable while remaining
    small enough for every provider to map reliably from its JSON output.
    """

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class SentimentReport(BaseModel):
    """Structured sentiment report produced by the Sentiment Analyst.

    Replaces the previous free-form prose output so downstream consumers
    (dashboards, audit logs, PDF renderers, other agents) can read
    ``overall_band`` and ``overall_score`` without maintaining fragile regex
    fallbacks that drift with every model release. ``narrative`` preserves the
    rich source-by-source analysis; ``render_sentiment_report`` prepends a
    deterministic header so the saved report stays human-readable.
    """

    overall_band: SentimentBand = Field(
        description=(
            "Overall sentiment direction. Exactly one of: "
            "Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. "
            "Use Mixed when sources point in clearly different directions. "
            "Use Neutral only when all sources are genuinely silent or non-committal."
        ),
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Numeric sentiment intensity on a 0–10 scale. "
            "0 = maximally bearish, 5 = neutral, 10 = maximally bullish. "
            "Guideline for consistency with overall_band: "
            "Bullish ~6.5–10, Mildly Bullish ~5.5–6.4, Neutral/Mixed ~4.5–5.5, "
            "Mildly Bearish ~3.5–4.4, Bearish ~0–3.4. "
            "Only the 0–10 bounds are enforced."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence in the assessment based on data quality and sample size. "
            "Use 'low' when one or more sources returned a placeholder or fewer "
            "than 5 data points; 'medium' when data is present but sparse; "
            "'high' when all three sources returned substantive data."
        ),
    )
    narrative: str = Field(
        description=(
            "Full sentiment report covering, in order: "
            "(1) source-by-source breakdown with specific evidence (cite message "
            "counts, ratios, notable posts); "
            "(2) cross-source divergences and alignments; "
            "(3) dominant narrative themes; "
            "(4) catalysts and risks surfaced by the data; "
            "(5) a markdown table summarising key sentiment signals, their "
            "direction, source, and supporting evidence."
        ),
    )


def render_sentiment_report(report: SentimentReport) -> str:
    """Render a SentimentReport to the markdown shape the rest of the system expects.

    The structured header (band + score + confidence) is prepended to the
    narrative so the saved report is both human-readable and machine-parseable
    without regex.
    """
    return "\n".join([
        f"**Overall Sentiment:** **{report.overall_band.value}** "
        f"(Score: {report.overall_score:.1f}/10)",
        f"**Confidence:** {report.confidence.capitalize()}",
        "",
        report.narrative,
    ])
