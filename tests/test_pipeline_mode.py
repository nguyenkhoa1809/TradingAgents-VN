"""Tests cho pipeline_mode (rating vs full) — Task tái cấu trúc 2 mode.

- Graph build: mode rating có Risk Officer, không Trader/risk debators;
  mode full giữ đồ thị cũ nguyên vẹn.
- Risk Officer schema render với dữ liệu giả.
- thesis_tracker parse được report không có section Trader (mode rating).
- Env override TRADINGAGENTS_PIPELINE_MODE=full → config full.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from langgraph.prebuilt import ToolNode

from tradingagents.graph.setup import GraphSetup
from tradingagents.graph.conditional_logic import ConditionalLogic
from tradingagents.agents.utils.agent_utils import get_stock_data

# render_report / thesis_tracker là CLI script: khi import chúng THAY sys.stdout
# bằng io.TextIOWrapper(sys.stdout.buffer) — chiếm quyền sở hữu buffer, phá capture
# của pytest. Cô lập: swap stdout sang 1 dummy có buffer no-op-close (không đóng khi
# wrapper cũ bị GC) trước khi import; giữ reference _KEEP_BUF cho buffer sống; rồi
# khôi phục stdout gốc của pytest.
import io as _io


class _NoCloseBytesIO(_io.BytesIO):
    def close(self):  # wrapper cũ bị GC sẽ gọi close() — bỏ qua để buffer sống
        pass


_KEEP_BUF = _NoCloseBytesIO()
_orig_stdout = sys.stdout
sys.stdout = _io.TextIOWrapper(_KEEP_BUF, encoding="utf-8", errors="replace")
try:
    from render_report import build_html
    from thesis_tracker import parse_report
finally:
    sys.stdout = _orig_stdout


def _build_workflow(pipeline_mode):
    """Build (uncompiled) workflow với LLM/tool giả để kiểm tra node wiring."""
    llm = MagicMock()
    llm.with_structured_output.return_value = MagicMock()
    llm.bind_tools.return_value = MagicMock()
    # Tool node thật (rẻ) để add_node nhận Runnable hợp lệ.
    tn = ToolNode([get_stock_data])
    tool_nodes = {k: tn for k in ("market", "social", "news", "fundamentals")}
    setup = GraphSetup(
        quick_thinking_llm=llm,
        deep_thinking_llm=llm,
        tool_nodes=tool_nodes,
        conditional_logic=ConditionalLogic(),
        analyst_thinking_llm=llm,
        pipeline_mode=pipeline_mode,
    )
    return setup.setup_graph(["market", "social", "news", "fundamentals"])


class GraphWiringTests(unittest.TestCase):
    def test_rating_mode_has_risk_officer_no_trader_or_debators(self):
        nodes = set(_build_workflow("rating").nodes.keys())
        self.assertIn("Risk Officer", nodes)
        self.assertIn("Portfolio Manager", nodes)
        self.assertIn("Research Manager", nodes)
        for absent in ("Trader", "Aggressive Analyst", "Neutral Analyst",
                       "Conservative Analyst"):
            self.assertNotIn(absent, nodes, f"{absent} phải vắng ở mode rating")

    def test_full_mode_keeps_original_graph(self):
        nodes = set(_build_workflow("full").nodes.keys())
        for present in ("Trader", "Aggressive Analyst", "Neutral Analyst",
                        "Conservative Analyst", "Portfolio Manager"):
            self.assertIn(present, nodes, f"{present} phải có ở mode full")
        self.assertNotIn("Risk Officer", nodes)


class RiskReviewSchemaTests(unittest.TestCase):
    def test_render_risk_review_with_fake_data(self):
        from tradingagents.agents.schemas import RiskReview, render_risk_review
        review = RiskReview(
            risks_outside_scenarios="Cổ đông lớn thoái vốn — impact ~10% fair value.",
            execution_constraints="Days-to-liquidate 4 ngày; room ngoại còn 12%.",
            falsification_conditions="NIM quý tới < 3.0%; giá thủng 25.0.",
        )
        md = render_risk_review(review)
        self.assertIn("Rủi ro NGOÀI bộ kịch bản", md)
        self.assertIn("Ràng buộc thực thi", md)
        self.assertIn("Điều kiện falsify", md)
        self.assertIn("NIM quý tới", md)


class ThesisTrackerNoTraderTests(unittest.TestCase):
    def test_parse_report_without_trader_section(self):
        """Report mode rating (không Trader, có risk_review) vẫn parse được PM."""
        sections = {
            "market_report": "Xu hướng tăng.",
            "fundamentals_report": "ROE ổn định.",
            "investment_plan": "**Recommendation**: Hold\n\n**Rationale**: cân bằng.",
            # KHÔNG có trader_investment_plan (mode rating)
            "risk_review": "**Rủi ro NGOÀI bộ kịch bản**: không có trọng yếu.",
            "final_trade_decision": (
                "**Rating**: Hold\n**Conviction**: TRUNG BÌNH — dải EV hẹp.\n\n"
                "**Executive Summary**: TP 40.0 nghìn đ, EV +2%. Giữ vị thế.\n\n"
                "**Investment Thesis**: định giá hợp lý."
            ),
        }
        html = build_html("VCB", "2026-07-11", sections, "2026-07-11 10:00:00")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "VCB_2026-07-11_deepseek-pro_rating.html"
            p.write_text(html, encoding="utf-8")
            row = parse_report(p)
        self.assertIsNotNone(row)
        self.assertEqual(row["signal"], "HOLD")


class EnvOverrideTests(unittest.TestCase):
    def test_env_override_sets_full_mode(self):
        from tradingagents.default_config import _apply_env_overrides
        prev = os.environ.get("TRADINGAGENTS_PIPELINE_MODE")
        os.environ["TRADINGAGENTS_PIPELINE_MODE"] = "full"
        try:
            cfg = _apply_env_overrides({"pipeline_mode": "rating"})
            self.assertEqual(cfg["pipeline_mode"], "full")
        finally:
            if prev is None:
                os.environ.pop("TRADINGAGENTS_PIPELINE_MODE", None)
            else:
                os.environ["TRADINGAGENTS_PIPELINE_MODE"] = prev


if __name__ == "__main__":
    unittest.main()
