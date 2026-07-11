"""Risk Officer: a single structured checklist-review node.

Ở pipeline_mode="rating", node này thay cho vòng tranh luận 3 risk debator
(Aggressive/Conservative/Neutral). KHÔNG tranh luận — nhiệm vụ là review có
cấu trúc: (a) rủi ro NGOÀI bộ kịch bản Bull/Base/Bear, (b) ràng buộc thực thi
từ risk metrics deterministic, (c) điều kiện falsify thesis. Kết quả render vào
state['risk_review'] và được Portfolio Manager đọc làm overlay rủi ro.

Dùng cùng pattern structured-output/fallback như Research Manager & Trader.
"""

from __future__ import annotations

from tradingagents.agents.schemas import RiskReview, render_risk_review
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    financials_section,
    risk_metrics_section,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_risk_officer(llm):
    structured_llm = bind_structured(llm, RiskReview, "Risk Officer")

    def risk_officer_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)
        research_plan = state.get("investment_plan", "")

        prompt = f"""As the Risk Officer, produce a STRUCTURED CHECKLIST REVIEW of the Research Manager's investment plan. Đây KHÔNG phải tranh luận — không bênh/chống thị trường. Nhiệm vụ của bạn là rà soát rủi ro một cách có kỷ luật để Portfolio Manager dùng làm overlay.

{instrument_context}

---

**Investment plan của Research Manager (đã có bộ kịch bản Bull/Base/Bear + xác suất):**
{research_plan}
{financials_section(state)}{risk_metrics_section(state)}
---

**CHECKLIST (điền đúng 3 mục, không thêm):**

a) RỦI RO NGOÀI BỘ KỊCH BẢN: Tối đa 3 rủi ro mà bảng kịch bản Bull/Base/Bear
   CHƯA bao phủ (governance, sự kiện pháp lý, cổ đông lớn/pha loãng, tail risk).
   Mỗi rủi ro: impact ước tính + có làm đổi rating không. KHÔNG lặp lại rủi ro
   đã nằm trong kịch bản Bear (tránh đếm hai lần). Nếu không có → ghi rõ một câu.

b) RÀNG BUỘC THỰC THI: CHỈ diễn giải số CÓ SẴN trong block risk metrics
   (days-to-liquidate/thanh khoản, room ngoại, free-float). KHÔNG bịa số.

c) ĐIỀU KIỆN FALSIFY THESIS: 2–3 điều kiện quan sát được (số liệu quý, mức giá)
   mà nếu xảy ra thì thesis của Research Manager sai. Phải cụ thể, kiểm chứng được.

⛔ CẤM SELF-CITATION: không nêu tên analyst/CTCK/nguồn ngoài trừ khi có nguyên
văn trong context ở trên.{get_language_instruction()}"""

        risk_review, _obj = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_risk_review,
            "Risk Officer",
        )

        # I2: validate citations against the plan + financials context.
        try:
            from tradingagents.agents.utils.citation_validator import validate_citations
            ticker = state.get("company_of_interest", "")
            context_for_ro = "\n".join(filter(None, [
                research_plan, state.get("financials_block", ""),
                state.get("risk_metrics_block", ""),
            ]))
            risk_review, _flagged = validate_citations(
                risk_review, context_for_ro, "Risk Officer", ticker
            )
        except Exception:
            pass

        return {"risk_review": risk_review}

    return risk_officer_node
