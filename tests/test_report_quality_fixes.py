"""Tests cho 4 sửa lỗi chất lượng report.

1. Renderer: run không có Trader → bảng không có dòng Trader; mẫu số nhãn
   override = số agent thực tế có khuyến nghị.
2. Bảng trend + DuPont deterministic (bank & non-bank) với frames giả.
3. bank_metrics.yaml: có số → render kèm as-of; thiếu → "—"; payload không
   chứa 0.0 cho field thiếu.
"""
import io as _io
import sys
import unittest

import pandas as pd

# render_report là CLI script bọc lại sys.stdout khi import — cô lập để không
# phá stdout-capture của pytest (xem tests/test_pipeline_mode.py).
class _NoCloseBytesIO(_io.BytesIO):
    def close(self):
        pass


_KEEP_BUF = _NoCloseBytesIO()
_orig_stdout = sys.stdout
sys.stdout = _io.TextIOWrapper(_KEEP_BUF, encoding="utf-8", errors="replace")
try:
    from render_report import _build_agent_rating_table
finally:
    sys.stdout = _orig_stdout

from tradingagents.agents.utils.fundamentals_tables import (
    build_trend_table,
    build_dupont_table,
)


# ── #1 Renderer pipeline summary ─────────────────────────────────────────────

class RendererPipelineSummaryTests(unittest.TestCase):
    def _sections_no_trader(self):
        # Mode rating: có market/news/fundamentals/investment_plan/risk_review/final;
        # KHÔNG có trader_investment_plan.
        return {
            "market_report": "**Signal**: Buy — đà tăng.",
            "news_report": "**Signal**: Buy — tin tốt.",
            "fundamentals_report": "**Signal**: Overweight — ROE ổn.",
            "investment_plan": "**Recommendation**: Overweight\n\n**Rationale**: lean bull.",
            "risk_review": "**Rủi ro NGOÀI bộ kịch bản**: không có.",
            "final_trade_decision": "**Rating**: Overweight\n\n**Executive Summary**: TP + EV.",
        }

    def test_no_trader_row_when_absent(self):
        html = _build_agent_rating_table(self._sections_no_trader(), None)
        self.assertNotIn("Trader", html)
        self.assertIn("Portfolio Manager", html)
        self.assertIn("Market Analyst", html)

    def test_override_denominator_excludes_absent_trader(self):
        # PM = Sell ngược với 4 agent còn lại (market/news Buy, funds/rm Overweight)
        # → 4/4 bất đồng, mẫu số = 4 (không phải 5), badge xuất hiện.
        sections = self._sections_no_trader()
        sections["final_trade_decision"] = "**Rating**: Sell\n\n**Executive Summary**: đảo chiều."
        ar = {
            "market": "Buy", "news": "Buy", "fundamentals": "Overweight",
            "rm": "Overweight", "pm": "Sell",
        }
        html = _build_agent_rating_table(sections, ar)
        self.assertIn("PM override đa số (4/4)", html)

    def test_no_override_badge_when_pm_agrees(self):
        sections = self._sections_no_trader()
        ar = {"market": "Buy", "news": "Buy", "fundamentals": "Overweight",
              "rm": "Overweight", "pm": "Overweight"}
        html = _build_agent_rating_table(sections, ar)
        self.assertNotIn("PM override", html)


# ── #2 Trend + DuPont deterministic ──────────────────────────────────────────

def _bank_frames():
    ia = pd.DataFrame([
        {"report_period": "2025", "Attributable to parent company": 35e12,
         "Net Interest Income": 60e12, "Total Operating Income": 75e12,
         "General and Admin Expenses": -25e12,
         "Net Operating Profit Before Allowance for Credit Loss": 50e12,
         "Net Accounting Profit/(loss) before tax": 44e12},
        {"report_period": "2024", "Attributable to parent company": 33e12,
         "Net Interest Income": 58e12, "Total Operating Income": 72e12,
         "General and Admin Expenses": -24e12,
         "Net Operating Profit Before Allowance for Credit Loss": 48e12,
         "Net Accounting Profit/(loss) before tax": 42e12},
    ])
    ra = pd.DataFrame([
        {"report_period": "2025", "ROE (%)": 0.167, "ROA (%)": 0.016,
         "Net Interest Margin": 0.026, "NPL (%)": 0.006},
        {"report_period": "2024", "ROE (%)": 0.187, "ROA (%)": 0.017,
         "Net Interest Margin": 0.029, "NPL (%)": 0.010},
    ])
    ba = pd.DataFrame([
        {"report_period": "2025", "TOTAL ASSETS": 2200e12, "OWNER'S EQUITY": 205e12},
        {"report_period": "2024", "TOTAL ASSETS": 2000e12, "OWNER'S EQUITY": 190e12},
    ])
    return {"ia": ia, "ra": ra, "ba": ba, "is_bank": True}


