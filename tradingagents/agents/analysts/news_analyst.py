from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_global_news,
    get_language_instruction,
    get_news,
    extract_analyst_rating,
)
from tradingagents.agents.utils.news_data_tools import get_marketwire_news
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market_router import is_vn_ticker

_VN_NEWS_CONTEXT = """

VIETNAMESE NEWS CONTEXT — apply when interpreting news for this stock:

PRIMARY SOURCES (most reliable for VN company-specific news):
1. Official HNX/HOSE disclosure portals — board resolutions, material events (most authoritative)
2. CafeF (cafef.vn) — most widely read Vietnamese financial news
3. Vietstock (vietstock.vn) — analysis-heavy, good for earnings previews
4. VnEconomy (vneconomy.vn) — macro and SBV policy focus

KEY MACRO CATALYSTS FOR VN MARKET:
- SBV policy rate changes and credit growth quota announcements
- VND/USD exchange rate management (SBV managed float)
- VN30/VN100 index semi-annual rebalancing — inclusion/exclusion moves stocks sharply
- SOE divestment (thoai von nha nuoc) calendar — creates supply overhang
- FDI inflow data from Ministry of Planning and Investment
- China macro: significant impact via supply chains and tourism

LANGUAGE NOTE: Most company-specific disclosures are in Vietnamese.
If news_block is sparse, note this gap and rely more on technical and fundamental analysis.
Flag these VN-specific risk keywords if present: "ket room ngoai" (foreign room full),
"giai chap" (margin call), "thoai von nha nuoc" (government divestment), "kiem toan" (audit concern).

⚠ TICKER IDENTITY WARNING — Vietnamese 3-letter tickers share codes with US securities:
  MBB = Ngân hàng Quân Đội (Military Bank), HOSE — NOT iShares MBS ETF (US)
  VNM = Vinamilk, HOSE                       — NOT VanEck Vietnam ETF (US)
  HPG = Hòa Phát Group, HOSE                 — NOT any US ticker
  TCB = Techcombank, HOSE                    — NOT any US ticker
If any tool returns news about US ETFs or non-Vietnamese companies for these tickers,
DISCARD that data entirely — it is the wrong security. Report news as unavailable instead.
"""


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)

        tools = [get_news, get_global_news]
        if is_vn_ticker(ticker):
            tools = [get_marketwire_news, get_news, get_global_news]

        vn_context = _VN_NEWS_CONTEXT if is_vn_ticker(ticker) else ""

        # News-digest formatting: tag each item with a sentiment marker the
        # renderer turns into a colored badge, and keep figures explicit.
        digest_format = (
            "\n\nNEWS DIGEST FORMAT — structure the report as follows:\n"
            "1. Start with a '## 📰 News Digest' section listing the 4–7 most "
            "market-relevant items. Begin EACH item with a sentiment marker on its "
            "own line, chosen from EXACTLY these tags:\n"
            "   [TÍCH CỰC]  — bullish / positive for the stock\n"
            "   [TRUNG LẬP] — neutral / mixed impact\n"
            "   [TIÊU CỰC]  — bearish / negative for the stock\n"
            "   Format: '- [TÍCH CỰC] **<headline>** — <1–2 sentence impact>. <source/date>'\n"
            "2. Always quote concrete figures explicitly (%, tỷ đồng, lần, giá) — "
            "do NOT round away the numbers; the digest highlights them automatically.\n"
            "3. After the digest, add a '## Phân Tích Chi Tiết' section with deeper context.\n"
            "4. End with a Markdown summary table of key points.\n"
        )

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for {asset_label}-specific or targeted news searches, and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + digest_format
            + vn_context
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""
        news_rating = None
        news_reason = None

        if len(result.tool_calls) == 0:
            report = result.content
            news_rating, news_reason = extract_analyst_rating(llm, report)

        return {
            "messages": [result],
            "news_report": report,
            "news_analyst_rating": news_rating,
            "news_analyst_reason": news_reason,
        }

    return news_analyst_node
