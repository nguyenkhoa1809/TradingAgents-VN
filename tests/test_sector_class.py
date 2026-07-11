"""Tests cho phân loại 3 nhóm ngành (BANK/SECURITIES/GENERIC) — bảng
"Kết quả theo quý" đúng đặc thù ngành, không còn "—" cho bank.

- _is_securities nhận diện đúng CTCK, không nhận nhầm bank.
- Bank: chart_data ra TOI có số (không None), CIR = |opex|/TOI đúng công thức.
- Securities: DT hoạt động có số, biên LN đúng.
- Generic: không đổi so với hiện tại (regression).
- Field thiếu → None (renderer format "—"), không phải 0.
"""
import io as _io
import re
import sys
import unittest

import pandas as pd

from tradingagents.agents.utils.vn_financial_fetcher import (
    _is_bank, _is_securities, sector_class, _income_field_vnd, _efficiency_pct,
)

# render_report là CLI script bọc lại sys.stdout khi import — cô lập.
class _NoCloseBytesIO(_io.BytesIO):
    def close(self):
        pass


_KEEP_BUF = _NoCloseBytesIO()
_orig_stdout = sys.stdout
sys.stdout = _io.TextIOWrapper(_KEEP_BUF, encoding="utf-8", errors="replace")
try:
    from render_report import _build_financial_block
finally:
    sys.stdout = _orig_stdout


def _bank_ratio_df():
    return pd.DataFrame([{"Net Interest Margin": 0.026, "LDR (%)": 0.85}])


def _securities_ia():
    return pd.DataFrame([{
        "report_period": "2025", "Net sales": 12930.7e9,
        "Attributable to parent company": 4106.1e9,
        "Revenue in Brokerage services": 2000e9,
    }])


def _generic_ia():
    return pd.DataFrame([{
        "report_period": "2025", "Net sales": 156116.1e9,
        "Attributable to parent company": 15453.2e9,
    }])


class SecuritiesDetectionTests(unittest.TestCase):
    def test_recognizes_securities_signature_column(self):
        self.assertTrue(_is_securities(_securities_ia(), pd.DataFrame()))

    def test_does_not_misclassify_bank_or_generic(self):
        self.assertFalse(_is_securities(_generic_ia(), pd.DataFrame()))
        self.assertFalse(_is_securities(pd.DataFrame(), pd.DataFrame()))

    def test_sector_class_bank_wins_over_securities_check(self):
        # is_bank=True (đã xác định qua ratio_df) → BANK, không cần xét _is_securities.
        self.assertEqual(sector_class(True, _generic_ia(), pd.DataFrame()), "BANK")

    def test_sector_class_securities_and_generic(self):
        self.assertEqual(sector_class(False, _securities_ia(), pd.DataFrame()), "SECURITIES")
        self.assertEqual(sector_class(False, _generic_ia(), pd.DataFrame()), "GENERIC")


class IncomeAndEfficiencyTests(unittest.TestCase):
    def test_bank_income_is_toi_not_net_sales(self):
        row = pd.Series({"Total Operating Income": 72454.6e9, "Net sales": None})
        self.assertEqual(_income_field_vnd(row, "BANK"), 72454.6e9)

    def test_bank_cir_formula(self):
        row = pd.Series({"General and Admin Expenses": -6884.055e9})
        toi = 21179.81e9
        cir = _efficiency_pct(row, toi, None, "BANK")
        self.assertAlmostEqual(cir, 6884.055 / 21179.81 * 100, places=2)

    def test_bank_missing_opex_returns_none_not_zero(self):
        row = pd.Series({})
        self.assertIsNone(_efficiency_pct(row, 1000.0, 100.0, "BANK"))

    def test_securities_income_uses_net_sales(self):
        row = pd.Series({"Net sales": 3178.1e9})
        self.assertEqual(_income_field_vnd(row, "SECURITIES"), 3178.1e9)

    def test_securities_margin_formula(self):
        margin = _efficiency_pct(pd.Series({}), 3178.1e9, 1277.9e9, "SECURITIES")
        self.assertAlmostEqual(margin, 1277.9 / 3178.1 * 100, places=2)

    def test_generic_unchanged_margin_formula(self):
        margin = _efficiency_pct(pd.Series({}), 156116.1e9, 15453.2e9, "GENERIC")
        self.assertAlmostEqual(margin, 15453.2 / 156116.1 * 100, places=2)

    def test_missing_income_gives_none_not_zero(self):
        self.assertIsNone(_efficiency_pct(pd.Series({}), None, 100.0, "GENERIC"))
        self.assertIsNone(_efficiency_pct(pd.Series({}), 0, 100.0, "GENERIC"))


class RendererSectorBranchTests(unittest.TestCase):
    def _chart_data(self, sclass, is_bank=False):
        return {
            "sector_class": sclass, "is_bank": is_bank,
            "years": ["2025", "2024"], "revenue_bn": [100.0, 90.0],
            "netprofit_bn": [30.0, 25.0], "efficiency_pct": [30.0, 27.8],
            "pe": [10.0, 9.0], "pb": [1.5, 1.4], "roe_pct": [15.0, 14.0],
            "roa_pct": [1.5, 1.4], "nim_pct": [2.6, 2.5], "npl_pct": [0.8, 0.9],
            "quarters": ["2025-Q2", "2025-Q1"], "q_revenue_bn": [55.0, 45.0],
            "q_profit_bn": [16.0, 14.0], "q_efficiency_pct": [29.0, 31.0],
        }

    def _strip(self, html):
        import html as _h
        return _h.unescape(re.sub(r"<[^>]+>", " ", html))

    def test_bank_shows_toi_and_cir_no_dash(self):
        block = _build_financial_block(self._chart_data("BANK", is_bank=True))
        text = self._strip(block)
        self.assertIn("TOI", text)
        self.assertIn("CIR", text)
        self.assertNotIn("— | —", text)  # không phải toàn "—" như bug cũ

    def test_securities_shows_dt_hoat_dong_and_bien_ln(self):
        block = _build_financial_block(self._chart_data("SECURITIES"))
        text = self._strip(block)
        self.assertIn("DT hoạt động", text)
        self.assertIn("Biên LN", text)

    def test_generic_regression_doanh_thu_bien_ln(self):
        block = _build_financial_block(self._chart_data("GENERIC"))
        text = self._strip(block)
        self.assertIn("Doanh thu", text)
        self.assertIn("Biên LN", text)

    def test_missing_field_renders_dash_not_zero(self):
        cd = self._chart_data("BANK", is_bank=True)
        cd["efficiency_pct"] = [None, None]
        cd["q_efficiency_pct"] = [None, None]
        block = _build_financial_block(cd)
        self.assertNotIn(">0.0<", block)


if __name__ == "__main__":
    unittest.main()
