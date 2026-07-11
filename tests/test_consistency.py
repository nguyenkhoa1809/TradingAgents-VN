"""Tests cho self-consistency sampling (mode rating).

- Vote đa số + tie-break thận trọng.
- Consensus 2/3 hoặc dải EV vắt ranh giới → conviction hạ bậc.
- Checkpoint: Phase I không chạy lại — decision nodes gọi đúng (N−1) lần
  (sample #1 = base_state, không re-run).
- backtest DB: ghi đủ N+1 records, đúng cột mới (sample_id/n_samples/consensus/is_final).
"""
import io as _io
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

from tradingagents.graph.consistency import (
    vote_rating, aggregate_samples, ev_range_straddles, downgrade_conviction,
    resample_decisions, parse_ev_pct, parse_conviction_label,
)


# backtest.py là CLI script gọi sys.stdout.reconfigure() khi import — cô lập để
# không phá stdout-capture của pytest.
class _NoCloseBytesIO(_io.BytesIO):
    def close(self):
        pass


_KEEP_BUF = _NoCloseBytesIO()
_orig_stdout = sys.stdout
sys.stdout = _io.TextIOWrapper(_KEEP_BUF, encoding="utf-8", errors="replace")
try:
    import backtest as _bt_module  # noqa: F401  (import-time reconfigure isolated)
finally:
    sys.stdout = _orig_stdout


class VoteTests(unittest.TestCase):
    def test_unanimous(self):
        self.assertEqual(vote_rating(["Buy", "Buy", "Buy"]), "Buy")

    def test_majority(self):
        self.assertEqual(vote_rating(["Overweight", "Overweight", "Hold"]), "Overweight")

    def test_tie_two_way_picks_conservative(self):
        # Buy vs Sell hoà → Sell (thận trọng hơn)
        self.assertEqual(vote_rating(["Buy", "Sell"]), "Sell")
        self.assertEqual(vote_rating(["Overweight", "Underweight"]), "Underweight")

    def test_tie_three_way_picks_most_conservative(self):
        self.assertEqual(vote_rating(["Buy", "Overweight", "Hold"]), "Hold")


class ConvictionDowngradeTests(unittest.TestCase):
    def test_consensus_not_unanimous_downgrades(self):
        agg = aggregate_samples([
            {"rating": "Overweight", "ev_pct": 7.0, "conviction": "CAO"},
            {"rating": "Overweight", "ev_pct": 8.0, "conviction": "CAO"},
            {"rating": "Hold", "ev_pct": 6.0, "conviction": "CAO"},
        ])
        self.assertEqual(agg["consensus"], "2/3")
        self.assertFalse(agg["unanimous"])
        self.assertEqual(agg["final_conviction"], "TRUNG BÌNH")  # hạ từ CAO
        self.assertTrue(agg["conviction_downgraded"])

    def test_ev_straddle_downgrades_even_if_unanimous(self):
        # 3/3 cùng rating nhưng EV [2, 8] vắt ranh giới Hold(−5..5)/Overweight(5..12)
        agg = aggregate_samples([
            {"rating": "Overweight", "ev_pct": 2.0, "conviction": "CAO"},
            {"rating": "Overweight", "ev_pct": 6.0, "conviction": "CAO"},
            {"rating": "Overweight", "ev_pct": 8.0, "conviction": "CAO"},
        ])
        self.assertTrue(agg["unanimous"])
        self.assertTrue(agg["ev_straddles_band"])
        self.assertEqual(agg["final_conviction"], "TRUNG BÌNH")

    def test_unanimous_no_straddle_keeps_conviction(self):
        agg = aggregate_samples([
            {"rating": "Overweight", "ev_pct": 6.0, "conviction": "CAO"},
            {"rating": "Overweight", "ev_pct": 7.0, "conviction": "CAO"},
            {"rating": "Overweight", "ev_pct": 8.0, "conviction": "CAO"},
        ])
        self.assertTrue(agg["unanimous"])
        self.assertFalse(agg["ev_straddles_band"])
        self.assertEqual(agg["final_conviction"], "CAO")
        self.assertFalse(agg["conviction_downgraded"])


class CheckpointResampleTests(unittest.TestCase):
    def test_phase1_not_rerun_decision_nodes_called_n_minus_1(self):
        base = {
            "final_trade_decision": "**Rating**: Buy\n**Conviction**: CAO\nEV = ... = +10.0%",
            "pm_rating": "Buy", "investment_plan": "ip", "risk_review": "rr",
        }
        # Mỗi node trả 1 update dict; đếm số lần gọi.
        rm = MagicMock(return_value={"investment_plan": "ip2"})
        ro = MagicMock(return_value={"risk_review": "rr2"})
        pm = MagicMock(return_value={
            "final_trade_decision": "**Rating**: Hold\n**Conviction**: THẤP\nEV = ... = +1.0%",
            "pm_rating": "Hold",
        })
        samples = resample_decisions(base, 3, rm, ro, pm)
        self.assertEqual(len(samples), 3)      # #1 base + 2 resample
        self.assertEqual(rm.call_count, 2)     # N−1
        self.assertEqual(ro.call_count, 2)
        self.assertEqual(pm.call_count, 2)
        # sample #1 giữ nguyên từ base (không bị node ghi đè)
        self.assertEqual(samples[0]["rating"], "Buy")
        self.assertEqual(samples[1]["rating"], "Hold")


class BacktestDbRecordsTests(unittest.TestCase):
    def test_writes_n_plus_1_records_with_new_columns(self):
        bt = _bt_module
        with tempfile.TemporaryDirectory() as d:
            # Trỏ DB vào thư mục tạm.
            _orig_dir, _orig_db = bt.CALIBRATION_DIR, bt.CALIBRATION_DB
            from pathlib import Path
            bt.CALIBRATION_DIR = Path(d)
            bt.CALIBRATION_DB = Path(d) / "calibration_store_test.db"
            try:
                con = bt._init_db()
                fields = {"rating": "Overweight", "ev_pct": 6.0, "conviction": "TRUNG BÌNH",
                          "bull_prob": None, "base_prob": None, "bear_prob": None,
                          "entry_price": 50.0}
                # 3 sample records (is_final=0) + 1 tổng hợp (is_final=1)
                for sid in (1, 2, 3):
                    bt._save_record(con, "2026-07-11", "VCB", fields, "v-rating-sc3",
                                    sample_id=sid, n_samples=3, consensus="2/3", is_final=0)
                bt._save_record(con, "2026-07-11", "VCB", fields, "v-rating-sc3",
                                sample_id=None, n_samples=3, consensus="2/3", is_final=1)
                rows = con.execute(
                    "SELECT sample_id, n_samples, consensus, is_final FROM calibration_runs "
                    "ORDER BY id"
                ).fetchall()
                con.close()
            finally:
                bt.CALIBRATION_DIR, bt.CALIBRATION_DB = _orig_dir, _orig_db

        self.assertEqual(len(rows), 4)  # N + 1
        finals = [r for r in rows if r[3] == 1]
        samples = [r for r in rows if r[3] == 0]
        self.assertEqual(len(finals), 1)
        self.assertEqual(len(samples), 3)
        self.assertEqual(finals[0][0], None)          # aggregate: sample_id NULL
        self.assertEqual(finals[0][1], 3)             # n_samples
        self.assertEqual(finals[0][2], "2/3")         # consensus
        self.assertEqual(sorted(s[0] for s in samples), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
