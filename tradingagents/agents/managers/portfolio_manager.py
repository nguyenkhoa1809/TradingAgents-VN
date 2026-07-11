"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

import logging

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision

logger = logging.getLogger(__name__)
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    financials_section,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.config import get_config


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)

        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        research_plan = state.get("investment_plan", "")
        trader_plan = state.get("trader_investment_plan", "")

        # Nguồn overlay rủi ro tùy mode: "rating" → Risk Officer review;
        # "full" → risk debate history (như cũ). PM prompt dùng chung cả hai.
        risk_review = state.get("risk_review", "")
        if risk_review and risk_review.strip():
            risk_overlay_label = ("Risk Officer review (rủi ro NGOÀI bộ kịch bản, "
                                  "ràng buộc thực thi, điều kiện falsify)")
            risk_overlay = risk_review
        else:
            risk_overlay_label = "Risk Analysts Debate History"
            risk_overlay = history

        # Trader chỉ có ở mode "full"; mode "rating" bỏ qua dòng này.
        trader_line = (
            f"- Trader's transaction proposal: **{trader_plan}**\n"
            if trader_plan and trader_plan.strip()
            else ""
        )

        ev_band_text = get_config().get("ev_rating_band_text", "")

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the analysis and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**BẢNG BAND EV → RATING (CỐ ĐỊNH — ánh xạ bắt buộc, điền vào ev_rating_band):**
```
{ev_band_text}
```

**Context:**
- Research Manager's investment plan: **{research_plan}**
{trader_line}{lessons_line}
**{risk_overlay_label}:**
{risk_overlay}
{financials_section(state)}
---

**5 NGUYÊN TẮC RA QUYẾT ĐỊNH (bắt buộc):**

1. EXPECTED VALUE: Lấy đúng bảng kịch bản + xác suất từ phase trước, tính
   EV = Σ(xác suất × payoff) và GHI RÕ phép tính. Nếu bạn đổi bộ xác suất so với
   phase trước, giải thích MỘT câu vì sao (khác khung thời gian, khác trọng số rủi ro).

2. BAND EV → RATING: Ánh xạ EV thô vào BẢNG BAND CỐ ĐỊNH ở trên (điền ev_rating_band).
   Rating suy ra TỪ band. Nếu rating cuối LỆCH khỏi band → giải trình cụ thể
   (rủi ro đuôi/thanh khoản/mandate) — không giải trình thì rating sai.

3. WHY NOW (bắt buộc cho Buy/Overweight): nêu RÕ một trong hai — (a) catalyst cụ thể
   1–2 quý tới, HOẶC (b) thừa nhận tường minh đây là vị thế optionality, sizing nhỏ,
   kèm điều kiện kích hoạt. Upside phụ thuộc RE-RATING mà KHÔNG có catalyst → hạ payoff
   kỳ vọng (điền payoff_horizon với horizon + giả định hội tụ), chống value trap.

4. SENSITIVITY & CONVICTION (điền ev_sensitivity + conviction): tính EV_low (+5pp
   Base→Bear) và EV_high (+5pp Base→Bull). Conviction: THẤP nếu dải [EV_low, EV_high]
   chứa EV=0% hoặc sẽ đổi rating; CAO nếu EV_low cách ranh giới gần nhất ≥3pp;
   TRUNG BÌNH còn lại.

5. RỦI RO OVERLAY: CHỈ các rủi ro NGOÀI bộ kịch bản (lấy từ '{risk_overlay_label}' ở
   trên) mới được dùng để hạ rating XUỐNG DƯỚI mức band EV chỉ định. Rủi ro đã nằm
   trong xác suất kịch bản Bear thì KHÔNG tính lần thứ hai. Ghi rõ rủi ro nào (nếu có)
   đã kéo rating xuống.

**TP vs EV (bắt buộc — hai con số KHÁC NHAU, không thay thế nhau):**
- Block định giá deterministic ở trên luôn có dòng "TP (composite fair value)" — điểm
  khi hội tụ, hoặc range gắn nhãn ĐỘ TIN CẬY THẤP. TP là FAIR VALUE định giá (so được
  với target price sellside); EV là EXPECTED RETURN có trọng số xác suất kịch bản.
  Executive summary PHẢI nêu CẢ HAI (TP và EV).
- Nếu dấu của (TP − giá hiện tại) NGƯỢC dấu EV → bắt buộc một câu giải thích (thường do
  phân bố kịch bản Bull/Bear lệch so với fair value tĩnh).
- Nếu TP là range ĐỘ TIN CẬY THẤP (định giá tĩnh chưa hội tụ) → KHÔNG dùng TP làm căn cứ
  chính; dựa vào EV và ghi rõ "định giá tĩnh chưa hội tụ, dùng range tham khảo".
- (điền ev_risk_adjusted): hiển thị CẢ EV thô lẫn EV_risk_adjusted = EV / (payoff_Bull −
  payoff_Bear). EV cao nhưng dải payoff rất rộng → đánh dấu rủi ro cao.

⛔ CẤM SELF-CITATION: không nêu tên analyst/CTCK/nguồn ngoài trừ khi có nguyên văn trong
context. Mỗi phần chỉ thêm thông tin MỚI, không lặp nguyên văn phase trước.

Be decisive and ground every conclusion in specific evidence.{get_language_instruction()}"""

        final_trade_decision, pm_obj = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
        )

        # I2: validate citations against full risk debate + upstream plans
        try:
            from tradingagents.agents.utils.citation_validator import validate_citations
            ticker = state.get("company_of_interest", "")
            context_for_pm = "\n".join(filter(None, [history, risk_review, research_plan, trader_plan]))
            final_trade_decision, _flagged = validate_citations(
                final_trade_decision, context_for_pm, "Portfolio Manager", ticker
            )
        except Exception:
            pass

        # Task 10 R1: đính kèm NGUYÊN VĂN block risk metrics deterministic vào
        # report cuối — không qua diễn giải LLM, để verify được độc lập với hành
        # vi model (số liệu debator trích dẫn trong văn xuôi có thể lệch/làm tròn).
        risk_metrics_block = state.get("risk_metrics_block", "")
        if risk_metrics_block:
            final_trade_decision += f"\n\n{risk_metrics_block}"

        pm_rating = pm_obj.rating.value if pm_obj is not None else None
        pm_reason = pm_obj.executive_summary if pm_obj is not None else None

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
            "pm_rating": pm_rating,
            "pm_reason": pm_reason,
        }

    return portfolio_manager_node
