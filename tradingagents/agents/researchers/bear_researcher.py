from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    financials_section,
    fact_check_section,
)


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        instrument_context = get_instrument_context_from_state(state)
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"
        fundamentals_label = (
            "Company fundamentals report"
            if asset_type == "stock"
            else "Asset fundamentals report (may be unavailable for crypto)"
        )

        prompt = f"""You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.
- Sensitivity Anchoring: If the fundamentals report includes a sensitivity table (Phân Tích Độ Nhạy), you MUST use its specific numbers to support your bear case. State the downside scenario level of the swing variable and the corresponding fair value / downside it implies. Challenge the bull by pointing to the breakeven: "At {{swing_variable}} = X, fair value drops to Y — below the current price." Avoid vague claims about risk without quantifying the impact.
- Data Substantiation: Every trend claim you make must be backed by at least 2–3 actual data points. When citing deterioration, show the series (e.g., margin Q1→Q2→Q3). Distinguish "cyclical trough" (temporary) from "structural decline" (multi-year). If you use a single bad quarter as evidence of a trend, the bull will legitimately challenge it — pre-empt this by showing the full series and explaining why this isn't just noise.
- FALSIFICATION (BẮT BUỘC — kết thúc bằng mục này): Nêu RÕ ít nhất 1 điều kiện ĐO ĐƯỢC sẽ chứng minh luận điểm Bear của bạn SAI (falsifiable). Phải là ngưỡng cụ thể quan sát được trong 1–2 quý tới, KHÔNG phải quan điểm. Ví dụ: "Tôi sai nếu doanh thu Q3 tăng > X% YoY hoặc nợ xấu giảm < Y%". Đây là trigger để PM đưa vào điều kiện nâng rating.

Resources available:

{instrument_context}
{financials_section(state)}{fact_check_section(state)}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
