from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
    get_financials_block,
    _FINANCIALS_CITE_RULE,
    extract_analyst_rating,
)
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market_router import is_vn_ticker

_VN_FETCHER_AVAILABLE = False
try:
    from tradingagents.agents.utils.vn_financial_fetcher import build_financials_payload
    _VN_FETCHER_AVAILABLE = True
except ImportError:
    pass

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

7. OUTLIER PERIOD DISSECTION: When a quarter/year is an extreme value (highest or
   lowest in the N-period window), you MUST dissect before citing it as evidence:
   (a) Separate core operating profit from: financial income, one-time gains/losses,
       provision reversals, asset revaluation, seasonality effects.
   (b) Compare LNST vs CFO for that specific period:
       CFO/LNST < 1.0x (or materially worse than the prior 4-quarter average) →
       flag explicitly: "Lợi nhuận nặng tính dồn tích — chưa chuyển hoá thành tiền mặt."
       Do NOT use this period as a thesis pillar without explaining the cash conversion gap.
   (c) Sector-specific decomposition (same logic, different line items):
       Banking → separate recurring NII from one-time fee/provision reversal
       Real estate → separate handover revenue from asset revaluation gains
       Industrial/Steel → separate spread margin from inventory write-back gains
       Consumer → separate same-store growth from new-store contribution
   (d) If the outlier quarter IS the forward anchor for valuation (annualised earnings),
       must state CFO/LNST ratio and sustainability judgement before proceeding.

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
        is_vn = is_vn_ticker(ticker)

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        # ── Single source of truth: canonical financials computed once at run
        #    start (A1). Fundamentals agent is a CONSUMER like every other agent
        #    — it cites this block, it does NOT build its own number tables.
        fin_block = get_financials_block(state)
        chart_json = state.get("financials_chart_json", "") or ""
        # Fallback for entry points that didn't pre-compute (tests / bare states).
        if not fin_block and is_vn and _VN_FETCHER_AVAILABLE:
            try:
                pay = build_financials_payload(ticker, trade_date=str(current_date))
                if not pay.get("error"):
                    fin_block = pay["block"]
                    chart_json = pay["chart_json"]
            except Exception:
                pass

        pre_loaded_block = (f"\n\n{fin_block}{_FINANCIALS_CITE_RULE}\n") if fin_block else ""

        # C5: Company profile pre-injected as ground truth để ngăn entity hallucination.
        # LLM được cung cấp danh sách tài sản/nhà máy thực tế TRƯỚC KHI phân tích.
        profile_block = state.get("company_profile_block", "") or ""
        if profile_block:
            pre_loaded_block += (
                "\n\n---\n"
                "## 🏢 HỒ SƠ CÔNG TY (NGUỒN GROUND TRUTH — VERIFIED)\n"
                "Thông tin dưới đây được lấy trực tiếp từ vnstock. "
                "Khi đề cập tên nhà máy, dự án, công ty con: "
                "CHỈ được dùng thực thể có trong hồ sơ này. "
                "KHÔNG được tự thêm tài sản/dự án không có trong danh sách.\n\n"
                f"{profile_block}\n---\n"
            )

        vn_context = _VN_FUNDAMENTALS_CONTEXT if is_vn else ""

        # ── BSR-style structured output prompt ─────────────────────────────
        if is_vn:
            output_format = (
                "\n\nYÊU CẦU CẤU TRÚC BÁO CÁO — xuất ra theo đúng thứ tự sau:\n\n"

                "## 📋 Tóm Tắt Đầu Tư (Executive Summary)\n"
                "- **Khuyến nghị**: STRONG BUY / BUY / HOLD / SELL / STRONG SELL\n"
                "- Luận điểm đầu tư cốt lõi (2–3 câu)\n"
                "- 2 xúc tác tích cực + 2 rủi ro chính\n\n"

                "## 📊 Kết Quả Tài Chính 5 Năm\n"
                "- Tham chiếu bảng dữ liệu pre-loaded ở trên\n"
                "- Nhận xét xu hướng doanh thu, biên lợi nhuận, tăng trưởng EPS\n"
                "- Đánh giá chất lượng lợi nhuận (earnings quality checklist 7 điểm)\n"
                "- **Phân tích chất lượng lợi nhuận**: Nếu có kỳ {outlier_period} là cực trị "
                  "(cao nhất/thấp nhất trong chuỗi), BẮT BUỘC: (a) tách lợi nhuận lõi "
                  "vs một lần (one-time); (b) so CFO/LNST kỳ đó với bình quân lịch sử — "
                  "nếu CFO/LNST < 1.0x: gắn cờ 'Lợi nhuận nặng tính dồn tích'; "
                  "(c) nêu rõ tính bền vững trước khi dùng kỳ này làm neo định giá.\n\n"

                "## 🎯 Định Giá Hiện Tại (bắt buộc tách 2 lớp)\n"
                "⚠️ **DÙNG SỐ ĐỊNH GIÁ TỪ KHỐI '🧮 ĐỊNH GIÁ DETERMINISTIC' đã tính sẵn ở "
                "trên. KHÔNG tự tính lại multiple/justified P/B/DDM/fair value bằng tay — "
                "chỉ CHỌN phương pháp phù hợp ngành, TRÍCH số từ khối đó, và DIỄN GIẢI.**\n"
                "Chọn phương pháp theo ngành:\n"
                "- Ngân hàng → **justified P/B** (từ payload) là phương pháp chính.\n"
                "- Mã cổ tức thực (GD-eligible / payout cao) → **DDM** (từ payload).\n"
                "- Sản xuất / phi tài chính → **P/E và EV/EBITDA ngành** (median thực từ payload).\n"
                "- Nếu payload gắn cờ 'DDM bỏ qua (mã tăng trưởng)' → KHÔNG dùng DDM, "
                  "chuyển sang justified P/B hoặc P/E ngành.\n"
                "**LỚP 1 — Giá trị trên số hiện tại (không giả định phục hồi):**\n"
                "- Trích fair value + upside từ khối định giá deterministic "
                  "(justified P/B × BVPS, hoặc P/E ngành × EPS TTM, hoặc DDM tùy ngành).\n"
                "- Ghi rõ: Fair value (hiện tại) = X nghìn đồng → upside = ±Y% (đã có trong payload)\n\n"
                "**LỚP 2 — Upside có điều kiện (chỉ khi có luận điểm phục hồi):**\n"
                "- Ghi RÕ điều kiện: {swing_variable} giả định ở mức nào, "
                  "LNST/biên forward = X; xác suất kịch bản = Y%\n"
                "- Ghi rõ: Fair value (kịch bản phục hồi) = X nghìn đồng → upside = ±Y%\n\n"
                "**⚠️ BẮT BUỘC — nếu upside hiện tại ≈ 0% (< ±5%) và phần lớn upside nằm ở kịch bản phục hồi:**\n"
                "Mở đầu mục bằng: '📌 Cổ phiếu đang ~fair value trên số hôm nay. "
                  "Đây là kèo phục hồi/optionality — upside phụ thuộc [điều kiện cụ thể].'\n"
                "KHÔNG được trình bày như 'cổ phiếu rẻ' hay 'định giá hấp dẫn' khi định giá hiện tại ≈ fair.\n\n"
                "**Quy tắc kỹ thuật:**\n"
                "- Mọi multiple target phải justify: P/B target GẮN ROE forward; "
                  "premium/discount so bình quân phải có lý do tường minh.\n"
                "- Hướng so sánh đúng số học: nếu target 12.0x thì KHÔNG viết "
                  "'cao hơn hiện tại 13.0x' (12 < 13, mâu thuẫn).\n"
                "- Upside/downside tổng hợp (weighted by scenario probability)\n\n"

                "## 📐 Phân Tích Độ Nhạy — Biến Xoay Chuyển Thesis\n"
                "**Bước 1 — Xác định swing variable**: Chọn 1–2 biến mà:\n"
                "  (a) Bull và Bear bất đồng nhiều nhất, HOẶC\n"
                "  (b) Có biên độ lịch sử lớn nhất tác động lên LNST/biên LN\n"
                "  Đặt tên biến rõ ràng (vd: {swing_variable}).\n\n"
                "**Bước 2 — Bảng sensitivity (bắt buộc):**\n"
                "| {swing_variable} | Thay đổi | Biên LN ước tính | LNST ước tính | "
                  "Fair value (hiện tại) | Upside/Downside |\n"
                "|---|---|---|---|---|---|\n"
                "| Base case | — | ...% | ...tỷ | ...nghìn | ±...% |\n"
                "| {swing_var} +10% | +10% | ... | ... | ... | ... |\n"
                "| {swing_var} −10% | −10% | ... | ... | ... | ... |\n"
                "| {swing_var} +20% | +20% | ... | ... | ... | ... |\n"
                "| {swing_var} −20% | −20% | ... | ... | ... | ... |\n\n"
                "**Bước 3 — Nhận xét**: Luận điểm Bull/Bear thay đổi thế nào khi "
                  "{swing_variable} đảo chiều? Ngưỡng nào thì đảo luận điểm (breakeven)?\n\n"

                "## ⚙️ Phân Tích DuPont\n"
                "- ROE = Net Margin × Asset Turnover × Financial Leverage\n"
                "- Giải thích điều gì đang thúc đẩy / kéo giảm ROE qua các năm\n\n"

                "## 🏭 Vị Thế Ngành & Yếu Tố Ngành Đặc Thù\n"
                "- Áp dụng sector-specific metrics từ context (NIM cho ngân hàng, "
                  "NPL, CASA; backlog cho BĐS; margin trend cho sản xuất...)\n"
                "- Lợi thế cạnh tranh (moat) nếu có\n\n"

                "## 🔢 Phân Tích Dòng Tiền\n"
                "- Operating CF vs. Net Income (chênh lệch lớn = red flag)\n"
                "- FCF, FCF Yield, CapEx intensity\n\n"

                "## ⚠️ Rủi Ro & Xúc Tác\n"
                "**Bear case** — 3 rủi ro chính (ghi rõ xác suất: Cao/TB/Thấp):\n"
                "**Bull case** — 3 xúc tác chính (ghi rõ timeline và tác động):\n\n"

                "## 📋 Bảng Tổng Hợp\n"
                "| Chỉ Số | Giá Trị | Benchmark VN | Tín Hiệu |\n"
                "|--------|---------|--------------|----------|\n"
                "(8–10 metrics quan trọng nhất, ít nhất bao gồm P/E, P/B, ROE, "
                "Net Margin, Net Debt/EBITDA, FCF Yield)\n"
                "⚠️ Cột **Benchmark VN** cho P/E, P/B, EV/EBITDA PHẢI lấy từ 'Sector "
                "multiples THỰC' (median ngành) trong khối định giá deterministic — "
                "KHÔNG tự bịa số 'ngành ~X lần' từ trí nhớ. Nếu payload không có median "
                "cho một chỉ số → ghi 'không có dữ liệu ngành'.\n"
            )
        else:
            output_format = (
                "\n\nEnd the report with a Markdown summary table: "
                "Metric | Value | Benchmark | Signal (Bullish/Neutral/Bearish)."
            )

        system_message = (
            "You are a senior equity analyst. "
            "Produce a rigorous, data-driven fundamental analysis report "
            "that helps traders make high-conviction investment decisions."
            + pre_loaded_block
            + "\n\n"
            "ANALYSIS STEPS:\n"
            "1. SECTOR: Identify sector and applicable sector-specific metrics.\n"
            "2. DATA: Call get_fundamentals first (TTM ratios). "
            "Then get_income_statement, get_balance_sheet, get_cashflow "
            "(freq='quarterly', 8 periods) for trend data. "
            "If PRE-LOADED DATA is provided above, use it as primary source — "
            "call tools only to fill gaps or verify.\n"
            "3. EARNINGS QUALITY: Run the 7-point checklist (including outlier period dissection). "
            "Identify any outlier period (highest/lowest in the 8-quarter window). "
            "If found: separate core profit from one-time items; compare CFO vs LNST for "
            "that period; flag 'accrual-heavy' if CFO/LNST < 1.0x vs historical average. "
            "NEVER use an unexamined outlier quarter as the annualised earnings base.\n"
            "4. TRENDS: 8-quarter trend in revenue growth, margins, leverage, FCF.\n"
            "5. SENSITIVITY: Identify 1–2 swing variables (biến xoay chuyển thesis). Build sensitivity table "
            "(±10%, ±20% on each) mapping to margin → LNST estimate → fair value → upside. "
            "State the breakeven level where the bull/bear thesis inverts.\n"
            "6. VALUATION: Two-layer valuation. Use the pre-computed '🧮 ĐỊNH GIÁ "
            "DETERMINISTIC' block (justified P/B, DDM, real sector medians, reverse-DCF) "
            "as the source of fair values — DO NOT recompute multiples/fair value by hand. "
            "Pick the method that fits the sector (bank→justified P/B; dividend stock→DDM; "
            "manufacturing→sector P/E + EV/EBITDA) and interpret. Lớp 1 = current numbers, "
            "Lớp 2 = conditional recovery scenario.\n"
            "7. RISKS: Top 3–5 company-specific + macro risks.\n"
            "8. VERDICT: Clear BUY/HOLD/SELL with price-level reasoning.\n"
            "\n"
            "RULES:\n"
            "- Financial figures in tỷ đồng (VND billion) unless noted otherwise.\n"
            "- Stock prices in nghìn đồng (thousands VND).\n"
            "- Use VN market benchmarks, NOT US/global comparables.\n"
            "- Never recompute TTM ROE/ROA from a single quarter.\n"
            "\n"
            "⛔ CẤM SELF-CITATION (bắt buộc): Bạn KHÔNG được đề cập tên analyst, tên CTCK, "
            "tên tổ chức tài chính hay bất kỳ nguồn bên ngoài nào (ví dụ: 'Analyst X từ "
            "Vietcap', 'theo báo cáo SSI Research') TRỪ KHI thông tin đó xuất hiện nguyên văn "
            "trong dữ liệu / tool output đã cung cấp cho lượt gọi này. Nếu không có tài liệu "
            "research/broker note trong context, KHÔNG được tự suy ra hoặc tạo ra một nguồn "
            "để minh họa hay xác nhận kết quả tính toán. Tạo citation giả là lỗi nghiêm trọng.\n"
            + output_format
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
        fundamentals_rating = None
        fundamentals_reason = None
        if len(result.tool_calls) == 0:
            report = result.content

            # I2: validate citations against tool outputs provided this turn
            try:
                from tradingagents.agents.utils.citation_validator import validate_citations
                tool_context = "\n".join(
                    m.content for m in state["messages"]
                    if hasattr(m, "tool_call_id") and isinstance(m.content, str)
                )
                report, _flagged = validate_citations(report, tool_context, "Fundamentals", ticker)
            except Exception:
                pass  # validator failure must never break the pipeline

            fundamentals_rating, fundamentals_reason = extract_analyst_rating(llm, report)
            # Embed chart data as a comment for render_report.py to extract
            if chart_json:
                report += f"\n<!-- VN_CHART_DATA {chart_json} -->"

        return {
            "messages": [result],
            "fundamentals_report": report,
            "fundamentals_analyst_rating": fundamentals_rating,
            "fundamentals_analyst_reason": fundamentals_reason,
        }

    return fundamentals_analyst_node
