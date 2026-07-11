"""Unit tests cho các lớp nâng cấp của valuation_engine (L1/L2/L3).

Chạy: pytest tests/test_valuation_engine.py -v
Các hàm lõi là pure (không gọi API), test bằng data giả — không cần vnstock.
"""
import pandas as pd
import pytest

from tradingagents.agents.utils.valuation_engine import (
    pb_history_band,
    composite_fair_value,
    roe_forward,
    band_corridor_reliable,
    band_mid_demoted,
    apply_cross_method_rescue,
    _implied_pb,
)

_CFG = {"pb_band_quarters": 20, "pb_band_min_quarters": 8}
_CORR_LOW, _CORR_HIGH = 0.6, 1.5


# ── (a) percentile band với data giả ─────────────────────────────────────────

def test_pb_band_percentiles_with_synthetic_data():
    # rq với 12 quý P/B từ 1.0..2.1 (bước 0.1) → đủ depth, không fetch thêm.
    pb_vals = [1.0 + 0.1 * i for i in range(12)]  # 1.0 .. 2.1
    rq = pd.DataFrame({"P/B": pb_vals, "report_period": [f"2023-Q{i%4+1}" for i in range(12)]})

    bvps_k = 20.0
    pb_now = 1.5
    band = pb_history_band("TEST", "VCI", rq, bvps_k, pb_now, _CFG, "2026-07-11")

    assert "error" not in band
    assert band["n_quarters"] == 12
    # median của 1.0..2.1 (12 điểm) = trung bình 1.5 và 1.6 = 1.55
    assert band["pb_p50"] == pytest.approx(1.55, abs=1e-6)
    assert band["pb_p10"] < band["pb_p25"] < band["pb_p50"] < band["pb_p75"] < band["pb_p90"]
    # fair value band = percentile × BVPS
    assert band["fv_band_mid"] == pytest.approx(band["pb_p50"] * bvps_k)
    assert band["fv_band_low"] == pytest.approx(band["pb_p25"] * bvps_k)
    assert band["fv_band_high"] == pytest.approx(band["pb_p75"] * bvps_k)
    # percentile của pb_now=1.5: có 6 giá trị <= 1.5 (1.0..1.5) trên 12 = 50%
    assert band["pb_percentile_now"] == pytest.approx(50.0, abs=0.1)


def test_pb_band_insufficient_quarters_returns_error():
    rq = pd.DataFrame({"P/B": [1.2, 1.3, 1.4], "report_period": ["2025-Q1", "2025-Q2", "2025-Q3"]})
    # < 8 quý và fetch thêm sẽ fail (không có vnstock trong test) → error.
    band = pb_history_band("TEST", "VCI", rq, 20.0, 1.3, _CFG, "2026-07-11")
    assert "error" in band
    assert band["n_quarters"] < _CFG["pb_band_min_quarters"]


# ── (b) sanity corridor 2 chiều ──────────────────────────────────────────────

def test_sanity_outside_band_but_inside_corridor_is_reliable():
    # Band [P10=2.44, P90=3.54] (VCB de-rate). P/B hiện tại 2.18 → corridor
    # [1.31, 3.27]. Justified implied 1.62: NGOÀI band (<P10) nhưng TRONG corridor
    # → reliable (chính sách mới cứu mã đang de-rate).
    reliable, reason = band_corridor_reliable(
        1.62, p10=2.44, p90=3.54, pb_now=2.18,
        corridor_low=_CORR_LOW, corridor_high=_CORR_HIGH)
    assert reliable is True
    assert reason == ""


def test_sanity_outside_both_band_and_corridor_is_unreliable():
    # Justified P/B lạc quan cũ: implied 3.58 > P90 2.9 VÀ > 1.5×2.2=3.3 → loại.
    reliable, reason = band_corridor_reliable(
        3.58, p10=1.5, p90=2.9, pb_now=2.2,
        corridor_low=_CORR_LOW, corridor_high=_CORR_HIGH)
    assert reliable is False
    assert "P90" in reason and "P/B hiện tại" in reason  # nêu cả hai mốc


def test_sanity_missing_band_defaults_reliable():
    reliable, reason = band_corridor_reliable(
        5.0, p10=None, p90=None, pb_now=2.0,
        corridor_low=_CORR_LOW, corridor_high=_CORR_HIGH)
    assert reliable is True


