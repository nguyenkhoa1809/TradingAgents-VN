from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market_router import is_vn_ticker

_VN_FUNDAMENTALS_CONTEXT = """

VIETNAMESE ACCOUNTING AND MARKET CONTEXT — apply when analyzing this company:

ACCOUNTING STANDARD: VAS (Vietnamese Accounting Standards), NOT GAAP/IFRS.
- Revenue recognition and provisioning rules follow SBV circulars for banks
- Related-party transactions are common in family-controlled companies and SOEs — scrutinize carefully
- All financials reported in VND (Vietnamese Dong). Do NOT convert to USD for ratio analysis.
- EPS in VND; P/E ratios are calculated against VND share price

BANK-SPECIFIC METRICS (for VCB, BID, CTG, TCB, VPB, MBB, ACB, STB, HDB):
- NIM (Net Interest Margin): key profitability driver — target >3.5% for healthy banks
- CASA ratio: higher = lower funding cost = better profitability
- NPL ratio: SBV requires <3%; above = credit quality concern
- Coverage ratio (provisions/NPL): >100% = conservative, well-provisioned
- Do NOT use gross margin for banks — use NIM instead

OWNERSHIP TYPE:
- SOEs (VCB, BID, CTG, GAS, PLX): government support but slower growth; divestment risk
- Private (FPT, MSN, VNM, TCB): faster decisions, market-driven
- VN30 P/E context: banks 8–14×, consumer/tech 15–25×, above 30× = growth premium

REPORTING CALENDAR: Q1→Apr 30, Q2→Jul 30, Q3→Oct 30, Annual→Apr 30 next year
"""


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        vn_context = _VN_FUNDAMENTALS_CONTEXT if is_vn_ticker(ticker) else ""

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
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

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
