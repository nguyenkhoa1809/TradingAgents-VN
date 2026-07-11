"""
fundamentals_tables.py
======================
Bảng tài chính deterministic cho Fundamentals Analyst — cùng nguyên tắc với
valuation_engine: "Python tính — LLM diễn giải".

Dựng sẵn 2 bảng markdown từ frames đã fetch (ia/ra/ba), inject vào financials
payload; analyst CHỈ viết nhận xét xu hướng, KHÔNG tự dựng lại bảng (trước đây
analyst tự điền và bỏ trống LNST dù data có).

  1. build_trend_table    — xu hướng hiệu quả 5 năm.
       bank:     Năm | LNST | ROE | ROA | NIM | NPL
       non-bank: Năm | Net margin | ROE   (không lặp DT/LNST tuyệt đối vì bảng
                 "Tổng Hợp Kết Quả Tài Chính" của renderer đã có — tránh trùng).
       (Bảng bank GIỮ LNST vì bảng renderer cho bank không có cột LNST.)
  2. build_dupont_table   — phân rã ROE deterministic.
       non-bank: ROE = Net Margin × Asset Turnover × Leverage (từ ia + ba, dùng
                 TTS/VCSH BÌNH QUÂN 2 năm liền kề).
       bank:     ROE = ROA × Leverage; ROA phân rã NII/TTS, thu ngoài lãi/TTS,
                 chi phí HĐ/TTS, dự phòng/TTS (từ ia bank + ba bank).

Đơn vị tiền: tỷ VND. Thiếu số → "—" (KHÔNG bao giờ 0.0 cho dữ liệu thiếu).
"""

from __future__ import annotations

import math

import pandas as pd

from tradingagents.agents.utils.vn_financial_fetcher import _get