def test_roe_forward_fades_when_declining():
    # ROE giảm: [0.10, 0.14, 0.18] (mới→cũ) → latest 0.10 < avg3y 0.14
    ra = pd.DataFrame({"ROE (%)": [0.10, 0.14, 0.18]})
    info = roe_forward(ra, coe=0.11)
    assert info["declining"] is True
    # ROE_fwd = 0.10 − 0.5×(0.14−0.10) = 0.08 → dưới COE 0.11 → floor tại COE
    assert info["floored"] is True
    assert info["roe_fwd"] == pytest.approx(0.11)


def test_roe_forward_no_fade_when_rising():
    ra = pd.DataFrame({"ROE (%)": [0.20, 0.16, 0.14]})  # latest 0.20 > avg 0.1667
    info = roe_forward(ra, coe=0.11)
    assert info["declining"] is False
    assert info["roe_fwd"] == pytest.approx((0.20 + 0.16 + 0.14) / 3)
    assert info["floored"] is False


# ── Cross-method rescue ──────────────────────────────────────────────────────

def test_rescue_via_live_anchor_within_tolerance():
    # Rule A (mạnh): sector bị loại nhưng fair value 36 trong 30% của anchor còn sống
    # justified 44.9 (ratio 1.25) → khôi phục. Đây chính là ca VCB kỳ vọng.
    methods = {
        "justified_pb": {"fair_value": 44.9, "reliable": True, "drop_kind": None},
        "sector":       {"fair_value": 36.0, "reliable": False, "drop_kind": "sanity"},
    }
    rescued = apply_cross_method_rescue(methods, rescue_tolerance=1.3)
    assert rescued == ["sector"]
    assert methods["sector"]["reliable"] is True
    assert "Justified P/B" in methods["sector"]["reason"]


def test_rescue_skipped_no_anchor_no_consensus():
    # sector bị loại nhưng anchor sống justified 80 lệch 2.2× (>1.3) và không có
    # method bị loại nào khác → vẫn loại (sanity check còn răng).
    methods = {
        "justified_pb": {"fair_value": 80.0, "reliable": True, "drop_kind": None},
        "sector":       {"fair_value": 36.0, "reliable": False, "drop_kind": "sanity"},
    }
    rescued = apply_cross_method_rescue(methods, rescue_tolerance=1.3)
    assert rescued == []
    assert methods["sector"]["reliable"] is False


def test_rescue_recovers_group_within_tolerance():
    # Rule B (yếu hơn): không anchor sống, 2 method cùng bị loại lệch 20%
    # (50 vs 60, ratio 1.2 ≤ 1.3) → khôi phục cả nhóm.
    methods = {
        "justified_pb": {"fair_value": 50.0, "reliable": False, "drop_kind": "sanity"},
        "sector":       {"fair_value": 60.0, "reliable": False, "drop_kind": "sanity"},
    }
    rescued = apply_cross_method_rescue(methods, rescue_tolerance=1.3)
    assert set(rescued) == {"justified_pb", "sector"}
    assert methods["justified_pb"]["reliable"] is True
    assert methods["sector"]["reliable"] is True
    assert methods["justified_pb"]["drop_kind"] == "rescued"


def test_rescue_skips_group_beyond_tolerance():
    # lệch 50% (40 vs 60, ratio 1.5 > 1.3) → không khôi phục
    methods = {
        "justified_pb": {"fair_value": 40.0, "reliable": False, "drop_kind": "sanity"},
        "sector":       {"fair_value": 60.0, "reliable": False, "drop_kind": "sanity"},
    }
    rescued = apply_cross_method_rescue(methods, rescue_tolerance=1.3)
    assert rescued == []
    assert methods["justified_pb"]["reliable"] is False
    assert methods["sector"]["reliable"] is False


def test_rescue_ignores_structural_drops():
    # 1 sanity + 1 structural (REAL_ESTATE) → chỉ 1 candidate → không rescue
    methods = {
        "justified_pb": {"fair_value": 50.0, "reliable": False, "drop_kind": "structural"},
        "sector":       {"fair_value": 55.0, "reliable": False, "drop_kind": "sanity"},
    }
    rescued = apply_cross_method_rescue(methods, rescue_tolerance=1.3)
    assert rescued == []


# ── Band mid demote khi percentile outlier ───────────────────────────────────

