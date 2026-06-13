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

VIETNAMESE MARKET — FUNDAMENTAL ANALYSIS FRAMEWORK
Apply ALL sections below when analyzing Vietnamese equities.

═══════════════════════════════════════════════════════
SECTION 1: ACCOUNTING CONTEXT (VAS)
═══════════════════════════════════════════════════════
- Standard: VAS (Vietnamese Accounting Standards), NOT GAAP/IFRS
- Revenue recognition follows SBV circulars for banks; MOF for others
- Related-party transactions are common in family-controlled companies and SOEs — scrutinize carefully
- All financials in VND. Do NOT convert to USD for ratio analysis.
- EPS in full VND/share; P/E calculated against VND share price
- Q4 annual audits often restate earlier quarters — treat full-year figures as more reliable

═══════════════════════════════════════════════════════
SECTION 2: SECTOR-SPECIFIC METRICS
═══════════════════════════════════════════════════════

BANKING (VCB, BID, CTG, TCB, VPB, MBB, ACB, STB, HDB, MSB):
- NIM (Net Interest Margin): KEY driver. >3.5% = healthy. >4% = strong.
- CASA ratio: Higher = cheaper funding. >30% = competitive advantage.
- NPL ratio: <2% = excellent. 2–3% = acceptable. >3% = SBV concern.
- Coverage ratio (LLR/NPL): >150% = conservative. >100% = adequate.
- CAR: Basel II >8% required; Basel III >9% for VN banks.
- Credit growth: SBV-regulated quota (typically 10–15%/year). Growth above quota = regulatory risk.
- ROE target: >15% acceptable; >20% excellent for VN banks.
- Do NOT use gross margin for banks — NIM and fee income are the correct metrics.

REAL ESTATE / BĐS (VHM, NLG, DIG, NVL, PDR, KDH):
- Pre-sales backlog: Revenue not yet recognized but cash collected — look for "tiền người mua trả trước" in liabilities.
- Landbank: Often not on balance sheet at fair value; check for undisclosed revaluation gains.
- Leverage: Debt/Equity >3× is concerning. Short-term debt rollovers = liquidity risk.
- Cash collection rate: "Tiền thu từ khách hàng" in CFO vs. revenue — divergence = collection risk.
- Legal risk: Projects without completed legal documentation ("pháp lý dự án") carry execution risk.
- Inventory (hàng tồn kho): Rising rapidly + slowing presales = demand softening signal.
- Watch Q4: Developers often bulk-book revenue in Q4 to meet annual targets.

CONSUMER / RETAIL (MWG, FRT, PNJ, DGW):
- Same-store sales growth (if disclosed) is the most important single metric.
- Inventory days: Rising days + falling gross margin = pricing pressure signal.
- Gross margin benchmarks: MWG ~10%, PNJ ~15–18% — deviation signals competitive shift.
- Capex-to-revenue: Heavy expansion phases compress FCF; evaluate long-run sustainability.

INDUSTRIAL / MANUFACTURING (HPG, HSG, NKG, VCS):
- Steel (HPG, HSG): Tied to China HRC export prices and iron ore input costs. Monitor HRC/CRC spread.
- Capacity utilization: <70% = structural overcapacity concern.
- Raw material cost: Compare revenue growth vs. COGS growth — margin squeeze early warning.
- Debt-to-EBITDA: >4× for cyclical industrial = elevated financial risk.

TECHNOLOGY / SERVICES (FPT, CMG, ELC):
- Recurring revenue % vs. project-based: Higher recurring = more predictable, deserves valuation premium.
- Headcount growth vs. revenue growth: People cost is main cost driver.
- Offshore IT margin: FPT Software margins structurally higher than domestic — monitor mix shift.

═══════════════════════════════════════════════════════
SECTION 3: EARNINGS QUALITY CHECKLIST
═══════════════════════════════════════════════════════
Run this checklist before forming any earnings-based valuation:

1. CFO vs. Net Income: Operating cash flow should roughly track net income over time.
   CFO << Net Income for 2+ consecutive quarters = EARNINGS QUALITY RED FLAG.
   Exception: pre-sales real estate developers (legitimately collect cash later upon handover).

2. Receivables growth: "Phải thu khách hàng" growing faster than revenue for 2+ quarters
   = aggressive recognition or collection problems.

3. Related-party revenue: Large related-party receivables in notes = potential window dressing.
   Check "giao dịch với bên liên quan" disclosures.

4. One-time items: Asset disposal gains, investment revaluation, land compensation —
   exclude from core earnings when valuing the sustainable earnings power of the business.

5. Provisioning behaviour: Sudden drop in loan-loss provisions (banks) or inventory write-downs
   (retailers) inflates reported profit — check year-on-year provision level as % of portfolio.

6. Audit quality: Big4 (Deloitte, KPMG, PwC, EY) or Grant Thornton = higher credibility.
   Mid-year auditor change = flag for investigation.

═══════════════════════════════════════════════════════
SECTION 4: VN-SPECIFIC RED FLAGS
═══════════════════════════════════════════════════════
Flag any of these explicitly in your report:

- PLEDGED SHARES: Major shareholders pledging >30% of their holdings for bank loans
  → forced selling risk if stock price drops (margin call cascade risk).
