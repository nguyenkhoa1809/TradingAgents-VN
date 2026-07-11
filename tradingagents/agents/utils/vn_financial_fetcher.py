"""
vn_financial_fetcher.py
Pre-fetches structured financial data from vnstock_data for VN tickers.

Usage in agents:
    from tradingagents.agents.utils.vn_financial_fetcher import fetch_vn_financial_context
    ctx = fetch_vn_financial_context("VCB")
    if not ctx["error"]:
        inject ctx["summary_md"] into LLM prompt
        append ctx["chart_json"] to report text for render_report.py to pick up

Usage in render_report.py:
    from tradingagents.agents.utils.vn_financial_fetcher import render_vn_charts_html
    charts_html = render_vn_charts_html(chart_data_dict)
"""

import json
import time
import math
import pandas as pd
from datetime import datetime


# ── helpers ────────────────────────────────────────────────────────────────

def _safe(fn, *args, retries=2, delay=1.0, **kwargs):
    for i in range(retries + 1):
        try:
            r = fn(*args, **kwargs)
            return r if isinstance(r, pd.DataFrame) else pd.DataFrame()
        except Exception:
            if i < retries:
                time.sleep(delay)
    return pd.DataFrame()


def _is_bank(ratio_df: pd.DataFrame) -> bool:
    for col in ("Net Interest Margin", "LDR (%)"):
        if col in ratio_df.columns:
            vals = pd.to_numeric(ratio_df[col], errors="coerce").dropna()
            if len(vals) > 0 and vals.abs().sum() > 0:
                return True
    return False


def _is_securities(ia: pd.DataFrame, iq: pd.DataFrame) -> bool:
    """CTCK có template BCTC riêng (TT210) với dòng doanh thu môi giới/margin/FVTPL
    KHÔNG xuất hiện ở ngành khác — cột 'Revenue in Brokerage services' là chữ ký
    đặc trưng, đáng tin hơn dò tên ICB (không cần fetch industry map thêm)."""
    for df in (ia, iq):
        if df is not None and not df.empty and "Revenue in Brokerage services" in df.columns:
            return True
    return False


def sector_class(is_bank: bool, ia: pd.DataFrame, iq: pd.DataFrame) -> str:
    """Nguồn phân loại ngành DÙNG CHUNG cho bảng năm/quý (fetcher) + renderer.

    "BANK" | "SECURITIES" | "GENERIC". is_bank tái dùng kết quả _is_bank() đã có
    (tránh tính 2 lần khác nhau ở 2 chỗ) — chỉ thêm lớp SECURITIES lên trên.
    """
    if is_bank:
        return "BANK"
    if _is_securities(ia, iq):
        return "SECURITIES"
    return "GENERIC"


def _income_field_vnd(row, sec_class: str):
    """Giá trị 'thu nhập' theo ngành (VND thô, chưa quy tỷ).

    BANK: TOI ('Total Operating Income' — field tổng có sẵn, không cần cộng
    cấu phần). SECURITIES: 'Net sales' ('OPERATING SALES') — đã kiểm chứng thực
    tế (SSI/VCI/VND, 10 quý liên tiếp) là tổng doanh thu HĐ (môi giới + cho vay
    margin + FVTPL/HTM/AFS + tư vấn/bảo lãnh) do vnstock tính sẵn — dùng trực
    tiếp, không tự cộng lại các dòng con (nhiều field cũ '(Before 2016)' không
    ổn định qua thời gian). GENERIC: 'Net sales'/'Revenue' như cũ.
    """
    if sec_class == "BANK":
        return _get(row, "Total Operating Income")
    return _get(row, "Net sales", "Revenue")


def _efficiency_pct(row, income_vnd, lnst_vnd, sec_class: str):
    """Chỉ số hiệu quả theo ngành, tính sẵn (renderer chỉ format, không tính lại).

    BANK: CIR = |Chi phí hoạt động| / TOI × 100 — THẤP = tốt (ngược chiều biên
    LN thông thường). SECURITIES/GENERIC: biên LN ròng = LNST / thu nhập × 100
    — CAO = tốt. Thiếu input → None (renderer hiển thị '—', không phải 0).

    Vì sao SECURITIES dùng biên LN thay vì CIR (dù cùng là ngành tài chính với
    BANK): CIR có ý nghĩa khi MẪU SỐ (thu nhập hoạt động) tương đối ỔN ĐỊNH qua
    các kỳ, để chi phí/thu nhập phản ánh đúng hiệu quả VẬN HÀNH — đúng với bank
    (thu nhập lãi thuần ổn định, ít biến động thị trường). CTCK thì thu nhập
    hoạt động (môi giới, tự doanh FVTPL, lãi margin) DAO ĐỘNG MẠNH theo diễn
    biến thị trường mỗi quý — CIR sẽ nhảy loạn không do vận hành tốt/xấu mà do
    thị trường lên/xuống, gây hiểu sai. Biên LN ròng (LNST/thu nhập) là chỉ số
    chuẩn ngành CTCK, phản ánh đúng bản chất kinh doanh biến động theo thị
    trường thay vì giả định nền chi phí cố định như bank.
    """
    if sec_class == "BANK":
        opex = _get(row, "General and Admin Expenses")
        if opex is None or not income_vnd:
            return None
        return abs(float(opex)) / float(income_vnd) * 100
    if income_vnd and lnst_vnd is not None:
        return float(lnst_vnd) / float(income_vnd) * 100
    return None