def test_band_mid_demoted_at_extreme_percentiles():
    assert band_mid_demoted(5) is True       # rẻ outlier
    assert band_mid_demoted(95) is True      # đắt outlier
    assert band_mid_demoted(10) is True      # ngưỡng dưới
    assert band_mid_demoted(90) is True      # ngưỡng trên
    assert band_mid_demoted(50) is False     # giữa band
    assert band_mid_demoted(None) is False


def test_composite_excludes_demoted_band_mid():
    # Percentile hiện tại = 5 → band mid bị hạ (reliable=False) → không vào composite
    # weights sau renormalize; chỉ còn justified + ddm.
    weights_cfg = {"BANK": {"justified_pb": 0.45, "ddm": 0.25, "pb_band": 0.30}}
    methods = {
        "justified_pb": {"fair_value": 50.0, "reliable": True},
        "ddm":          {"fair_value": 46.0, "reliable": True},
        "pb_band":      {"fair_value": 80.0, "reliable": band_mid_demoted(5) is False},
    }
    res = composite_fair_value(methods, "BANK", weights_cfg, pays_dividend=True)
    assert res["converged"] is True
    assert "pb_band" not in res["weights_used"]
    assert set(res["weights_used"].keys()) == {"justified_pb", "ddm"}


# ── Two-tier promotion (reference tier → composite khi composite tier < 2) ────

def test_promotion_pulls_reference_method_when_composite_thin():
    # BANK: justified reliable (composite tier), ddm vắng, band demoted → composite
    # tier chỉ còn 1. sector reliable (reference tier, không nằm trong BANK weights)
    # → được promote để hội tụ. Đây chính là ca VCB kỳ vọng.
    weights_cfg = {"BANK": {"justified_pb": 0.45, "ddm": 0.25, "pb_band": 0.30}}
    methods = {
        "justified_pb": {"fair_value": 44.0, "reliable": True},
        "ddm":          {"fair_value": None, "reliable": False},
        "pb_band":      {"fair_value": 80.0, "reliable": False},   # demoted
        "sector":       {"fair_value": 36.0, "reliable": True},    # reference tier
    }
    res = composite_fair_value(methods, "BANK", weights_cfg, pays_dividend=True)
    assert res["converged"] is True
    assert res["promoted"] == ["sector"]
    assert set(res["weights_used"].keys()) == {"justified_pb", "sector"}
    # justified giữ 0.45, sector nhận phần thiếu 0.55 → renormalize giữ nguyên (sum=1)
    assert res["weights_used"]["justified_pb"] == pytest.approx(0.45)
    assert res["weights_used"]["sector"] == pytest.approx(0.55)
    assert res["composite"] == pytest.approx(0.45 * 44.0 + 0.55 * 36.0)


def test_promotion_skipped_when_reference_unreliable():
    # sector reference tier nhưng reliable=False (ngoài corridor) → KHÔNG promote
    # → composite tier vẫn 1 → range low-confidence.
    weights_cfg = {"BANK": {"justified_pb": 0.45, "ddm": 0.25, "pb_band": 0.30}}
    methods = {
        "justified_pb": {"fair_value": 44.0, "reliable": True},
        "ddm":          {"fair_value": None, "reliable": False},
        "pb_band":      {"fair_value": 80.0, "reliable": False},
        "sector":       {"fair_value": 36.0, "reliable": False},   # unreliable
    }
    res = composite_fair_value(methods, "BANK", weights_cfg, pays_dividend=True)
    assert res["converged"] is False
    assert res["promoted"] == []
    assert res["composite"] is None
    assert res["tp_range_low"] == pytest.approx(36.0)
    assert res["tp_range_high"] == pytest.approx(80.0)


def test_promotion_real_estate_band_demoted_promotes_reliable_reference():
    # REAL_ESTATE base = {pb_band, sector}. band demoted → composite tier chỉ còn
    # sector. justified_pb reliable (reference tier) → promote → hội tụ.
    weights_cfg = {"REAL_ESTATE": {"pb_band": 0.60, "sector": 0.40}}
    methods = {
        "pb_band":      {"fair_value": 40.0, "reliable": False},  # demoted
        "sector":       {"fair_value": 55.0, "reliable": True},
        "justified_pb": {"fair_value": 50.0, "reliable": True},   # reference tier
        "ddm":          {"fair_value": None, "reliable": False},
    }
    res = composite_fair_value(methods, "REAL_ESTATE", weights_cfg, pays_dividend=True)
    assert res["converged"] is True
    assert res["promoted"] == ["justified_pb"]
    assert set(res["weights_used"].keys()) == {"sector", "justified_pb"}


