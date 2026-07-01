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


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        if not trader_plan or not trader_plan.strip():
            logger.warning(
                "Portfolio Manager: trader_investment_plan is empty — "
                "decision will be based on incomplete upstream input (Trader phase missing)."
            )
            trader_plan = "[MISSING — Trader phase produced no output]"

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}
{financials_section(state)}
---

**PHƯƠNG PHÁP RA QUYẾT ĐỊNH (bắt buộc):**
1. KHÔNG quyết theo kiểu "bên nào thắng tranh luận". Hãy lấy các kịch bản + xác suất
   đã sinh ở phase trước, tính Expected Value = Σ(xác suất × payoff) và ghi rõ phép tính.
2. Rating suy ra TỪ EV + mức tin cậy. Nếu rating ngược dấu EV, phải giải thích lý do
   cụ thể (rủi ro đuôi, thanh khoản, chất lượng dữ liệu) — nếu không thì rating sai.
3. Bắc cầu định giá ↔ khuyến nghị: nếu định giá cho upside/downside X%, nêu rõ
   "Định giá cho upside X%, nhưng tôi khuyến nghị Y vì...". Không để hai phần mâu thuẫn.
4. Mỗi phần chỉ thêm thông tin MỚI; không lặp lại nguyên văn các luận điểm đã nêu trước.
5. Nếu bộ xác suất kịch bản bạn dùng KHÁC với bộ ở phase trước (vd phân tích kỹ thuật
   dùng 60/25/15 còn bạn dùng 40/40/20), phải có MỘT câu giải thích vì sao khác
   (khác khung thời gian, khác trọng số rủi ro...) — không để hai bộ mâu thuẫn trống.
6. ĐỊNH GIÁ 2 LỚP: Kiểm tra xem Lớp 1 (số hiện tại, không giả định phục hồi) cho
   upside bao nhiêu. Nếu L1 upside ≈ 0% (< ±5%) và phần lớn upside nằm ở kịch bản
   phục hồi L2 — bắt buộc thừa nhận điều đó ngay đầu quyết định: "Đây là kèo optionality,
   không phải cổ phiếu rẻ hôm nay." Hạ sizing đợt 1 tương ứng xác suất L2 xảy ra;
   KHÔNG đặt Buy/Overweight với sizing đầy nếu L1 không có margin of safety.
7. WHY NOW: Nếu rating là Buy hoặc Overweight, bắt buộc nêu RÕ trong quyết định:
   (a) Xúc tác cụ thể gần kỳ (sự kiện/công bố/thời điểm 1–2 quý tới), HOẶC
   (b) Thừa nhận tường minh: "Vị thế kiên nhẫn/optionality — sizing nhỏ, chờ [điều kiện]."
   KHÔNG được phát hành Buy/Overweight mà không có một trong hai. Nếu thiếu xúc tác rõ
   ràng → hạ xuống Hold hoặc Overweight với sizing nhỏ kèm điều kiện kích hoạt.
8. IMPACT-WEIGHTED RISK: Trước khi confirm rating, rà soát lại rủi ro ĐÃ XÁC NHẬN
   và CHƯA GIẢI QUYẾT từ debate. Với mỗi rủi ro loại này: ước tính impact (% fair value
   downside) × xác suất = EV risk. Nếu tổng EV risk > 15% fair value downside, rating
   tối đa là Hold dù số lượng luận điểm Bull nhiều hơn. Ghi rõ phép tính này trong
   quyết định.
9. SENSITIVITY (bắt buộc — ghi vào ev_sensitivity + conviction): Sau khi chốt EV, tính
   EV_low (chuyển +5pp từ Base sang Bear, Bull giữ nguyên) và EV_high (chuyển +5pp từ
   Base sang Bull, Bear giữ nguyên). Gắn nhãn Conviction: THẤP nếu dải [EV_low, EV_high]
   chứa EV=0% hoặc nếu EV_low/EV_high sẽ đổi rating; CAO nếu EV_low cách ranh giới gần
   nhất ≥3 điểm %; TRUNG BÌNH nếu còn lại.
10. ⛔ CẤM SELF-CITATION: Không được đề cập tên analyst, CTCK hay nguồn bên ngoài
    (ví dụ: "Analyst X từ Vietcap") TRỪ KHI thông tin đó có nguyên văn trong context
    đã cung cấp. Không được tạo citation để minh họa hay xác nhận EV/định giá.

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        final_trade_decision = invoke_structured_or_freetext(
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
            context_for_pm = "\n".join(filter(None, [history, research_plan, trader_plan]))
            final_trade_decision, _flagged = validate_citations(
                final_trade_decision, context_for_pm, "Portfolio Manager", ticker
            )
        except Exception:
            pass

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
        }

    return portfolio_manager_node
