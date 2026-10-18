"""
fiscal_calendar.py
===================
Công ty có năm tài chính (niên độ) không kết thúc 31/12 — vnstock_data gắn
report_period THEO QUÝ DƯƠNG LỊCH THUẦN TUÝ (Q1=Th1-3 ... Q4=Th10-12) bất kể
niên độ tự báo cáo của công ty (đã verify: vnstock_data/core/utils/transform.py
::generate_period() không hề biết niên độ thật — chỉ nối yearReport +
lengthReport). "Năm" trong annual frame (report_period = 'YYYY') do đó cũng là
năm DƯƠNG LỊCH Th1-12, KHÔNG phải FY thật của công ty.

Cùng pattern với Report project (Report/Update report/fiscal_calendar.py) —
khác ở chỗ áp trực tiếp lên các frame pandas (ratio/income/balance sheet/cash
flow) trả về từ vnstock_data (report_period 'YYYY' hoặc 'YYYY-Qn'), TRƯỚC khi
các module khác (fundamentals_tables, valuation_engine) gom nhóm theo năm —
sửa 1 lần tại nguồn (vn_financial_fetcher.fetch_vn_financial_context), mọi nơi
đọc frames dùng chung sẽ tự động đúng.

Chỉ CTD được xác nhận qua đối chiếu số liệu thật: FY2026 CTD = Q3/2025(295) +
Q4/2025(228) + Q1/2026(119) + Q2/2026(146) = 788 tỷ NPATMI — verify bằng dữ
liệu vnstock_data thật (Attributable to parent company, 2025-Q3..2026-Q2),
khớp tính đến 2026-08-06. Thêm ticker khác vào registry khi đã xác nhận thật
qua đối chiếu số liệu — đừng đoán từ tên ngành.
"""

from __future__ import annotations

import math

import pandas as pd

#: ticker -> tháng kết thúc niên độ tài chính (1-12). 12 = dương lịch bình
#: thường (đa số mã VN) — không cần liệt kê ở đây.
FISCAL_YEAR_END_MONTH = {
    "CTD": 6,   # Coteccons — niên độ Th7 năm N-1 đến Th6 năm N.
}

#: ticker -> năm dương lịch ĐẦU TIÊN mà cột "năm" phải dựng lại thành niên độ
#: thật (nhãn 'FYxxxx'). Năm trước mốc này giữ nguyên dương lịch — không lùi
#: xa hơn vì không chắc đủ dữ liệu quý để cộng dồn chính xác.
FISCAL_RECONSTRUCT_FROM = {
    "CTD": 2024,
}

#: Cột luôn lấy nguyên từ quý cuối niên độ (không cộng dồn, không rơi vào
#: flow_cols) dù đang gọi reconstruct kiểu "flow" — vd nhãn ticker lặp lại
#: mỗi quý, không phải số để cộng.
_ALWAYS_POINT_IN_TIME = ("ticker",)
_EXCLUDE_ALWAYS = ("report_period", "ticker")


def fiscal_end_month(ticker: str) -> int | None:
    return FISCAL_YEAR_END_MONTH.get((ticker or "").strip().upper())


def fiscal_cutover(ticker: str) -> int | None:
    return FISCAL_RECONSTRUCT_FROM.get((ticker or "").strip().upper())


def year_num(label: str) -> int:
    """Số năm từ 1 nhãn cột, chấp nhận cả 'FYxxxx' lẫn 'xxxx' thuần."""
    label = str(label)
    return int(label[2:]) if label.startswith("FY") else int(label)


def prev_label_candidates(label: str) -> list[str]:
    """2 khả năng nhãn năm liền trước `label` — 'FY{n-1}' (nếu năm trước cũng
    đã dựng lại) hoặc '{n-1}' thuần (nếu năm trước là mốc trước cutover).
    Caller tự chọn khả năng có mặt trong dict năm của mình."""
    n = year_num(label) - 1
    return [f"FY{n}", str(n)]


def min_quarters_needed(ticker: str, as_of_year: int, as_of_quarter: int) -> int | None:
    """Số quý (lùi từ hiện tại) cần fetch để chuỗi quý đủ dài dựng niên độ thật
    từ FISCAL_RECONSTRUCT_FROM[ticker] tới hiện tại. None nếu ticker không có
    trong registry.

    Caller PHẢI dùng số này (không phải n_quarters mặc định — thường chỉ 8-10
    quý) khi slice frame quý cho mục đích reconstruct — nếu không, quý xa nhất
    cần cho niên độ đầu tiên (cutover) sẽ bị cắt mất, forward-scan gãy ngay từ
    FY đầu tiên (dù các FY gần hơn thừa dữ liệu thật để dựng đúng)."""
    end_month = fiscal_end_month(ticker)
    cutover = fiscal_cutover(ticker)
    if not end_month or not cutover:
        return None
    first_y, first_q = fiscal_quarters_for(end_month, cutover)[0]
    span = (as_of_year - first_y) * 4 + (as_of_quarter - first_q) + 1
    return max(span, 4) + 2  # +2 quý đệm biên