def _pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _x(v, d=2) -> str:
    try:
        return f"{float(v):,.{d}f}x"
    except (TypeError, ValueError):
        return "—"


def _num(v, d=1) -> str:
    try:
        f = float(v)
        if abs(f) >= 1000:
            return f"{f:,.0f}"
        return f"{f:,.{d}f}"
    except (TypeError, ValueError):
        return "—"


def _bn(v) -> str:
    """Format raw VND value into tỷ (billions)."""
    try:
        return f"{float(v) / 1e9:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _f_num(v):
    """float hoặc None (dùng để phân biệt 0.0 thật vs thiếu)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(row, *keys):
    """Return first non-null value from row for given keys."""
    for k in keys:
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return None


def _filter(df: pd.DataFrame, kind: str, n: int) -> pd.DataFrame:
    if df.empty or "report_period" not in df.columns:
        return pd.DataFrame()
    col = df["report_period"].astype(str)
    if kind == "annual":
        mask = col.str.match(r"^\d{4}$")
    else:
        mask = col.str.contains("-Q")
    return df[mask].iloc[::-1].head(n).reset_index(drop=True)


# ── main fetcher ────────────────────────────────────────────────────────────

def fetch_vn_financial_context(
    symbol: str,
    source: str = "VCI",
    n_years: int = 5,
    n_quarters: int = 10,
    trade_date: str | None = None,
) -> dict:
    """
    Fetch financial data and return:
      summary_md   : formatted markdown tables for LLM injection
      chart_json   : JSON string to embed in report for render_report.py
      is_bank      : bool
      error        : str or None
    """
    empty = {"summary_md": "", "chart_json": "", "is_bank": False, "error": None}

    try:
        from vnstock_data import Finance, Quote
    except ImportError:
        empty["error"] = "vnstock_data not available in this environment"
        return empty

    try:
        f = Finance(symbol=symbol, source=source)
        today = trade_date or datetime.today().strftime("%Y-%m-%d")
        _today_dt = datetime.strptime(today, "%Y-%m-%d")
        one_yr_ago = f"{_today_dt.year - 1}-01-01"

        ratio_raw = _safe(f.ratio)
        inc_raw   = _safe(f.income_statement)
        bs_raw    = _safe(f.balance_sheet)
        cf_raw    = _safe(f.cash_flow)

        # Latest price
        latest_price = None
        try:
            q  = Quote(symbol=symbol, source=source)
            px = _safe(q.history, start=one_yr_ago, end=today, interval="1D")
            if not px.empty:
                cc = next((c for c in px.columns if "close" in c.lower()), None)
                if cc:
                    latest_price = float(px[cc].dropna().iloc[-1])
        except Exception:
            pass

        # Slice to annual / quarterly
        ra = _filter(ratio_raw, "annual",  n_years)     # annual ratios
        rq = _filter(ratio_raw, "quarter", n_quarters)  # quarterly ratios
        ia = _filter(inc_raw,   "annual",  n_years)     # annual income
        iq = _filter(inc_raw,   "quarter", n_quarters)  # quarterly income
        ba = _filter(bs_raw,    "annual",  n_years)     # annual balance sheet
        ca = _filter(cf_raw,    "annual",  n_years)     # annual cash flow

        is_bank = _is_bank(rq) if not rq.empty else False
        sec_class = sector_class(is_bank, ia, iq)

        # ── build markdown ──────────────────────────────────────────────────
        md = []
        md.append("---")
        md.append(f"## 📊 DỮ LIỆU TÀI CHÍNH PRE-LOADED: {symbol}")
        if latest_price:
            md.append(f"**Giá hiện tại**: {latest_price:,.1f} nghìn đ/CP")
        md.append("")

        # --- Annual summary ---
        if not ra.empty:
            md.append("### Tóm tắt tài chính năm (nguồn: vnstock_data)")
            if is_bank:
                md.append("| Năm | P/E | P/B | ROE | ROA | NIM | NPL | BVPS (VND) |")
                md.append("|-----|-----|-----|-----|-----|-----|-----|------------|")
                for _, r in ra.iterrows():
                    period = str(r.get("report_period", ""))
                    md.append(
                        f"| {period}"
                        f" | {_num(_get(r, 'P/E'))}"
                        f" | {_num(_get(r, 'P/B'), 2)}"
                        f" | {_pct(_get(r, 'ROE (%)', 'ROE(%)'))} "
                        f" | {_pct(_get(r, 'ROA (%)', 'ROA(%)'))} "
                        f" | {_pct(_get(r, 'Net Interest Margin'))}"
                        f" | {_pct(_get(r, 'NPL (%)', 'NPL(%)'))} "
                        f" | {_num(_get(r, 'Book Value/Share (VND)'), 0)} |"
                    )
            else:
                md.append("| Năm | DT (tỷ) | LNST (tỷ) | EPS (VND) | Biên LN | ROE | P/E | P/B |")
                md.append("|-----|---------|-----------|-----------|---------|-----|-----|-----|")
                for _, r in ra.iterrows():
                    period = str(r.get("report_period", ""))
                    # Match income row for same year
                    ir = None
                    if not ia.empty and "report_period" in ia.columns:
                        m = ia[ia["report_period"].astype(str) == period]
                        if not m.empty:
                            ir = m.iloc[0]
                    rev_raw  = _get(ir, "Net sales", "Revenue") if ir is not None else None
                    lnst_raw = _get(ir, "Attributable to parent company", "Net profit") if ir is not None else None
                    rev  = _bn(rev_raw)  if rev_raw  is not None else "—"
                    lnst = _bn(lnst_raw) if lnst_raw is not None else "—"
                    eps  = _num(_get(ir, "EPS basic (VND)", "EPS"), 0) if ir is not None else "—"
                    # Biên LN tính trực tiếp = LNST / DT (A2: internal-consistent, không
                    # dùng field margin của vnstock vì nó dùng định nghĩa lợi nhuận khác).
                    margin = (f"{float(lnst_raw) / float(rev_raw) * 100:.1f}%"
                              if (rev_raw and lnst_raw and float(rev_raw) != 0) else "—")
                    roe    = _pct(_get(r, "ROE (%)", "ROE(%)"))
                    pe     = _num(_get(r, "P/E"))
                    pb     = _num(_get(r, "P/B"), 2)
                    md.append(f"| {period} | {rev} | {lnst} | {eps} | {margin} | {roe} | {pe} | {pb} |")
            md.append("")

        # --- Current valuation snapshot ---
        if not rq.empty:
            md.append("### Định giá hiện tại (quý gần nhất)")
            first = rq.iloc[0]
            VALUATION_KEYS = [
                ("P/E",                         "P/E",          "x"),
                ("P/B",                         "P/B",          "x"),
                ("P/S",                         "P/S",          "x"),
                ("EV/EBITDA",                   "EV/EBITDA",    "x"),
                ("Dividend Yield (%)",           "Div Yield",    "%"),
                ("ROE (%)",                      "ROE",          "%"),
                ("ROA (%)",                      "ROA",          "%"),
                ("After-tax Profit Margin (%)",  "Net Margin",   "%"),
                ("Gross Margin (%)",             "Gross Margin", "%"),
                ("Net Interest Margin",          "NIM",          "%"),
                ("NPL (%)",                      "NPL",          "%"),
                ("CASA Ratio",                   "CASA",         "%"),
                # CAR CỐ TÌNH bỏ khỏi đây: vnstock trả 0.0 (thiếu thật) → nguồn CAR
                # lấy từ bank_metrics.yaml, inject riêng ở build_financials_payload.
                ("LDR (%)",                      "LDR",          "%"),
                ("Net Debt (Bn)",                "Net Debt",     "tỷ"),
                ("Free Cash Flow (Bn)",          "FCF",          "tỷ"),
                ("FCF Yield (%)",                "FCF Yield",    "%"),
            ]
            # Với các bank-ratio này, vnstock zero-fill khi thiếu → 0.0 nghĩa là
            # KHÔNG CÓ DỮ LIỆU, không phải giá trị thật (0% NIM/NPL/CASA/LDR bất khả).
            # Bỏ qua để không truyền 0.0 vô nghĩa xuống agent.
            _ZERO_MISSING = {"Net Interest Margin", "NPL (%)", "CASA Ratio", "LDR (%)"}
            row_items = []
            for key, label, fmt in VALUATION_KEYS:
                v = _get(first, key)
                if v is None:
                    continue
                if key in _ZERO_MISSING and _f_num(v) == 0.0:
                    continue
                if fmt == "%":
                    row_items.append(f"**{label}**: {_pct(v)}")
                elif fmt == "x":
                    row_items.append(f"**{label}**: {_num(v, 2)}")
                else:
                    row_items.append(f"**{label}**: {_num(v, 0)} {fmt}")
            if row_items:
                # Print as 2-column grid via markdown
                half = (len(row_items) + 1) // 2
                for i in range(half):
                    left  = row_items[i]
                    right = row_items[i + half] if i + half < len(row_items) else ""
                    md.append(f"- {left}　　{right}")
            md.append("")

        # --- Quarterly P&L trend ---
        if not iq.empty:
            md.append("### Xu hướng doanh thu / LNST theo quý (8Q gần nhất)")
            md.append("| Quý | DT (tỷ) | LNST (tỷ) | EPS (VND) |")
            md.append("|-----|---------|-----------|-----------|")
            for _, r in iq.iterrows():
                period = str(r.get("report_period", ""))
                rev  = _bn(_get(r, "Net sales", "Revenue"))
                lnst = _bn(_get(r, "Attributable to parent company", "Net profit"))
                eps  = _num(_get(r, "EPS basic (VND)", "EPS"), 0)
                md.append(f"| {period} | {rev} | {lnst} | {eps} |")
            md.append("")

        # DuPont annual decomposition đã CHUYỂN sang fundamentals_tables.py
        # (deterministic, có bank ROA×leverage) — inject ở build_financials_payload,
        # tránh bảng DuPont trùng lặp (một ở đây, một ở module mới).

        # --- Free Cash Flow (annual) — ĐỊNH NGHĨA DUY NHẤT: FCF = CFO − CapEx (A3) ---
        # Bank không dùng FCF kiểu này → bỏ qua.
        if not is_bank and not ca.empty:
            md.append("### Free Cash Flow (FCF = CFO − CapEx, tỷ đồng)")
            md.append("| Năm | CFO | CapEx | FCF |")
            md.append("|-----|-----|-------|-----|")
            for _, r in ca.iterrows():
                period = str(r.get("report_period", ""))
                cfo = _get(r, "Net cash inflows/(outflows) from operating activities")
                capex = _get(r, "Purchases of fixed assets and other long term assets")
                cfo_f = float(cfo) if cfo is not None else None
                capex_f = abs(float(capex)) if capex is not None else None
                fcf = (cfo_f - capex_f) if (cfo_f is not None and capex_f is not None) else None
                md.append(
                    f"| {period} | {_bn(cfo_f)} | {_bn(capex_f) if capex_f is not None else '—'}"
                    f" | {_bn(fcf) if fcf is not None else '—'} |"
                )
            md.append("")

        md.append("---")
        summary_md = "\n".join(md)

        # ── build chart_data for SVG rendering in render_report.py ──────────
        chart_data: dict = {
            "symbol":        symbol,
            "is_bank":       is_bank,
            "sector_class":  sec_class,
            "latest_price":  latest_price,
            "years":         [],
            "revenue_bn":    [],   # thu nhập theo ngành: TOI (bank) / DT HĐ (securities) / doanh thu (generic)
            "netprofit_bn":  [],
            "efficiency_pct": [],  # CIR (bank, thấp=tốt) hoặc biên LN ròng (securities/generic, cao=tốt)
            "pe":            [],
            "pb":            [],
            "roe_pct":       [],
            "roa_pct":       [],
            "nim_pct":       [],
            "npl_pct":       [],
            "quarters":      [],
            "q_revenue_bn":  [],
            "q_income_bn":   [],   # = q_revenue_bn, tên theo spec (giữ cả 2 key cho tương thích)
            "q_profit_bn":   [],
            "q_efficiency_pct": [],
        }

        if not ra.empty and "report_period" in ra.columns:
            years = list(ra["report_period"].astype(str))[::-1]  # oldest first
            ra_idx = ra.set_index("report_period")
            ia_idx = ia.set_index("report_period") if (not ia.empty and "report_period" in ia.columns) else pd.DataFrame()

            chart_data["years"] = years
            for y in years:
                r = ra_idx.loc[y] if y in ra_idx.index else pd.Series(dtype=object)
                i = ia_idx.loc[y] if (not ia_idx.empty and y in ia_idx.index) else pd.Series(dtype=object)

                def fv(row, *keys):
                    v = _get(row, *keys)
                    return float(v) if v is not None else None

                rev_raw = _income_field_vnd(i, sec_class)
                rev = float(rev_raw) if rev_raw is not None else None
                chart_data["revenue_bn"].append(rev / 1e9 if rev else None)
                np_raw = _get(i, "Attributable to parent company", "Net profit")
                np_ = float(np_raw) if np_raw is not None else None
                chart_data["netprofit_bn"].append(np_ / 1e9 if np_ else None)
                eff = _efficiency_pct(i, rev_raw, np_raw, sec_class)
                chart_data["efficiency_pct"].append(eff)
                chart_data["pe"].append(fv(r, "P/E"))
                chart_data["pb"].append(fv(r, "P/B"))
                roe = fv(r, "ROE (%)", "ROE(%)")
                chart_data["roe_pct"].append(roe * 100 if roe is not None else None)
                roa = fv(r, "ROA (%)", "ROA(%)")
                chart_data["roa_pct"].append(roa * 100 if roa is not None else None)
                nim = fv(r, "Net Interest Margin")
                chart_data["nim_pct"].append(nim * 100 if nim is not None else None)
                npl = fv(r, "NPL (%)", "NPL(%)")
                chart_data["npl_pct"].append(npl * 100 if npl is not None else None)

        if not iq.empty and "report_period" in iq.columns:
            iq_sorted = iq.iloc[::-1].reset_index(drop=True)
            chart_data["quarters"] = list(iq_sorted["report_period"].astype(str))
            for _, r in iq_sorted.iterrows():
                rev_raw = _income_field_vnd(r, sec_class)
                np_raw = _get(r, "Attributable to parent company", "Net profit")
                rev_bn = float(rev_raw) / 1e9 if rev_raw is not None else None
                chart_data["q_revenue_bn"].append(rev_bn)
                chart_data["q_income_bn"].append(rev_bn)
                chart_data["q_profit_bn"].append(float(np_raw) / 1e9 if np_raw is not None else None)
                chart_data["q_efficiency_pct"].append(_efficiency_pct(r, rev_raw, np_raw, sec_class))

        return {
            "summary_md": summary_md,
            "chart_json":  json.dumps(chart_data),
            "is_bank":     is_bank,
            "error":       None,
            # Raw sliced frames cho valuation_engine (tránh fetch lại ratio/income/cf).
            "frames": {
                "ra": ra, "rq": rq, "ia": ia, "iq": iq, "ba": ba, "ca": ca,
                "latest_price": latest_price, "is_bank": is_bank,
            },
        }

    except Exception as e:
        return {"summary_md": "", "chart_json": "", "is_bank": False, "error": str(e), "frames": {}}


# ── Single source of truth (A1/A2/A3) ──────────────────────────────────────

_FINANCIALS_HEADER = """# 📒 SỐ LIỆU TÀI CHÍNH CHÍNH THỐNG (SINGLE SOURCE OF TRUTH)