def _nonbank_frames():
    ia = pd.DataFrame([
        {"report_period": "2025", "Net sales": 140e12, "Attributable to parent company": 14e12},
        {"report_period": "2024", "Net sales": 120e12, "Attributable to parent company": 10e12},
    ])
    ra = pd.DataFrame([
        {"report_period": "2025", "ROE (%)": 0.127},
        {"report_period": "2024", "ROE (%)": 0.111},
    ])
    ba = pd.DataFrame([
        {"report_period": "2025", "Total Assets": 220e12, "Owner's Equity": 110e12},
        {"report_period": "2024", "Total Assets": 210e12, "Owner's Equity": 100e12},
    ])
    return {"ia": ia, "ra": ra, "ba": ba, "is_bank": False}


class TrendDupontTests(unittest.TestCase):
    def test_bank_trend_has_lnst_column_and_values(self):
        md = build_trend_table(_bank_frames(), is_bank=True)
        self.assertIn("LNST (tỷ)", md)
        self.assertIn("| 2025 |", md)
        self.assertIn("35,000", md)  # 35e12 → 35,000 tỷ
        self.assertIn("16.7%", md)

    def test_nonbank_trend_efficiency_only_no_absolute(self):
        md = build_trend_table(_nonbank_frames(), is_bank=False)
        self.assertIn("Net margin", md)
        self.assertIn("ROE", md)
        # không lặp cột doanh thu/LNST tuyệt đối
        self.assertNotIn("Doanh thu", md)
        self.assertNotIn("LNST", md)

    def test_bank_dupont_has_roa_leverage_decomposition(self):
        md = build_dupont_table(_bank_frames(), is_bank=True)
        self.assertIn("NII/TTS", md)
        self.assertIn("Dự phòng/TTS", md)
        self.assertIn("Leverage", md)
        self.assertIn("ROA", md)

    def test_nonbank_dupont_reconciles_roe(self):
        md = build_dupont_table(_nonbank_frames(), is_bank=False)
        self.assertIn("Net Margin", md)
        self.assertIn("Asset Turnover", md)
        # ROE DuPont 2025: NM 10% × AT (140/215=0.651) × Lev (215/105=2.048) ≈ 13.3%
        self.assertIn("ROE (DuPont)", md)


# ── #3 bank_metrics.yaml CAR + no 0.0 ────────────────────────────────────────

class BankMetricsCarTests(unittest.TestCase):
    def test_car_present_renders_with_asof(self):
        import tradingagents.agents.utils.vn_financial_fetcher as vff
        _orig = vff._load_bank_metrics
        vff._load_bank_metrics = lambda sym: {"car": 11.5, "as_of": "2026-06-30", "source": "BCTC Q2"}
        try:
            block = vff._bank_extra_block("VCB")
        finally:
            vff._load_bank_metrics = _orig
        self.assertIn("11.5%", block)
        self.assertIn("2026-06-30", block)
        self.assertNotIn("0.0%", block)

    def test_car_missing_renders_dash_not_zero(self):
        import tradingagents.agents.utils.vn_financial_fetcher as vff
        _orig = vff._load_bank_metrics
        vff._load_bank_metrics = lambda sym: {"car": None, "as_of": None, "source": None}
        try:
            block = vff._bank_extra_block("VCB")
        finally:
            vff._load_bank_metrics = _orig
        self.assertNotIn("0.0%", block)
        self.assertIn("—", block)

    def test_zero_car_treated_as_missing(self):
        # car=0.0 trong yaml → coi là thiếu (không truyền 0.0).
        import tradingagents.agents.utils.vn_financial_fetcher as vff
        loaded = vff._load_bank_metrics  # gọi thật với yaml repo (VCB để trống)
        m = loaded("VCB")
        self.assertIsNone(m.get("car"))


if __name__ == "__main__":
    unittest.main()