def fiscal_quarters_for(end_month: int, fiscal_year: int) -> list[tuple[int, int]]:
    """4 kỳ (năm, quý) dương lịch cấu thành niên độ kết thúc tháng end_month
    của fiscal_year, theo thứ tự thời gian.

    end_month=6, fiscal_year=2025 -> [(2024,3),(2024,4),(2025,1),(2025,2)]
    """
    last_q = end_month // 3
    out, q, y = [], last_q, fiscal_year
    for _ in range(4):
        out.append((y, q))
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    out.reverse()
    return out


def _f(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def build_fiscal_row(qdf: pd.DataFrame, end_month: int, fiscal_year: int,
                      flow_cols=(), point_in_time_cols=(),
                      ratio_specs=()) -> "pd.Series | None":
    """Dựng 1 dòng niên độ thật (nhãn 'FYxxxx') từ 4 quý dương lịch cấu thành
    (qdf, cột report_period dạng 'YYYY-Qn'). Trả None nếu thiếu BẤT KỲ quý nào
    trong 4 quý — điều kiện dừng cho scan forward, KHÔNG suy đoán quý chưa có
    báo cáo thật.

    - flow_cols: cộng dồn 4 quý (doanh thu, LNST, CFO, CapEx...).
    - point_in_time_cols: lấy từ quý CUỐI niên độ, không cộng dồn (TA, VCSH,
      số CP, và mọi cột ratio frame — vnstock tính TTM tại mỗi quý nên quý
      cuối niên độ ĐÃ LÀ giá trị 4-quý cần lấy, không cần tự cộng lại).
    - ratio_specs: [(ratio_col, base_col), ...] — quy mỗi quý về tuyệt đối
      (base × ratio), cộng dồn tuyệt đối, rồi chia lại cho base đã cộng dồn
      (KHÔNG lấy trung bình cộng 4 tỷ lệ % — sai khi base thay đổi giữa quý).
    """
    if qdf is None or qdf.empty or "report_period" not in qdf.columns:
        return None
    idx = {str(p): row for p, row in zip(qdf["report_period"].astype(str),
                                          qdf.to_dict("records"))}
    labels = [f"{y}-Q{q}" for y, q in fiscal_quarters_for(end_month, fiscal_year)]
    rows = [idx.get(lb) for lb in labels]
    if any(r is None for r in rows):
        return None

    out = {}
    for col in flow_cols:
        if col in _EXCLUDE_ALWAYS:
            continue
        vals = [_f(r.get(col)) for r in rows]
        out[col] = sum(vals) if all(v is not None for v in vals) else None
    for ratio_col, base_col in ratio_specs:
        parts, base_sum, ok = [], 0.0, True
        for r in rows:
            b, rt = _f(r.get(base_col)), _f(r.get(ratio_col))
            if b is None or rt is None:
                ok = False
                break
            parts.append(b * rt)
            base_sum += b
        out[ratio_col] = (sum(parts) / base_sum) if (ok and base_sum) else None
    fye = rows[-1]
    for col in (*point_in_time_cols, *_ALWAYS_POINT_IN_TIME):
        if col == "report_period":
            continue
        out[col] = fye.get(col)
    out["report_period"] = f"FY{fiscal_year}"
    return pd.Series(out)


def reconstruct_annual_frame(annual_df: pd.DataFrame, quarterly_df: pd.DataFrame,
                              ticker: str, flow_cols=(), point_in_time_cols=(),
                              ratio_specs=()) -> pd.DataFrame:
    """Thay các dòng annual từ FISCAL_RECONSTRUCT_FROM[ticker] trở đi bằng
    niên độ thật 'FYxxxx' dựng từ 4 quý dương lịch cấu thành (quarterly_df).
    Scan FORWARD LIÊN TỤC (không chỉ lặp qua các năm annual_df đã có sẵn) tới
    khi thiếu 1 trong 4 quý cần thiết — vnstock có thể chưa có dòng "năm"
    dương lịch mới nhất (năm dương lịch chưa hết) nhưng 4 quý cấu thành niên
    độ mới nhất đã có đủ số liệu thật.

    Không đổi gì nếu ticker không có trong registry hoặc thiếu input.
    """
    end_month = fiscal_end_month(ticker)
    cutover = fiscal_cutover(ticker)
    if not end_month or not cutover or annual_df is None or annual_df.empty \
            or "report_period" not in annual_df.columns:
        return annual_df

    period = annual_df["report_period"].astype(str)
    is_plain = period.str.match(r"^\d{4}$")
    plain = annual_df[is_plain]
    keep = plain[plain["report_period"].astype(str).astype(int) < cutover]

    new_rows, fy = [], cutover
    while True:
        row = build_fiscal_row(quarterly_df, end_month, fy,
                                flow_cols=flow_cols,
                                point_in_time_cols=point_in_time_cols,
                                ratio_specs=ratio_specs)
        if row is None:
            break
        new_rows.append(row)
        fy += 1

    if not new_rows:
        return annual_df
    # Mới nhất trước — khớp quy ước hiện tại của mọi frame annual trong
    # codebase (.iloc[0] = năm/kỳ gần nhất).
    return pd.concat([pd.DataFrame(list(reversed(new_rows))), keep], ignore_index=True)


def reconstruct_flow_frame(annual_df: pd.DataFrame, quarterly_df: pd.DataFrame,
                            ticker: str, point_in_time_cols=()) -> pd.DataFrame:
    """Toàn bộ cột (trừ report_period/ticker) là FLOW — cộng dồn 4 quý. Dùng
    cho income statement / cash flow (mọi dòng đều là luồng trong kỳ báo cáo).

    `point_in_time_cols`: cột cần loại khỏi flow (lấy điểm cuối niên độ thay
    vì cộng dồn) — vd 'Cash and cash equivalents at the end of period' trong
    cash flow statement là SỐ DƯ, cộng 4 quý lại vô nghĩa.
    """
    if quarterly_df is None or quarterly_df.empty:
        return annual_df
    cols = [c for c in quarterly_df.columns
            if c not in _EXCLUDE_ALWAYS and c not in point_in_time_cols]
    return reconstruct_annual_frame(annual_df, quarterly_df, ticker,
                                     flow_cols=cols,
                                     point_in_time_cols=point_in_time_cols)


def reconstruct_point_in_time_frame(annual_df: pd.DataFrame, quarterly_df: pd.DataFrame,
                                     ticker: str) -> pd.DataFrame:
    """Toàn bộ cột (trừ report_period) là POINT-IN-TIME — lấy quý CUỐI niên
    độ, không cộng dồn. Dùng cho balance sheet (số dư tại 1 thời điểm) và
    ratio frame (vnstock đã tính theo TTM tại mỗi quý: P/E, P/B, ROE, ROA,
    BVPS, số CP lưu hành... — quý cuối niên độ CHÍNH LÀ giá trị 4-quý cần)."""
    if quarterly_df is None or quarterly_df.empty:
        return annual_df
    cols = [c for c in quarterly_df.columns if c != "report_period"]
    return reconstruct_annual_frame(annual_df, quarterly_df, ticker,
                                     point_in_time_cols=cols)


def fiscal_note(ticker: str) -> str:
    """Khối cảnh báo/hướng dẫn tiêm vào prompt cho ticker lệch niên độ — nói
    rõ cột 'FYxxxx' là niên độ thật ĐÃ CÓ ĐỦ 4 quý báo cáo (actual, không phải
    dự phóng), tránh LLM mô tả FY mới nhất bằng ngôn ngữ tương lai/kỳ vọng."""
    ticker = (ticker or "").strip().upper()
    end_month = fiscal_end_month(ticker)
    if not end_month or end_month == 12:
        return ""
    import calendar
    end_name = calendar.month_name[end_month]
    cutover = fiscal_cutover(ticker)
    return (
        f"=== NIÊN ĐỘ KẾ TOÁN — {ticker} ===\n"
        f"{ticker} có năm tài chính kết thúc ngày cuối tháng {end_month} "
        f"({end_name}), KHÔNG phải 31/12 như đa số công ty VN. Các cột năm "
        f"gắn nhãn 'FY{cutover}' trở đi trong các bảng DuPont/định giá/FCF "
        f"dưới đây đã được DỰNG LẠI đúng niên độ thật (cộng dồn 4 quý dương "
        f"lịch cấu thành niên độ) — dùng trực tiếp được, không cần tự quy đổi. "
        f"Cột năm trước 'FY{cutover}' vẫn là năm dương lịch Th1-12 thông "
        f"thường.\n"
        f"Mọi nhãn quý (2025-Q1, 2025-Q2...) vẫn là QUÝ DƯƠNG LỊCH THUẦN TUÝ "
        f"(Q1=Th1-3, Q2=Th4-6, Q3=Th7-9, Q4=Th10-12) — KHÔNG phải số thứ tự "
        f"quý {ticker} tự dùng trong báo cáo của chính họ.\n"
        f"Các năm 'FY' liệt kê là NĂM TÀI CHÍNH ĐÃ CÓ ĐỦ 4 QUÝ BÁO CÁO THỰC "
        f"TẾ (actual, đã công bố) — TUYỆT ĐỐI không mô tả FY mới nhất bằng "
        f"ngôn ngữ tương lai/kỳ vọng ('dự kiến', 'chưa có số liệu FY...'); chỉ "
        f"năm KHÔNG xuất hiện trong bảng mới thực sự là dự phóng.\n"
        f"=== HẾT NIÊN ĐỘ KẾ TOÁN ===")
