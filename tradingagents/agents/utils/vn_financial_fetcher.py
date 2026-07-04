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
                ("CAR",                          "CAR",          "%"),
                ("LDR (%)",                      "LDR",          "%"),
                ("Net Debt (Bn)",                "Net Debt",     "tỷ"),
                ("Free Cash Flow (Bn)",          "FCF",          "tỷ"),
                ("FCF Yield (%)",                "FCF Yield",    "%"),
            ]
            row_items = []
            for key, label, fmt in VALUATION_KEYS:
                v = _get(first, key)
                if v is None:
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

        # --- DuPont decomposition (annual) ---
        if not ra.empty:
            md.append("### DuPont: ROE decomposition")
            md.append("| Năm | ROE | Net Margin | Asset Turnover | Financial Leverage |")
            md.append("|-----|-----|------------|----------------|-------------------|")
            for _, r in ra.iterrows():
                period = str(r.get("report_period", ""))
                roe    = _pct(_get(r, "ROE (%)", "ROE(%)"))
                # Net Margin = LNST / DT (khớp với bảng trên — A2)
                ir2 = None
                if not ia.empty and "report_period" in ia.columns:
                    m2 = ia[ia["report_period"].astype(str) == period]
                    if not m2.empty:
                        ir2 = m2.iloc[0]
                rev2  = _get(ir2, "Net sales", "Revenue") if ir2 is not None else None
                lnst2 = _get(ir2, "Attributable to parent company", "Net profit") if ir2 is not None else None
                margin = (f"{float(lnst2) / float(rev2) * 100:.1f}%"
                          if (rev2 and lnst2 and float(rev2) != 0) else "—")
                at     = _num(_get(r, "Asset Turnover"), 2)
                lev    = _num(_get(r, "Financial Leverage", "Debt/Equity"), 2)
                md.append(f"| {period} | {roe} | {margin} | {at} | {lev} |")
            md.append("")

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
            "latest_price":  latest_price,
            "years":         [],
            "revenue_bn":    [],
            "netprofit_bn":  [],
            "pe":            [],
            "pb":            [],
            "roe_pct":       [],
            "roa_pct":       [],
            "nim_pct":       [],
            "npl_pct":       [],
            "quarters":      [],
            "q_revenue_bn":  [],
            "q_profit_bn":   [],
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

                rev = fv(i, "Net sales", "Revenue")
                chart_data["revenue_bn"].append(rev / 1e9 if rev else None)
                np_ = fv(i, "Attributable to parent company", "Net profit")
                chart_data["netprofit_bn"].append(np_ / 1e9 if np_ else None)
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
                rev = _get(r, "Net sales", "Revenue")
                np_ = _get(r, "Attributable to parent company", "Net profit")
                chart_data["q_revenue_bn"].append(float(rev) / 1e9 if rev else None)
                chart_data["q_profit_bn"].append(float(np_) / 1e9 if np_ else None)

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