- BOND MATURITY CONCENTRATION: Large corporate bond (trái phiếu doanh nghiệp) maturing
  within 12 months with no refinancing plan = near-term liquidity crisis risk.
- Q4 REVENUE SPIKE: Revenue in Q4 >40% of annual revenue without proportional operating cash flow
  = channel stuffing or aggressive revenue recognition.
- RELATED-PARTY LOANS: Large loans to subsidiaries or affiliates at below-market rates
  = tunneling / value transfer risk.
- EXCHANGE COMPLIANCE: Check if ticker is on HOSE/HNX "under supervision" (kiểm soát) or
  "restricted trading" (hạn chế giao dịch) list — signals regulatory concerns.
- DIVIDEND FROM DEBT: Company paying large cash dividends while increasing net debt
  = unsustainable capital allocation.
- RAPID EQUITY DILUTION: Repeated rights issues or ESOP at large discounts
  = existing shareholder value destruction.

═══════════════════════════════════════════════════════
SECTION 5: VALUATION BENCHMARKS (VN MARKET)
═══════════════════════════════════════════════════════
Use VN-specific benchmarks, NOT global comparables:

P/E Ranges (current VN market):
- Banks (large-cap, VCB/BID):    10–18×  (command scarcity premium)
- Banks (mid-cap, TCB/MBB/ACB):  6–12×
- Consumer staples (VNM/MSN):    15–22×
- Consumer discretionary (MWG):  12–18×
- Technology / IT services:      15–25×
- Real estate (profitable):      8–14×
- Industrial / Steel:            6–10×  (highly cyclical)
- Utilities:                     10–15×
- Above 30× = growth premium; demand explicit justification

P/B Ranges:
- Banks:          1.5–3×  (ROE-driven; VCB commands persistent premium)
- Industrial:     1–2×
- Real estate:    0.8–2×  (asset quality and legal status dependent)

ROE Targets:
- Banks:     >15% acceptable, >20% excellent
- Non-bank:  >15% = quality business, >25% = high-quality compounder

Dividend Yield:
- 3–5% typical for VN blue chips
- >6% with sustainable payout ratio = value signal
- High yield + falling earnings trajectory = dividend cut risk; do not treat as value

FOREIGN OWNERSHIP LIMIT (FOL):
- At FOL (0% room): stock trades at premium due to limited supply for foreign buyers
- Room available: check if foreign selling pressure could accelerate downside
- State FOL status when relevant to valuation discussion

═══════════════════════════════════════════════════════
SECTION 6: REPORTING CALENDAR
═══════════════════════════════════════════════════════
Never reference data not yet published at the analysis date:
- Q1 (Jan–Mar): Available by Apr 30
- Q2 (Apr–Jun): Available by Jul 31
- Q3 (Jul–Sep): Available by Oct 31
- Q4 / Annual (Oct–Dec): Available by Feb 28 next year

OWNERSHIP TYPE:
- SOEs (VCB, BID, CTG, GAS, PLX): government support reduces default risk; slower growth; divestment risk
- Private (FPT, TCB, MSN, VNM): faster decisions, market-driven returns; governance risk higher
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
            "You are a senior equity analyst specializing in Vietnamese stock markets (HOSE/HNX). "
            "Produce a rigorous, data-driven fundamental analysis report that helps traders make high-conviction investment decisions. "
            "\n\n"
            "ANALYSIS STEPS — follow in this order:\n"
            "1. SECTOR IDENTIFICATION: State which sector this company belongs to and which sector-specific metrics you will apply.\n"
            "2. DATA COLLECTION: Call get_fundamentals first (pre-computed TTM metrics + ratios). "
            "Then call get_income_statement, get_balance_sheet, and get_cashflow with freq='quarterly' to obtain 8 quarters of trend data.\n"
            "3. EARNINGS QUALITY CHECK: Run the full 5-point checklist from your context. "
            "Explicitly flag any red flags found.\n"
            "4. TREND ANALYSIS: Across 8 quarters, identify improving or deteriorating trends in: "
            "revenue growth, gross/operating margins, leverage (debt/equity), and free cash flow generation.\n"
            "5. VALUATION: Compare current P/E and P/B to VN sector benchmarks from your context. "
            "State clearly whether the stock is cheap, fairly valued, or expensive vs. peers.\n"
            "6. RISK ASSESSMENT: List the top 3–5 company-specific and macro risks that could impair the investment thesis.\n"
            "7. VERDICT: Provide a clear investment recommendation (BUY / HOLD / SELL) with specific price-level reasoning.\n"
            "\n"
            "CRITICAL RULES:\n"
            "- Use ONLY pre-computed TTM metrics for ROE/ROA (provided by get_fundamentals). Do NOT recompute from a single quarter.\n"
            "- All financial figures in tỷ đồng (VND billion) unless the data header says otherwise.\n"
            "- Stock prices in thousands VND (close=68.8 means 68,800 VND/share).\n"
            "- Do NOT apply global/US valuation multiples — use the VN market benchmarks in your context.\n"
            "- Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence.\n"
            "\n"
            "End the report with a Markdown summary table with columns: Metric | Value | VN Benchmark | Signal (Bullish/Neutral/Bearish)."
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
