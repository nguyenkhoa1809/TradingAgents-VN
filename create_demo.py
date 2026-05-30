"""Create demo report markdown files for testing render_report.py"""
from pathlib import Path

demo = Path("demo_report")
demo.mkdir(exist_ok=True)

files = {
    "market_report.md": """# Market Analysis - NVDA

## Price Action Summary
NVIDIA (NVDA) closed at **$875.40** on 2024-05-10, representing a **+2.3%** gain from the prior session.
The stock is trading **above** its 50-day MA ($830) and 200-day MA ($680), confirming a strong uptrend.

## Technical Indicators

| Indicator | Value | Signal |
|-----------|-------|--------|
| RSI (14)  | 67.2  | Bullish (not overbought) |
| MACD      | +12.4 | Bullish crossover |
| Bollinger Upper | $920 | Resistance |
| Bollinger Lower | $820 | Support |
| Volume    | 42.1M | Above 30-day avg (35M) |

## Support & Resistance
- **Key support**: $840 (previous breakout level)
- **Key resistance**: $900 (psychological level), $950 (ATH proximity)

## Conclusion
Technical picture is **constructive** with momentum indicators aligned to the upside.
""",

    "sentiment_report.md": """# Social Sentiment Analysis - NVDA

## Overall Sentiment Score: **78 / 100 (Bullish)**

Social media and retail investor sentiment is overwhelmingly positive ahead of earnings.

## Platform Breakdown

| Platform | Sentiment | Mentions (24h) |
|----------|-----------|----------------|
| Reddit (r/investing, r/stocks) | 82% Positive | 4,200+ |
| Twitter/X | 75% Positive | 18,500+ |
| StockTwits | 79% Bullish | 6,100+ |

## Key Themes
- **AI Chip Dominance**: Retail investors bullish on NVDA monopoly in AI training chips
- **Blackwell Architecture**: Excitement around next-gen GPU cycle
- **Earnings Anticipation**: Options positioning suggests expected +/-8% move

## Risk Signals
- Some concerns around valuation (P/E ~70x)
- Short interest at 1.2% of float — low but creeping up
""",

    "news_report.md": """# News Analysis - NVDA

## Headline Sentiment: **Positive** — Score 8.2 / 10

## Top Stories (Last 7 Days)

**1. "NVIDIA Reports Record Q1 Revenue"** — Reuters, May 10

NVDA beat consensus EPS by 18%, driven by Data Center segment growth of +427% YoY.

**2. "Microsoft, Google Double Down on NVDA Chips"** — Bloomberg, May 9

Hyperscalers confirmed multi-billion dollar H100/H200 orders for 2024-2025.

**3. "Jensen Huang: The next industrial revolution is here"** — WSJ, May 8

Bullish commentary on AI infrastructure buildout; stock up 4% on the day.

**4. "Export Control Risks to China Remain"** — FT, May 7

US government reviewing further restrictions. Management guided China revenue below 5%.

## Summary
Overwhelmingly positive catalysts from earnings beat and hyperscaler demand signals.
Export control headwind is noted but manageable given revenue de-risking.
""",

    "fundamentals_report.md": """# Fundamentals Analysis - NVDA

## Valuation Snapshot

| Metric | NVDA | Sector Median | Assessment |
|--------|------|---------------|------------|
| P/E (TTM) | 70.2x | 28.4x | Premium justified by growth |
| P/S | 35.1x | 8.2x | Elevated |
| EV/EBITDA | 55.8x | 18.6x | Premium |
| PEG Ratio | 0.83 | 1.50 | **Undervalued on growth basis** |

## Income Statement — Q1 FY2025

- **Revenue**: $26.0B (+262% YoY) — Record quarter
- **Gross Margin**: 78.4% (vs. 64.6% prior year) — Expanding
- **Operating Income**: $16.9B (+690% YoY)
- **EPS**: $6.12 (beat consensus $5.16 by 18%)

## Balance Sheet Strength
- Cash & Equivalents: **$31.4B** (fortress balance sheet)
- Long-term Debt: $8.5B (manageable)
- Free Cash Flow (TTM): **$28.7B** — Exceptional

## Conclusion
Fundamentals are **exceptional**. Revenue growth and margin expansion are unprecedented.
Premium valuation is justified by the PEG ratio and FCF trajectory.
""",

    "investment_plan.md": """# Research Team Decision

## Bull Case (Bull Researcher)

NVDA is the **picks-and-shovels play** of the AI revolution. Key arguments:

- Monopolistic position in AI training (80%+ market share in data center GPUs)
- Massive ecosystem moat via CUDA — competitors need 5-10 years to replicate
- Revenue visibility: order backlog extends 12-18 months
- Gross margin expansion signals pricing power rarely seen in semiconductors

**Price Target**: $1,100 (12-month) — 26% upside

## Bear Case (Bear Researcher)

Risks the market may be under-pricing:

- Cyclicality: semiconductor capex cycles are notoriously volatile
- AMD MI300X gaining traction in inference workloads
- Custom ASICs (Google TPU, Amazon Trainium) as long-term competitive threat
- Hyperscaler concentration risk — top 4 customers = 45% of revenue

**Fair Value Bear Case**: $650 — 26% downside risk

## Research Manager Decision

After weighing both sides:

> The bull case is **structurally stronger** given NVDA's moat and near-term earnings momentum.
> Position sizing should reflect elevated valuation and cyclical risk.

**Recommendation**: **BUY** with a 12-month target of **$1,050** (+20% upside).
Suggested position size: 5-7% of portfolio.
""",

    "trader_investment_plan.md": """# Trading Team Plan

## Signal: BUY

Given the bullish fundamental and technical backdrop:

### Entry Points
- **Primary entry**: $865-875 (current range) — 60% of planned position
- **Secondary entry**: $840 (50-day MA retest) — 40% on dip

### Position Sizing
- Recommended allocation: **5-6% of portfolio**
- Risk per trade: max 2% of total portfolio

### Stop Loss
- Hard stop: **$820** (below key support and 50-day MA)
- Risk per share: ~$55 (6.3% from primary entry)

### Profit Targets

| Target | Price | Action |
|--------|-------|--------|
| T1 | $950 | Take 30% off |
| T2 | $1,000 | Take 30% off |
| T3 | $1,100 | Trail remaining |

### Timeline
Expected to play out over **3-6 months** as next earnings cycle and Blackwell ramp catalyze re-rating.
""",

    "final_trade_decision.md": """# Portfolio Manager Decision

## Final Signal: **BUY**

After reviewing all analyst reports, research team debate, and the trading plan:

### Risk-Adjusted Assessment

| Risk Factor | Severity | Mitigation |
|-------------|----------|------------|
| Valuation premium | Medium | PEG <1x justifies premium |
| Cyclicality | Medium | Diversify across AI value chain |
| China export | Low-Medium | De-risked to <5% revenue |
| Competitive | Low (near-term) | CUDA moat is multi-year defensible |

### Portfolio Action

**INITIATE LONG POSITION — BUY**

- **Entry**: $865-875 range
- **Position size**: 5% of portfolio
- **Stop loss**: $820 (-6%)
- **12-month target**: $1,050 (+20%)
- **Risk/Reward**: 1:3.2 (favorable)

### Rationale

NVIDIA represents the highest-conviction AI infrastructure play with:

1. Unmatched execution and market share dominance
2. Earnings momentum that continues to surprise to the upside
3. A defensible moat via CUDA ecosystem
4. Management credibility and capital allocation discipline

The risk/reward at current prices remains **highly attractive** for a 5% position.

**FINAL DECISION: BUY NVDA — 5% portfolio allocation.**
""",
}

for fname, content in files.items():
    (demo / fname).write_text(content, encoding="utf-8")

print(f"Demo report files created in: {demo.resolve()}")
for f in demo.iterdir():
    print(f"  {f.name}")