# ── (c) composite renormalize khi 1 phương pháp bị loại ──────────────────────

def test_composite_renormalizes_when_method_dropped():
    weights_cfg = {
        "BANK": {"justified_pb": 0.45, "ddm": 0.25, "pb_band": 0.30},
    }
    methods = {
        "justified_pb": {"fair_value": 50.0, "reliable": True},
        "ddm":          {"fair_value": 40.0, "reliable": False},  # bị loại
        "pb_band":      {"fair_value": 45.0, "reliable": True},
    }
    res = composite_fair_value(methods, "BANK", weights_cfg, pays_dividend=True)
    assert res["converged"] is True
    assert res["n_used"] == 2
    # trọng số renormalize: 0.45/(0.45+0.30)=0.6, 0.30/0.75=0.4
    assert res["weights_used"]["justified_pb"] == pytest.approx(0.6)
    assert res["weights_used"]["pb_band"] == pytest.approx(0.4)
    assert res["composite"] == pytest.approx(0.6 * 50.0 + 0.4 * 45.0)


def test_composite_not_converged_returns_tp_range():
    # <2 reliable → composite điểm None NHƯNG có TP range [min,max] mọi fair value
    # (kể cả reliable=False), không phải chuỗi "không hội tụ" trống (change #4).
    weights_cfg = {"BANK": {"justified_pb": 0.45, "ddm": 0.25, "pb_band": 0.30}}
    methods = {
        "justified_pb": {"fair_value": 50.0, "reliable": True},
        "ddm":          {"fair_value": 40.0, "reliable": False},   # bị loại nhưng có fv
        "pb_band":      {"fair_value": 80.0, "reliable": False},   # demoted nhưng có fv
    }
    res = composite_fair_value(methods, "BANK", weights_cfg, pays_dividend=True)
    assert res["converged"] is False
    assert res["composite"] is None
    # range trải trên MỌI method có fair value: min 40, max 80
    assert res["tp_range_low"] == pytest.approx(40.0)
    assert res["tp_range_high"] == pytest.approx(80.0)


def test_composite_default_nodiv_drops_ddm():
    weights_cfg = {
        "DEFAULT":       {"sector": 0.40, "pb_band": 0.35, "ddm": 0.25},
        "DEFAULT_NODIV": {"sector": 0.55, "pb_band": 0.45},
    }
    methods = {
        "sector":  {"fair_value": 60.0, "reliable": True},
        "pb_band": {"fair_value": 50.0, "reliable": True},
        "ddm":     {"fair_value": 40.0, "reliable": True},  # có reliable nhưng route bỏ
    }
    res = composite_fair_value(methods, "DEFAULT", weights_cfg, pays_dividend=False)
    assert res["converged"] is True
    assert set(res["weights_used"].keys()) == {"sector", "pb_band"}
    assert res["weights_used"]["sector"] == pytest.approx(0.55)
    assert res["composite"] == pytest.approx(0.55 * 60.0 + 0.45 * 50.0)


# ── (d) route REAL_ESTATE loại justified P/B khỏi composite ──────────────────

def test_composite_real_estate_excludes_justified_and_ddm():
    weights_cfg = {
        "REAL_ESTATE": {"pb_band": 0.60, "sector": 0.40},
    }
    # justified_pb / ddm reliable=False (route BĐS gắn cờ không phù hợp)
    methods = {
        "justified_pb": {"fair_value": 80.0, "reliable": False,
                         "reason": "không phù hợp cho BĐS — cần RNAV"},
        "ddm":          {"fair_value": 70.0, "reliable": False,
                         "reason": "không phù hợp cho BĐS — cần RNAV"},
        "pb_band":      {"fair_value": 50.0, "reliable": True},
        "sector":       {"fair_value": 55.0, "reliable": True},
    }
    res = composite_fair_value(methods, "REAL_ESTATE", weights_cfg, pays_dividend=True)
    assert res["converged"] is True
    assert set(res["weights_used"].keys()) == {"pb_band", "sector"}
    # justified P/B (80) KHÔNG được tính vào composite dù fair_value cao
    assert res["composite"] == pytest.approx(0.60 * 50.0 + 0.40 * 55.0)
    assert res["composite"] < 60.0  # không bị kéo lên bởi justified 80