⚠️ ĐÂY LÀ NGUỒN SỐ DUY NHẤT cho {symbol}. Tất cả số liệu tài chính, tỷ số, FCF
trong phần phân tích của bạn PHẢI trích từ bảng dưới đây — KHÔNG được tự tính lại,
KHÔNG ước lượng, KHÔNG dùng số từ trí nhớ. Nếu một chỉ số không có ở đây, hãy nói
"không có dữ liệu" thay vì bịa. Mọi tỷ số (margin, ROE, ROA, ROIC, P/E, P/B, D/E,
FCF) đã được tính sẵn bằng máy — chỉ diễn giải, không tính lại.
"""


def _load_bank_metrics(symbol: str) -> dict:
    """Đọc bank_metrics.yaml → dict cho ticker (car/as_of/source). {} nếu thiếu.

    car được coi là CÓ chỉ khi là số > 0; null / 0 / rỗng → thiếu (không truyền 0.0).
    """
    try:
        from tradingagents.dataflows.config import get_config
        import yaml
        path = get_config().get("bank_metrics_path")
        if not path or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        entry = data.get(symbol.upper()) or {}
        car = _f_num(entry.get("car"))
        return {
            "car": car if (car is not None and car > 0) else None,
            "as_of": entry.get("as_of") or None,
            "source": entry.get("source") or None,
        }
    except Exception:
        return {}


def _bank_extra_block(symbol: str) -> str:
    """Markdown block chỉ số vốn bank từ nguồn ngoài vnstock (CAR)."""
    m = _load_bank_metrics(symbol)
    car = m.get("car")
    if car is not None:
        asof = f" (tại {m['as_of']})" if m.get("as_of") else ""
        src = f" — nguồn: {m['source']}" if m.get("source") else ""
        car_str = f"**{car:.1f}%**{asof}{src}"
    else:
        car_str = "— (chưa cập nhật trong bank_metrics.yaml; KHÔNG suy luận trên 0%)"
    return (
        "\n### Chỉ số vốn (nguồn ngoài vnstock — cập nhật thủ công)\n"
        f"- **CAR (Capital Adequacy Ratio)**: {car_str}\n"
    )


def build_financials_payload(symbol: str, source: str = "VCI", trade_date: str | None = None,
                             beta: float | None = None) -> dict:
    """Canonical financial payload — MỘT nguồn số duy nhất cho mọi agent (A1).

    Tất cả số/tỷ số được tính sẵn ở Python layer (A2); FCF = CFO − CapEx (A3).

    ``beta`` (Task 10 R1, tính 1 lần từ price history ở trading_graph.py) được
    truyền xuống valuation_engine (V1) để COE dùng beta thật thay vì
    default_beta=1.0 — None vẫn hoạt động (fallback), giữ hàm gọi được độc lập.

    Trả về:
        block       : markdown "nguồn chân lý" tiêm vào mọi agent (kèm chỉ thị cite-only)
        chart_json  : JSON cho render_report.py
        data        : dict chart_data (cho validator/render)
        is_bank     : bool
        error       : str | None
    """
    ctx = fetch_vn_financial_context(symbol, source=source, trade_date=trade_date)
    if ctx.get("error") or not ctx.get("summary_md"):
        return {"block": "", "chart_json": ctx.get("chart_json", ""), "data": {},
                "is_bank": ctx.get("is_bank", False), "error": ctx.get("error") or "no data"}

    block = _FINANCIALS_HEADER.format(symbol=symbol) + "\n" + ctx["summary_md"]
    try:
        data = json.loads(ctx["chart_json"]) if ctx.get("chart_json") else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    # Bảng trend + DuPont deterministic (Python tính, analyst chỉ nhận xét).
    # Best-effort: lỗi ở đây không phá payload.
    try:
        from tradingagents.agents.utils.fundamentals_tables import build_fundamentals_tables
        _ft = build_fundamentals_tables(ctx.get("frames", {}), ctx.get("is_bank", False))
        if _ft:
            block += "\n" + _ft
    except Exception:
        pass

    # CAR & metric bank bổ sung ngoài vnstock (bank_metrics.yaml). CHỈ cho bank.
    # Thiếu số → "—", TUYỆT ĐỐI không 0.0.
    if ctx.get("is_bank"):
        block += "\n" + _bank_extra_block(symbol)

    # ── Task 8: định giá deterministic — tiêm thẳng vào block (single source) ──
    # valuation_engine tính từ số, agent chỉ diễn giải. Best-effort: lỗi ở đây
    # không được phá payload (agent vẫn có bảng tài chính gốc để cite).
    valuation = {}
    try:
        from tradingagents.agents.utils.valuation_engine import build_valuation_block
        val = build_valuation_block(symbol, ctx.get("frames", {}),
                                    source=source, trade_date=trade_date, beta=beta)
        if not val.get("error") and val.get("valuation_md"):
            block += "\n" + val["valuation_md"]
            valuation = val.get("data", {})
    except Exception:
        pass

    return {
        "block": block,
        "chart_json": ctx["chart_json"],
        "data": data,
        "valuation": valuation,
        "is_bank": ctx.get("is_bank", False),
        "error": None,
    }


# ── SVG chart generators (called by render_report.py) ──────────────────────

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _fmt_label(v: float, big: bool) -> str:
    """Compact value label shown above a bar."""
    if v is None:
        return ""
    if big:  # tỷ đồng — thousands grouping
        if abs(v) >= 10000:
            return f"{v/1000:,.1f}K"
        return f"{v:,.0f}"
    # ratios / percentages
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{v:.1f}"


def _rounded_top_bar(x: float, y: float, w: float, h: float, r: float) -> str:
    """SVG path for a bar with only the top two corners rounded."""
    r = max(0.0, min(r, w / 2, h))
    return (
        f"M{x:.1f},{y+h:.1f} "
        f"L{x:.1f},{y+r:.1f} "
        f"Q{x:.1f},{y:.1f} {x+r:.1f},{y:.1f} "
        f"L{x+w-r:.1f},{y:.1f} "
        f"Q{x+w:.1f},{y:.1f} {x+w:.1f},{y+r:.1f} "
        f"L{x+w:.1f},{y+h:.1f} Z"
    )


def _svg_bar_chart(
    labels: list,
    series: list[tuple],   # list of (values, color, legend_label)
    title: str = "",
    unit: str = "",
    width: int = 760,
    height: int = 300,
    big_numbers: bool = False,
    value_labels: bool = True,
) -> str:
    """Generate a polished grouped SVG bar chart with gradient fills,
    rounded tops and value labels. series = [(values, color, label), ...]"""
    PAD = {"l": 56, "r": 18, "t": 46, "b": 58}
    w = width  - PAD["l"] - PAD["r"]
    h = height - PAD["t"] - PAD["b"]

    n_groups = len(labels)
    n_series = len(series)
    all_vals  = [v for s, _, _ in series for v in s if v is not None]
    if not all_vals:
        return ""

    vmax = max(all_vals) * 1.18
    vmin = min(0, min(all_vals)) * 1.12
    if vmax == vmin:
        vmax = vmin + 1

    def ys(v):
        if v is None:
            return None
        ratio = (v - vmin) / (vmax - vmin)
        return PAD["t"] + h * (1 - ratio)

    group_w  = w / max(n_groups, 1)
    inner    = group_w * 0.62
    bar_w    = inner / max(n_series, 1)
    bar_gap  = (group_w - inner) / 2

    # Unique gradient ids per color so multiple charts on one page don't clash
    grad_ids = {}
    defs = ['<defs>']
    for _, color, _ in series:
        if color not in grad_ids:
            gid = f"g{abs(hash((color, title))) % 100000}_{color.lstrip('#')}"
            grad_ids[color] = gid
            defs.append(
                f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0%" stop-color="{color}" stop-opacity="1"/>'
                f'<stop offset="100%" stop-color="{color}" stop-opacity="0.55"/>'
                f'</linearGradient>'
            )
    defs.append('</defs>')

    svg = []
    svg.append(
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="background:linear-gradient(180deg,#0f1729,#0c1322);border:1px solid #1c2740;'
        f'border-radius:14px;display:block;width:100%;height:auto" '
        f'font-family="Inter,system-ui,sans-serif" preserveAspectRatio="xMidYMid meet">'
    )
    svg.extend(defs)

    # Title
    if title:
        svg.append(
            f'<text x="{PAD["l"]}" y="26" text-anchor="start" '
            f'fill="#e2e8f0" font-size="14" font-weight="700" letter-spacing="-0.01em">'
            f'{title}</text>'
        )

    # Soft dashed gridlines + Y labels
    for pct in range(0, 101, 25):
        v = vmin + (vmax - vmin) * pct / 100
        y = ys(v)
        svg.append(
            f'<line x1="{PAD["l"]}" y1="{y:.1f}" x2="{PAD["l"] + w}" y2="{y:.1f}" '
            f'stroke="#1e293b" stroke-width="1" stroke-dasharray="3 4"/>'
        )
        lbl = _fmt_label(v, big_numbers)
        svg.append(
            f'<text x="{PAD["l"] - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'fill="#5b6b85" font-size="10.5">{lbl}</text>'
        )

    # Zero baseline (solid, if range crosses 0)
    if vmin < 0 < vmax:
        zy = ys(0)
        svg.append(
            f'<line x1="{PAD["l"]}" y1="{zy:.1f}" x2="{PAD["l"] + w}" y2="{zy:.1f}" '
            f'stroke="#33455f" stroke-width="1.5"/>'
        )

    # Bars + value labels
    for gi, label in enumerate(labels):
        gx = PAD["l"] + gi * group_w + bar_gap
        for si, (values, color, _) in enumerate(series):
            if gi >= len(values) or values[gi] is None:
                continue
            v = values[gi]
            bx = gx + si * bar_w
            y_top  = ys(v)
            zero_y = ys(0) if vmin < 0 else ys(vmin)
            bh = abs(zero_y - y_top)
            rect_y = min(y_top, zero_y)
            if bh < 1.5:
                bh = 1.5
            gw = bar_w * 0.82
            gxx = bx + (bar_w - gw) / 2
            svg.append(
                f'<path d="{_rounded_top_bar(gxx, rect_y, gw, bh, 4)}" '
                f'fill="url(#{grad_ids[color]})"/>'
            )
            # value label above bar
            if value_labels and v is not None:
                svg.append(
                    f'<text x="{gxx + gw/2:.1f}" y="{rect_y - 6:.1f}" text-anchor="middle" '
                    f'fill="#cbd5e1" font-size="10" font-weight="600">{_fmt_label(v, big_numbers)}</text>'
                )

        # X-axis label
        lx = gx + inner / 2
        svg.append(
            f'<text x="{lx:.1f}" y="{PAD["t"] + h + 18}" text-anchor="middle" '
            f'fill="#8295b0" font-size="11" font-weight="500">{label}</text>'
        )

    # Unit (top-left under title)
    if unit:
        svg.append(
            f'<text x="{PAD["l"]}" y="40" text-anchor="start" '
            f'fill="#5b6b85" font-size="9.5">đơn vị: {unit}</text>'
        )

    # Legend (top-right)
    lx = width - PAD["r"]
    ly = 24
    for _, color, lbl in reversed(series):
        tw = len(lbl) * 6.2 + 18
        lx -= tw
        svg.append(f'<rect x="{lx:.1f}" y="{ly - 9}" width="11" height="11" fill="{color}" rx="3"/>')
        svg.append(
            f'<text x="{lx + 16:.1f}" y="{ly:.1f}" fill="#94a3b8" font-size="10.5">{lbl}</text>'
        )
        lx -= 12

    svg.append("</svg>")
    return "\n".join(svg)


# ── HTML section assembler (called by render_report.py) ────────────────────

def render_vn_charts_html(chart_data: dict) -> str:
    """
    Generate a complete HTML block of polished SVG bar charts from chart_data.
    All charts are 5-year (or 8-quarter) grouped bar charts for visual consistency.
    Returned string is prepended to the fundamentals card body.
    """
    if not chart_data:
        return ""

    years    = chart_data.get("years", [])
    is_bank  = chart_data.get("is_bank", False)

    charts = []  # collected SVG strings, laid out 2-per-row

    # Chart 1 — Revenue + Net Profit (bar, tỷ đồng)
    rev_series = chart_data.get("revenue_bn", [])
    np_series  = chart_data.get("netprofit_bn", [])
    if years and any(v is not None for v in rev_series):
        rev_label = "Tổng thu nhập" if is_bank else "Doanh thu"
        c = _svg_bar_chart(
            years,
            [(rev_series, "#3b82f6", rev_label),
             (np_series,  "#22c55e", "LNST")],
            title=f"Doanh thu & LNST · 5 năm",
            unit="tỷ đồng",
            big_numbers=True,
        )
        if c:
            charts.append(c)

    # Chart 2 — NIM + NPL (banks) OR P/E + P/B (non-bank), as bars
    pe_series  = chart_data.get("pe", [])
    pb_series  = chart_data.get("pb", [])
    nim_series = chart_data.get("nim_pct", [])
    npl_series = chart_data.get("npl_pct", [])
    if is_bank and years and any(v is not None for v in nim_series):
        c = _svg_bar_chart(
            years,
            [(nim_series, "#f59e0b", "NIM (%)"),
             (npl_series, "#f43f5e", "NPL (%)")],
            title="NIM & NPL · 5 năm",
            unit="%",
        )
        if c:
            charts.append(c)
    elif years and any(v is not None for v in pe_series):
        c = _svg_bar_chart(
            years,
            [(pe_series, "#f59e0b", "P/E"),
             (pb_series, "#a855f7", "P/B")],
            title="Định giá P/E & P/B · 5 năm",
            unit="lần",
        )
        if c:
            charts.append(c)

    # Chart 3 — ROE + ROA (bar, %)
    roe_series = chart_data.get("roe_pct", [])
    roa_series = chart_data.get("roa_pct", [])
    if years and any(v is not None for v in roe_series):
        c = _svg_bar_chart(
            years,
            [(roe_series, "#10b981", "ROE (%)"),
             (roa_series, "#38bdf8", "ROA (%)")],
            title="Hiệu quả sinh lời ROE & ROA · 5 năm",
            unit="%",
        )
        if c:
            charts.append(c)

    # Chart 4 — Quarterly revenue + profit (bar, 8Q)
    quarters = chart_data.get("quarters", [])
    q_rev    = chart_data.get("q_revenue_bn", [])
    q_profit = chart_data.get("q_profit_bn", [])
    if quarters and any(v is not None for v in q_rev):
        c = _svg_bar_chart(
            quarters,
            [(q_rev,    "#3b82f6", "Doanh thu"),
             (q_profit, "#22c55e", "LNST")],
            title="Xu hướng kết quả theo quý · 8 quý",
            unit="tỷ đồng",
            big_numbers=True,
        )
        if c:
            charts.append(c)

    if not charts:
        return ""

    # Lay out as a responsive 2-column grid; charts auto-flow
    cells = "".join(f'<div style="min-width:0">{c}</div>' for c in charts)
    return (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));'
        'gap:18px;margin-bottom:28px;padding-bottom:28px;border-bottom:1px solid #1e293b">'
        f'{cells}</div>'
    )
