"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

from tradingagents.agents.schemas import ResearchPlan, render_research_plan
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    financials_section,
    fact_check_section,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")

    def research_manager_node(state) -> dict:
        instrument_context = get_instrument_context_from_state(state)
        history = state["investment_debate_state"].get("history", "")

        investment_debate_state = state["investment_debate_state"]

        prompt = f"""As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.

**BURDEN OF PROOF ĐỐI XỨNG (bắt buộc):** Đánh giá cả hai phía theo CÙNG MỘT tiêu chuẩn
bằng chứng. Trước khi kết luận, với MỖI bên nêu rõ data point nào ĐÃ XÁC NHẬN (số liệu
lịch sử/đã công bố) vs DỰ BÁO (kỳ vọng tương lai). KHÔNG được bác một bên vì "chưa chứng
minh được tương lai" trong khi bên kia cũng chỉ đang dự báo. Số liệu thực tế gần nhất
(vd LNST quý mới nhất) là bằng chứng ĐÃ XÁC NHẬN — không được gạt đi.

**IMPACT-WEIGHTED RISK ASSESSMENT (bắt buộc):** Khi đánh giá rủi ro từ Bear, KHÔNG
đếm số lượng data point — hãy đánh giá theo TÁC ĐỘNG lên LNST/fair value:
- Với mỗi rủi ro được Bear nêu: ước tính impact (% LNST giảm hoặc fair value giảm) và
  xác suất xảy ra. Rủi ro có impact cao + xác suất trung bình > rủi ro có impact thấp +
  xác suất cao.
- Rủi ro ĐÃ XÁC NHẬN (đang xảy ra, số liệu thực) và CHƯA GIẢI QUYẾT phải được MAP rõ
  vào kịch bản Bear với xác suất cụ thể và sizing hệ quả — KHÔNG được "acknowledge rồi
  bỏ qua" chỉ vì bên Bull có nhiều luận điểm hơn.
- Công thức: EV Bear = Σ(xác suất_i × impact_i). Nếu EV Bear > 15% fair value downside,
  rating tối đa là Hold dù Bull thắng điểm số.

**XÁC NHẬN DỮ LIỆU TREND (bắt buộc):** Trước khi chấp nhận luận điểm trend từ bất kỳ bên nào,
kiểm tra: (a) Xu hướng có được dẫn chứng bằng tối thiểu 2–3 data point thực không?
(b) Phân biệt "phục hồi từ đáy" vs "xu hướng tăng cấu trúc" — đây là hai luận điểm
khác nhau về độ bền. Nếu một bên claim xu hướng tăng nhưng chỉ có 1 quý tốt sau nhiều
quý xấu → ghi nhận là "phục hồi, chưa xác nhận xu hướng", không phải structural uptrend.

**WHY NOW (bắt buộc cho Buy/Overweight):** Nếu rating của bạn là Buy hoặc Overweight,
bắt buộc phải nêu RÕ một trong hai:
(a) Xúc tác cụ thể gần kỳ: sự kiện/công bố/thời điểm dự kiến trong 1–2 quý tới sẽ làm
    giá phản ánh lại giá trị (vd: kết quả kinh doanh, phê duyệt dự án, refinancing xong).
(b) Thừa nhận tường minh: "Đây là vị thế kiên nhẫn/optionality — chưa có xúc tác rõ
    trong ngắn hạn; sizing nhỏ, chờ điều kiện kích hoạt [điều kiện cụ thể]."
KHÔNG được ra Buy/Overweight mà không có một trong hai điều trên.

**⛔ CẤM SELF-CITATION (bắt buộc):** Bạn KHÔNG được đề cập tên analyst, tên CTCK
hay tổ chức tài chính bên ngoài nào (ví dụ: "Analyst X từ Vietcap", "theo SSI
Research") TRỪ KHI thông tin đó xuất hiện nguyên văn trong debate/tool output đã
được cung cấp cho lượt này. Không được tạo citation giả để xác nhận luận điểm.
{financials_section(state)}{fact_check_section(state)}
---

**Debate History:**
{history}""" + get_language_instruction()

        investment_plan, rm_obj = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
        )

        # I2: validate citations against debate history (the full context fed to this agent)
        try:
            from tradingagents.agents.utils.citation_validator import validate_citations
            ticker = state.get("company_of_interest", "")
            context_for_rm = history  # debate history is the entire factual context
            investment_plan, _flagged = validate_citations(
                investment_plan, context_for_rm, "Research Manager", ticker
            )
        except Exception:
            pass

        rm_rating = rm_obj.recommendation.value if rm_obj is not None else None
        rm_reason = rm_obj.rationale if rm_obj is not None else None

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
            "rm_rating": rm_rating,
            "rm_reason": rm_reason,
        }

    return research_manager_node
