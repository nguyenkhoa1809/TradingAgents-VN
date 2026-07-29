"""Tests cho batch_run.py — retry transient, skip permanent, verify post-batch.

Bối cảnh: lần batch 7 mã trước mất 1 mã không log gì — root cause là SystemExit
(vnstock rate-limit Guardian) không bị `except Exception` bắt, giết cả process.
Test ở đây xác nhận: SystemExit/transient được retry đúng số lần rồi thành công;
NoMarketDataError skip ngay không retry; verify step phát hiện đúng mã thiếu.
"""
import io as _io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# batch_run.py gọi sys.stdout.reconfigure() khi import — cô lập để không phá
# stdout-capture của pytest (cùng pattern đã dùng cho backtest.py trong
# test_consistency.py).
class _NoCloseBytesIO(_io.BytesIO):
    def close(self):
        pass


_KEEP_BUF = _NoCloseBytesIO()
_orig_stdout = sys.stdout
sys.stdout = _io.TextIOWrapper(_KEEP_BUF, encoding="utf-8", errors="replace")
try:
    import batch_run
finally:
    sys.stdout = _orig_stdout

from tradingagents.dataflows.symbol_utils import NoMarketDataError


class IsTransientTests(unittest.TestCase):
    def test_system_exit_is_transient(self):
        # SystemExit = vnstock rate-limit Guardian — phải retry, không phải skip.
        self.assertTrue(batch_run._is_transient(SystemExit("rate limit hit")))

    def test_timeout_message_is_transient(self):
        self.assertTrue(batch_run._is_transient(Exception("Read timed out")))

    def test_connection_error_is_transient(self):
        self.assertTrue(batch_run._is_transient(ConnectionError("connection reset by peer")))

    def test_no_market_data_error_is_not_transient(self):
        self.assertFalse(batch_run._is_transient(NoMarketDataError("XYZ not found")))

    def test_generic_exception_without_keyword_is_not_transient(self):
        self.assertFalse(batch_run._is_transient(ValueError("unexpected shape")))


class PropagateWithRetryTests(unittest.TestCase):
    def setUp(self):
        # Không thực sự chờ 5s/15s trong test.
        self._sleep_patch = patch.object(batch_run.time, "sleep", return_value=None)
        self._sleep_patch.start()
        self.logger = MagicMock()

    def tearDown(self):
        self._sleep_patch.stop()

    def test_transient_error_retries_then_succeeds(self):
        ta = MagicMock()
        ta.propagate.side_effect = [
            SystemExit("rate limit"),
            ("state", "Buy"),
        ]
        result = batch_run._propagate_with_retry(ta, "VCB", "2026-07-11", self.logger)
        self.assertEqual(result, ("state", "Buy"))
        self.assertEqual(ta.propagate.call_count, 2)

    def test_permanent_error_skips_without_retry(self):
        ta = MagicMock()
        ta.propagate.side_effect = NoMarketDataError("TCX not found")
        with self.assertRaises(NoMarketDataError):
            batch_run._propagate_with_retry(ta, "TCX", "2026-07-11", self.logger)
        self.assertEqual(ta.propagate.call_count, 1)  # không retry

    def test_transient_error_exhausts_retries_then_raises(self):
        ta = MagicMock()
        ta.propagate.side_effect = SystemExit("rate limit")
        with self.assertRaises(SystemExit):
            batch_run._propagate_with_retry(ta, "VCB", "2026-07-11", self.logger)
        self.assertEqual(ta.propagate.call_count, batch_run.MAX_RETRIES + 1)


class VerifyBatchTests(unittest.TestCase):
    def test_detects_missing_tickers(self):
        tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"]
        trade_date = "2026-07-11"
        with tempfile.TemporaryDirectory() as d:
            reports_dir = Path(d)
            # 5/7 mã có report, thiếu DDD và GGG.
            for t in ["AAA", "BBB", "CCC", "EEE", "FFF"]:
                out_dir = reports_dir / t
                out_dir.mkdir(parents=True)
                (out_dir / f"{t}_{trade_date}_deepseek_0930.html").write_text("<html></html>")

            result = batch_run.verify_batch(tickers, trade_date, reports_dir)
            self.assertEqual(result["total"], 7)
            self.assertEqual(sorted(result["missing"]), ["DDD", "GGG"])
            self.assertEqual(len(result["present"]), 5)

    def test_all_present(self):
        tickers = ["AAA", "BBB"]
        trade_date = "2026-07-11"
        with tempfile.TemporaryDirectory() as d:
            reports_dir = Path(d)
            for t in tickers:
                out_dir = reports_dir / t
                out_dir.mkdir(parents=True)
                (out_dir / f"{t}_{trade_date}_deepseek_0930.html").write_text("<html></html>")
            result = batch_run.verify_batch(tickers, trade_date, reports_dir)
            self.assertEqual(result["missing"], [])


class ValidateTickersTests(unittest.TestCase):
    def test_filters_invalid_tickers(self):
        with patch.object(batch_run, "_get_vn_listing",
                          return_value={"VCB": "Vietcombank", "HPG": "Hoa Phat"}):
            valid, invalid = batch_run.validate_tickers(["VCB", "HPG", "ZZZZ"])
        self.assertEqual(valid, ["VCB", "HPG"])
        self.assertEqual(invalid, ["ZZZZ"])

    def test_empty_listing_does_not_block(self):
        # Fetch listing lỗi/rỗng → fail-open, không chặn ticker nào.
        with patch.object(batch_run, "_get_vn_listing", return_value={}):
            valid, invalid = batch_run.validate_tickers(["VCB", "ZZZZ"])
        self.assertEqual(valid, ["VCB", "ZZZZ"])
        self.assertEqual(invalid, [])


if __name__ == "__main__":
    unittest.main()