def _f(v):
    """float hoặc None (bỏ NaN/chuỗi rỗng)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _year(row) -> str:
    return str(row.get("report_period", ""))[:4]


def _bn(v):
    """VND → tỷ (1 chữ số)."""
    x = _f(v)
    return f"{x / 1e9:,.0f}" if x is not None else "—"


def _pct(v, d=1):
    """ratio (fraction) → phần trăm. vnstock ROE/ROA/NIM/NPL lưu dạng fraction."""
    x = _f(v)
    return f"{x * 100:.{d}f}%" if x is not None else "—"


def _xf(v, d=2):
    x = _f(v)
    return f"{x:.{d}f}x" if x is not None else "—"


def _rows_by_year(df: pd.DataFrame) -> dict:
    """{year: row(Series)} cho frame annual đã filter (report_period = 'YYYY')."""
    out = {}
    if df is None or df.empty or "report_period" not in df.columns:
        return out
    for _, r in df.iterrows():
        y = _year(r)
        if y.isdigit():
            out[y] = r
    return out


# ── 1. Bảng xu hướng 5 năm ──────────────────────────────────────────────────

def build_trend_table(frames: dict, is_bank: bool) -> str:
    ia = frames.get("ia", pd.DataFrame())
    ra = frames.get("ra", pd.DataFrame())
    ia_y, ra_y = _rows_by_year(ia), _rows_by_year(ra)
    years = sorted(set(ia_y) | set(ra_y), reverse=True)[:5]
    if not years:
        return ""

    lines = ["### Xu hướng hiệu quả 5 năm (tính bằng máy — chỉ nhận xét, KHÔNG dựng lại bảng)"]
    if is_bank:
        lines.append("| Năm | LNST (tỷ) | ROE | ROA | NIM | NPL |")
        lines.append("|-----|-----------|-----|-----|-----|-----|")
        for y in years:
            ir = ia_y.get(y)
            rr = ra_y.get(y)
            lnst = _bn(_get(ir, "Attributable to parent company", "Net profit/(loss) after tax", "Net profit")) if ir is not None else "—"
            roe = _pct(_get(rr, "ROE (%)", "ROE(%)")) if rr is not None else "—"
            roa = _pct(_get(rr, "ROA (%)", "ROA(%)")) if rr is not None else "—"
            nim = _pct(_get(rr, "Net Interest Margin")) if rr is not None else "—"
            npl = _pct(_get(rr, "NPL (%)", "NPL(%)")) if rr is not None else "—"
            lines.append(f"| {y} | {lnst} | {roe} | {roa} | {nim} | {npl} |")
    else:
        # Non-bank: chỉ chỉ số hiệu quả (DT/LNST tuyệt đối đã có ở bảng renderer).
        lines.append("| Năm | Net margin | ROE |")
        lines.append("|-----|------------|-----|")
        for y in years:
            ir = ia_y.get(y)
            rr = ra_y.get(y)
            margin = "—"
            if ir is not None:
                dt = _f(_get(ir, "Net sales", "Revenue"))
                lnst = _f(_get(ir, "Attributable to parent company", "Net profit"))
                if dt and lnst is not None and dt != 0:
                    margin = f"{lnst / dt * 100:.1f}%"
            roe = _pct(_get(rr, "ROE (%)", "ROE(%)")) if rr is not None else "—"
            lines.append(f"| {y} | {margin} | {roe} |")
    return "\n".join(lines)


# ── 2. DuPont deterministic ─────────────────────────────────────────────────

def _avg(cur, prev):
    """Bình quân 2 năm; nếu thiếu năm trước → dùng năm hiện tại (không bịa)."""
    c = _f(cur)
    p = _f(prev)
    if c is None:
        return None
    return (c + p) / 2 if p is not None else c


def build_dupont_table(frames: dict, is_bank: bool) -> str:
    ia = frames.get("ia", pd.DataFrame())
    ba = frames.get("ba", pd.DataFrame())
    ra = frames.get("ra", pd.DataFrame())
    ia_y, ba_y, ra_y = _rows_by_year(ia), _rows_by_year(ba), _rows_by_year(ra)
    years = sorted(set(ia_y) & set(ba_y), reverse=True)[:5]
    if not years:
        return ""

    _TA = ("Total Assets", "TOTAL ASSETS")
    _EQ = ("Owner's Equity", "OWNER'S EQUITY")

    if not is_bank:
        lines = [
            "### Phân rã DuPont (ROE = Net Margin × Asset Turnover × Leverage)",
            "*TTS/VCSH bình quân 2 năm liền kề; ROE thực lấy từ ratio để đối chiếu.*",
            "| Năm | Net Margin | Asset Turnover | Leverage | ROE (DuPont) | ROE thực |",
            "|-----|------------|----------------|----------|--------------|----------|",
        ]
        for y in years:
            ir, br = ia_y.get(y), ba_y.get(y)
            py = str(int(y) - 1)
            bprev = ba_y.get(py)
            dt = _f(_get(ir, "Net sales", "Revenue")) if ir is not None else None
            lnst = _f(_get(ir, "Attributable to parent company", "Net profit")) if ir is not None else None
            ta_avg = _avg(_get(br, *_TA) if br is not None else None,
                          _get(bprev, *_TA) if bprev is not None else None)
            eq_avg = _avg(_get(br, *_EQ) if br is not None else None,
                          _get(bprev, *_EQ) if bprev is not None else None)
            nm = (lnst / dt) if (dt and lnst is not None and dt != 0) else None
            at = (dt / ta_avg) if (dt is not None and ta_avg) else None
            lev = (ta_avg / eq_avg) if (ta_avg is not None and eq_avg) else None
            roe_dp = (nm * at * lev) if None not in (nm, at, lev) else None
            roe_act = _f(_get(ra_y.get(y), "ROE (%)", "ROE(%)")) if ra_y.get(y) is not None else None
            lines.append(
                f"| {y} | {_pct(nm)} | {_xf(at)} | {_xf(lev)} | {_pct(roe_dp)} | {_pct(roe_act)} |"
            )
        return "\n".join(lines)

    # Bank: ROE = ROA × Leverage; ROA phân rã theo % tổng tài sản bình quân.
    lines = [
        "### Phân rã DuPont ngân hàng (ROE = ROA × Leverage; ROA theo % TTS bình quân)",
        "*NII/thu ngoài lãi/chi phí HĐ/dự phòng đều quy về % TTS bình quân 2 năm.*",
        "| Năm | NII/TTS | Thu ngoài lãi/TTS | Chi phí HĐ/TTS | Dự phòng/TTS | ROA | Leverage | ROE |",
        "|-----|---------|-------------------|----------------|--------------|-----|----------|-----|",
    ]
    for y in years:
        ir, br = ia_y.get(y), ba_y.get(y)
        py = str(int(y) - 1)
        bprev = ba_y.get(py)
        ta_avg = _avg(_get(br, *_TA) if br is not None else None,
                      _get(bprev, *_TA) if bprev is not None else None)
        eq_avg = _avg(_get(br, *_EQ) if br is not None else None,
                      _get(bprev, *_EQ) if bprev is not None else None)

        nii = _f(_get(ir, "Net Interest Income")) if ir is not None else None
        toi = _f(_get(ir, "Total Operating Income")) if ir is not None else None
        ga = _f(_get(ir, "General and Admin Expenses")) if ir is not None else None
        pre_prov = _f(_get(ir, "Net Operating Profit Before Allowance for Credit Loss")) if ir is not None else None
        pretax = _f(_get(ir, "Net Accounting Profit/(loss) before tax",
                         "Net accounting profit/(loss) before tax")) if ir is not None else None
        lnst = _f(_get(ir, "Attributable to parent company",
                       "Net profit/(loss) after tax")) if ir is not None else None

        non_ii = (toi - nii) if (toi is not None and nii is not None) else None
        # opex bank thường ghi âm; lấy trị tuyệt đối để hiển thị % chi phí.
        opex = abs(ga) if ga is not None else None
        provision = (pre_prov - pretax) if (pre_prov is not None and pretax is not None) else None

        def _over_ta(x):
            return (x / ta_avg) if (x is not None and ta_avg) else None

        roa = _over_ta(lnst)
        lev = (ta_avg / eq_avg) if (ta_avg is not None and eq_avg) else None
        roe = (roa * lev) if (roa is not None and lev is not None) else None
        lines.append(
            f"| {y} | {_pct(_over_ta(nii),2)} | {_pct(_over_ta(non_ii),2)} | "
            f"{_pct(_over_ta(opex),2)} | {_pct(_over_ta(provision),2)} | "
            f"{_pct(roa,2)} | {_xf(lev)} | {_pct(roe)} |"
        )
    return "\n".join(lines)


def build_fundamentals_tables(frames: dict, is_bank: bool) -> str:
    """Ghép trend + DuPont thành 1 khối markdown để inject vào payload."""
    parts = [t for t in (build_trend_table(frames, is_bank),
                         build_dupont_table(frames, is_bank)) if t]
    if not parts:
        return ""
    return "\n\n".join(["", "---"] + parts + ["---"])
