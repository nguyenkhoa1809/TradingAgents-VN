"""
vn_gen6.py — lớp dữ liệu VN của TA-VN trên vnstock THẾ HỆ 6 (qua NLNK_Marketdata/quant-signals).

Vì sao có file này (07/11/2026):
  - vnstock thế hệ 6 bỏ các lớp cũ Quote/Finance/Company/Listing/Trading; không có nguồn VCI/KBS;
    không có Finance.ratio(), financial_health(), Company.news().
  - Từ ~24/08/2026 mọi thế hệ cũ trả BCTC/ratio dạng DÀI (period, id, name, value) — code TA-VN
    viết cho dạng RỘNG nên khối FA đã âm thầm rỗng (error=None) từ đó.

File này dựng lại ĐÚNG định dạng mà code TA-VN đang đọc, từ một nguồn duy nhất:
  BCTC   ← NLNK_Marketdata.fin.statements (kho DuckDB trước, FHSC sau)
  giá    ← NLNK_Marketdata.sources.ohlcv (SSI → FHSC)
  tỷ số  ← vnstock_quant.fa.ratios (P/E, P/B, ROE, NIM, EV/EBITDA, FCF...) tính tại máy
Gồm 2 kiểu khung:
  "classic"  cột tiếng Anh như vnstock_data cũ ("Net sales", "P/E", "ROE (%)"...), cột
             report_period ("2025" | "2025-Q3"), CŨ → MỚI, năm + quý chung 1 khung
             (vn_financial_fetcher / valuation_engine / fundamentals_tables).
  "unified"  cột snake_case theo id FHSC (net_revenue, owners_equity...), cột period, MỚI → CŨ
             (các hàm công cụ trong vnstock_data_adapter).
Các lớp Finance / Quote / Company / Listing / Trading bắt chước API cũ để call site đổi 1 dòng import.

Quy ước số liệu:
  - Giá: nghìn đồng (như cũ). Giá trị BCTC: đồng. Tỷ số %: dạng thập phân (0.21 = 21%) như cũ.
  - Vốn hoá lịch sử = giá thô × số CP thực tế (archive gen 5, tới 06/11/2026), sau đó giá SSI ×
    số CP hiện tại (NLNK_Marketdata.fin.market_series). Ngày giá: quý theo quy ước 'vn' (khớp VCI cũ),
    năm = giá cuối năm (NLNK_Marketdata.fin.ratio_overrides).
  - "Outstanding Shares (mil)" chứa SỐ CP THÔ (giữ quirk của VCI mà valuation_engine đã xử lý).
"""

from __future__ import annotations

import functools
import os
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_QS = Path(os.environ.get("KIM_QUANT_SIGNALS_DIR")
           or Path(__file__).resolve().parents[3] / "quant-signals")
if str(_QS) not in sys.path:
    sys.path.insert(0, str(_QS))


class VnDataError(RuntimeError):
    """Lỗi dữ liệu VN (thiếu NLNK_Marketdata / vnstock 6 / nguồn lỗi)."""


def _kim():
    try:
        from NLNK_Marketdata import fin, sources  # noqa: WPS433
    except ImportError as exc:
        raise VnDataError(f"Không import được NLNK_Marketdata ({_QS}) — đặt KIM_QUANT_SIGNALS_DIR") from exc
    if sources.backend() != "gen6":
        raise VnDataError("Cần vnstock thế hệ 6 — chạy TA-VN bằng ~/.venv_6x/Scripts/python.exe")
    return fin, sources


# ── Ánh xạ id FHSC → tên cột classic ───────────────────────────────────────────
# (tên classic, [id ưu tiên lần lượt]) — lấy giá trị khác rỗng đầu tiên.
_IS_MAP = [
    ("Net sales", ["IS_NET_REVENUE", "IS_REVENUE"]),
    ("Revenue", ["IS_NET_REVENUE", "IS_REVENUE"]),
    ("Cost of Sales", ["IS_COST_OF_GOODS_SOLD"]),
    ("Gross Profit", ["IS_GROSS_PROFIT"]),
    ("Financial Income", ["IS_FINANCIAL_INCOME"]),
    ("Interest Expenses", ["IS_INTEREST_EXPENSES"]),
    ("Selling Expenses", ["IS_SELLING_EXPENSES"]),
    ("Operating Profit/Loss", ["IS_OPERATING_PROFIT", "IS_OPERATING_RESULT"]),
    ("Net accounting profit/(loss) before tax", ["IS_PROFIT_BEFORE_TAX"]),
    ("Net Accounting Profit/(loss) before tax", ["IS_PROFIT_BEFORE_TAX"]),
    ("Net profit/(loss) after tax", ["IS_NET_PROFIT_AFTER_TAX"]),
    ("Net profit", ["IS_NET_PROFIT_AFTER_TAX"]),
    ("Attributable to parent company", ["IS_PROFIT_AFTER_TAX_FOR_SHAREHOLDERS_OF_PARENT_COMPANY"]),
    ("Minority Interest", ["IS_MINORITY_INTEREST"]),
    ("EPS basic (VND)", ["IS_BASIC_EARNINGS_PER_SHARE"]),
    # ngân hàng
    ("Total Operating Income", ["IS_TOTAL_OPERATING_INCOME"]),
    ("Net Interest Income", ["IS_NET_INTEREST_INCOME"]),
    ("Net Fee and Commission Income", ["IS_NET_FEE_AND_COMMISSION_INCOME"]),
    ("Net Operating Profit Before Allowance for Credit Loss",
     ["IS_OPERATING_PROFIT_BEFORE_PROVISION_FOR_CREDIT_LOSSES"]),
    ("Provision for credit losses", ["IS_PROVISION_FOR_CREDIT_LOSSES"]),
    # chứng khoán — cột này là "chữ ký" nhận diện CTCK trong vn_financial_fetcher
    ("Revenue in Brokerage services", ["IS_REVENUE_FROM_BROKERAGE_SERVICES"]),
]
_GA_BANK = ["IS_OPERATING_EXPENSES"]
_GA_OTHER = ["IS_GENERAL_AND_ADMINISTRATIVE_EXPENSES"]

_BS_MAP = [
    ("TOTAL ASSETS", ["BS_TOTAL_ASSETS"]),
    ("Total Assets", ["BS_TOTAL_ASSETS"]),
    ("LIABILITIES", ["BS_TOTAL_LIABILITIES"]),
    ("OWNER'S EQUITY", ["BS_EQUITY"]),
    ("Owner's Equity", ["BS_EQUITY"]),
    ("Owners Equity", ["BS_EQUITY"]),
    ("Minority Interest", ["BS_MINORITY_INTEREST"]),
    ("Paid-in capital", ["BS_CHARTER_CAPITAL"]),
    ("Cash and cash equivalents", ["BS_CASH_AND_PRECIOUS_METALS"]),
    ("Short-term borrowings", ["BS_SHORT_TERM_BORROWINGS"]),
    ("Long-term borrowings", ["BS_LONG_TERM_BORROWINGS"]),
    ("Inventories, Net", ["BS_INVENTORIES"]),
    ("Loans and advances to customers, net", ["BS_LOANS_TO_CUSTOMERS"]),
    ("Deposits from customers", ["BS_CUSTOMER_DEPOSITS"]),
]

_CF_MAP = [
    ("Net cash inflows/(outflows) from operating activities", ["CF_NET_CASH_FLOWS_FROM_OPERATING_ACTIVITIES"]),
    ("Net Cash Flows from Investing Activities", ["CF_NET_CASH_FLOWS_FROM_INVESTING_ACTIVITIES"]),
    ("Cash flows from financial activities", ["CF_NET_CASH_FLOWS_FROM_FINANCING_ACTIVITIES"]),
    ("Purchases of fixed assets and other long term assets", ["CF_PAYMENTS_FOR_FIXED_ASSETS"]),
    ("Depreciation and Amortisation", ["CF_DEPRECIATION_AND_AMORTISATION"]),
    ("Dividends paid", ["CF_DIVIDENDS_PAID"]),
    ("Cash and cash equivalents at the beginning of period", ["CF_CASH_AND_CASH_EQUIVALENTS_AT_BEGINNING_OF_PERIOD"]),
    ("Cash and cash equivalents at the end of period", ["CF_CASH_AND_CASH_EQUIVALENTS_AT_END_OF_PERIOD"]),
]

# (tên classic, id vnstock_quant, hệ số nhân)
_RATIO_MAP = [
    ("P/E", "RT_VALUE_PE", 1.0),
    ("P/B", "RT_VALUE_PB", 1.0),
    ("P/S", "RT_VALUE_PS", 1.0),
    ("EV/EBITDA", "RT_VALUE_EV_EBITDA", 1.0),
    ("ROE (%)", "RT_PRT_ROE", 1.0),
    ("ROA (%)", "RT_PRT_ROA", 1.0),
    ("After-tax Profit Margin (%)", "RT_PRT_NET_MARGIN", 1.0),
    ("Gross Margin (%)", "RT_PRT_GROSS_MARGIN", 1.0),
    ("Net Interest Margin", "RT_BANK_NIM", 1.0),
    ("NPL (%)", "RT_BANK_NPL", 1.0),
    ("CASA Ratio", "RT_BANK_CASA", 1.0),
    ("LDR (%)", "RT_BANK_LDR", 1.0),
    ("Cost to Income (CIR)", "RT_BANK_CIR", 1.0),
    ("Debt/Equity", "RT_LEV_DE", 1.0),
    ("Loans/Equity", "RT_LEV_LOAN_EQUITY", 1.0),
    ("Current Ratio", "RT_LQD_CR", 1.0),
    ("Interest Coverage", "RT_LEV_INTEREST_COVERAGE", 1.0),
    ("Net Debt (Bn)", "RT_LEV_NET_DEBT", 1e-9),
    ("Free Cash Flow (Bn)", "RT_CF_FCF", 1e-9),
    ("FCF Yield (%)", "RT_CF_FCF_YIELD", 1.0),
    ("Payout Ratio", "RT_VALUE_PAYOUT", 1.0),
    ("Earnings Yield", "RT_VALUE_EARNINGS_YIELD", 1.0),
    ("EPS (VND)", "RT_VALUE_EPS", 1.0),
    ("Book Value/Share (VND)", "RT_VALUE_BVPS", 1.0),
    ("Outstanding Shares (mil)", "RT_VALUE_OUTSTANDING_SHARES", 1.0),
    ("Market Capital (Bn VND)", "RT_VALUE_MARKET_CAP", 1e-9),
    ("Altman Z''", "RT_SCORE_ALTMAN_Z2", 1.0),
    ("Piotroski F-score", "RT_SCORE_PIOTROSKI_F", 1.0),
    ("Beneish M-score", "RT_SCORE_BENEISH_M", 1.0),
]
_HEALTH_COLS = ["Altman Z''", "Piotroski F-score", "Beneish M-score", "Debt/Equity", "Loans/Equity",
                "Current Ratio", "Interest Coverage", "ROE (%)", "ROA (%)", "Net Interest Margin",
                "LDR (%)", "Cost to Income (CIR)"]

_Q_RE = r"^\d{4}-Q[1-4]$"
_Y_RE = r"^\d{4}$"


# ── Bộ dữ liệu theo mã (cache trong process) ───────────────────────────────────

def _wide(frames: dict, period_re: str) -> pd.DataFrame:
    """{report: dài} → 1 khung rộng index=period, cột=id (gộp 3 báo cáo)."""
    parts = []
    for df in frames.values():
        if df is None or len(df) == 0:
            continue
        d = df[["period", "id", "value"]].copy()
        d["period"] = d["period"].astype(str)
        d = d[d["period"].str.match(period_re)]
        d["value"] = pd.to_numeric(d["value"], errors="coerce")
        parts.append(d)
    if not parts:
        return pd.DataFrame()
    allp = pd.concat(parts, ignore_index=True)
    w = allp.pivot_table(index="period", columns="id", values="value", aggfunc="first").sort_index()
    w.columns.name = None
    return w


def _fa_ratios(symbol: str, frames: dict, prices: pd.DataFrame, period: str) -> pd.DataFrame:
    from vnstock_quant import fa
    fin, _ = _kim()
    stm = [f for f in frames.values() if f is not None and len(f)]
    if not stm:
        return pd.DataFrame()
    kw = {"prices": {symbol: prices}} if len(prices) else {}
    ds = fa.Dataset.from_frames({symbol: stm}, **kw)
    ids = [rid for _, rid, _ in _RATIO_MAP]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        long = fa.ratios(ds, ids=ids, period=period, format="long",
                         overrides=fin.ratio_overrides(period))
    if long is None or len(long) == 0:
        return pd.DataFrame()
    w = long.pivot_table(index="period", columns="id", values="value", aggfunc="first")
    w.index = w.index.astype(str)
    w.columns.name = None
    return w.sort_index()


@functools.lru_cache(maxsize=32)
def _bundle(symbol: str) -> dict:
    """Mọi thứ cần cho 1 mã — fetch 1 lần / process."""
    fin, _ = _kim()
    sym = symbol.upper()
    q = fin.statements(sym, "quarter")
    try:
        y = fin.statements(sym, "year")
    except Exception as exc:  # noqa: BLE001  (năm lỗi → vẫn chạy được phần quý)
        warnings.warn(f"vn_gen6: không lấy được BCTC năm {sym}: {exc}")
        y = {}
    shares, sh_src, sh_flag = fin.current_shares(sym, q.get("balance_sheet"))
    prices, _ = fin.market_series(sym)        # giá thô × số CP thực tế (archive) rồi SSI
    wq, wy = _wide(q, _Q_RE), _wide(y, _Y_RE)
    is_bank = "IS_TOTAL_OPERATING_INCOME" in wq.columns and wq["IS_TOTAL_OPERATING_INCOME"].notna().any()
    return {
        "symbol": sym, "q": q, "y": y, "wq": wq, "wy": wy, "is_bank": is_bank,
        "shares": shares, "shares_source": sh_src, "shares_flag": sh_flag, "prices": prices,
        "rq": _fa_ratios(sym, q, prices, "quarter"),
        "ry": _fa_ratios(sym, y, prices, "year") if y else pd.DataFrame(),
    }


def clear_cache():
    _bundle.cache_clear()


# ── Dựng khung classic ─────────────────────────────────────────────────────────

def _pick(w: pd.DataFrame, ids: list) -> pd.Series:
    out = pd.Series(float("nan"), index=w.index)
    for i in ids:
        if i in w.columns:
            out = out.fillna(w[i])
    return out


def _classic_block(w: pd.DataFrame, mapping: list, extra=None) -> pd.DataFrame:
    if w.empty:
        return pd.DataFrame()
    cols = {}
    for name, ids in mapping:
        s = _pick(w, ids)
        if s.notna().any():          # cột rỗng hoàn toàn → bỏ (giữ đúng ngữ nghĩa "có cột = có dữ liệu")
            cols[name] = s
    if extra:
        cols.update({k: v for k, v in extra(w).items() if v is not None and v.notna().any()})
    out = pd.DataFrame(cols, index=w.index)
    out.insert(0, "report_period", out.index.astype(str))
    return out.reset_index(drop=True)


def _income_extra(is_bank: bool, shares_by_period):
    def f(w):
        ga = _pick(w, (_GA_BANK + _GA_OTHER) if is_bank else _GA_OTHER)
        eps = _pick(w, ["IS_BASIC_EARNINGS_PER_SHARE"])
        np_parent = _pick(w, ["IS_PROFIT_AFTER_TAX_FOR_SHAREHOLDERS_OF_PARENT_COMPANY"])
        sh = shares_by_period(w.index)
        eps = eps.fillna(np_parent / sh)          # BCTC NH/CTCK hay bỏ trống EPS → LNST mẹ / số CP kỳ đó
        return {"General and Admin Expenses": ga, "EPS basic (VND)": eps, "EPS": eps}
    return f


def _shares_by_period(b: dict):
    """Số CP theo kỳ = vốn điều lệ cuối kỳ / 10.000 (fallback: số CP hiện tại)."""
    charter = pd.concat([
        b["wq"].get("BS_CHARTER_CAPITAL", pd.Series(dtype=float)),
        b["wy"].get("BS_CHARTER_CAPITAL", pd.Series(dtype=float)),
    ])

    def f(index):
        s = charter.reindex(index) / 10000.0
        return s.where(s > 0).fillna(b["shares"] or float("nan"))
    return f


def _ratio_classic(r: pd.DataFrame) -> pd.DataFrame:
    if r.empty:
        return pd.DataFrame()
    cols = {}
    for name, rid, mult in _RATIO_MAP:
        if rid in r.columns and r[rid].notna().any():
            cols[name] = r[rid] * mult
    out = pd.DataFrame(cols, index=r.index)
    if "Payout Ratio" in out and "Earnings Yield" in out:
        out["Dividend Yield (%)"] = out["Payout Ratio"] * out["Earnings Yield"]
    out.insert(0, "report_period", out.index.astype(str))
    return out.reset_index(drop=True)


def classic_frames(symbol: str) -> dict:
    """{ratio, income_statement, balance_sheet, cash_flow}: khung classic, năm rồi quý, cũ → mới."""
    b = _bundle(symbol.upper())
    shp = _shares_by_period(b)
    out = {}
    for name, w_y, w_q, mapping, extra in [
        ("income_statement", b["wy"], b["wq"], _IS_MAP, _income_extra(b["is_bank"], shp)),
        ("balance_sheet", b["wy"], b["wq"], _BS_MAP, None),
        ("cash_flow", b["wy"], b["wq"], _CF_MAP, None),
    ]:
        out[name] = pd.concat([_classic_block(w_y, mapping, extra), _classic_block(w_q, mapping, extra)],
                              ignore_index=True)
    out["ratio"] = pd.concat([_ratio_classic(b["ry"]), _ratio_classic(b["rq"])], ignore_index=True)
    return {k: v.copy() for k, v in out.items()}


# ── Dựng khung unified (cho hàm công cụ của adapter) ───────────────────────────

def unified_statement(symbol: str, report: str, period: str = "quarter") -> pd.DataFrame:
    """report ∈ income_statement | balance_sheet | cash_flow. Cột snake_case theo id FHSC
    (IS_NET_REVENUE → net_revenue), cột 'period', MỚI → CŨ, giá trị đồng."""
    b = _bundle(symbol.upper())
    frames = b["q"] if period == "quarter" else b["y"]
    df = frames.get(report)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    w = _wide({report: df}, _Q_RE if period == "quarter" else _Y_RE)
    w.columns = [c.split("_", 1)[1].lower() if "_" in c else c.lower() for c in w.columns]
    w = w.loc[:, ~w.columns.duplicated()].sort_index(ascending=False)
    w.insert(0, "period", w.index.astype(str))
    return w.reset_index(drop=True)


def unified_ratio(symbol: str, period: str = "quarter") -> pd.DataFrame:
    """Tỷ số (tên classic), cột 'period', MỚI → CŨ."""
    b = _bundle(symbol.upper())
    r = _ratio_classic(b["rq"] if period == "quarter" else b["ry"])
    if r.empty:
        return r
    r = r.rename(columns={"report_period": "period"}).iloc[::-1].reset_index(drop=True)
    return r


def health_scorecard(symbol: str, curr_date: str | None = None, n: int = 4) -> pd.DataFrame:
    """Thay financial_health() (gen 6 không có): Altman Z'', Piotroski F, Beneish M + đòn bẩy,
    thanh khoản, hiệu quả — n quý gần nhất ĐÃ công bố trước curr_date. Cột = kỳ, dòng = chỉ tiêu."""
    r = unified_ratio(symbol, "quarter")
    if r.empty:
        return r
    if curr_date:
        r = r[r["period"].map(publication_date) <= pd.Timestamp(curr_date)]
    r = r.head(n)
    keep = [c for c in _HEALTH_COLS if c in r.columns]
    if not keep:
        return pd.DataFrame()
    t = r.set_index("period")[keep].T.iloc[:, ::-1]
    t.index.name = "metric"
    return t.reset_index()


def publication_date(period: str) -> pd.Timestamp:
    """Ngày BCTC kỳ này chắc chắn đã công bố (chống nhìn trước tương lai)."""
    p = str(period)
    if len(p) == 4 and p.isdigit():                       # BCTC năm kiểm toán: hạn 90 ngày
        return pd.Timestamp(int(p) + 1, 3, 31)
    y, q = int(p[:4]), int(p[-1])
    ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    lag = {1: 30, 2: 31, 3: 31, 4: 60}
    m, d = ends[q]
    return pd.Timestamp(y, m, d) + pd.Timedelta(days=lag[q])


# ── Ngành / peer ────────────────────────────────────────────────────────────────

_ICB_L1 = {"0000": "Dầu khí", "1000": "Nguyên vật liệu", "2000": "Công nghiệp", "3000": "Hàng tiêu dùng",
           "4000": "Y tế", "5000": "Dịch vụ tiêu dùng", "6000": "Viễn thông", "7000": "Tiện ích cộng đồng",
           "8000": "Tài chính", "9000": "Công nghệ thông tin"}


def symbols_by_industries() -> pd.DataFrame:
    """Tương đương Listing.symbols_by_industries() cũ: 1 dòng / mã / cấp ICB (1..4).
    Nguồn chỉ có mã + tên cấp 4; cấp 1–3 suy từ chữ số (8355 → 8000/8300/8350), tên cấp 1–3
    ghép từ tên các ngành cấp 4 bên dưới."""
    fin, _ = _kim()
    lst = fin.listing_cached()
    lst = lst[lst["icb_code"].notna()][["symbol", "organ_name", "icb_code", "icb_name"]].copy()
    lst["icb_code"] = lst["icb_code"].astype(str).str.zfill(4)
    rows = []
    for lvl, keep in [(1, 1), (2, 2), (3, 3), (4, 4)]:
        d = lst.copy()
        d["icb_code"] = d["icb_code"].str[:keep].str.ljust(4, "0")
        if lvl == 1:
            # tên cấp 1 cố định — KHÔNG ghép tên con (8000 Tài chính chứa cả "Bất động sản" →
            # _route_industry sẽ xếp nhầm ngân hàng/CTCK vào REAL_ESTATE)
            d["icb_name"] = d["icb_code"].map(_ICB_L1).fillna("")
        elif lvl < 4:
            names = (lst.assign(code=lst["icb_code"].str[:keep].str.ljust(4, "0"))
                        .groupby("code")["icb_name"].apply(lambda s: " / ".join(sorted(set(s)))[:120]))
            d["icb_name"] = d["icb_code"].map(names)
        d["icb_level"] = lvl
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def peer_latest_multiples(symbols) -> dict:
    """{symbol: {pe, pb, ev_ebitda}} từ fa_daily mới nhất trong kho (không gọi API).
    Mã ngoài universe của kho → không có mặt (đếm n_peers thực tế ở caller)."""
    fin, _ = _kim()
    d = fin.latest_fa_daily(list(symbols))
    out = {}
    for _, r in d.iterrows():
        out[str(r["symbol"])] = {"pe": _num(r.get("pe")), "pb": _num(r.get("pb")), "ev_ebitda": None,
                                 "date": str(r.get("date"))[:10]}
    return out


def _num(v):
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


# ── Lớp tương thích API cũ ─────────────────────────────────────────────────────

class Finance:
    """Finance(symbol, source).ratio()/income_statement()/balance_sheet()/cash_flow() — khung classic.
    source bị bỏ qua (thế hệ 6: BCTC chỉ có FHSC)."""

    def __init__(self, symbol: str, source: str | None = None, **_):
        self.symbol = symbol.upper()

    def _get(self, name):
        return classic_frames(self.symbol)[name]

    def ratio(self, *_, **__):
        return self._get("ratio")

    def income_statement(self, *_, **__):
        return self._get("income_statement")

    def balance_sheet(self, *_, **__):
        return self._get("balance_sheet")

    def cash_flow(self, *_, **__):
        return self._get("cash_flow")


class Quote:
    """Quote(symbol, source).history(start, end, interval='1D') — time, open, high, low, close, volume
    (nghìn đồng, đã điều chỉnh). Chỉ hỗ trợ 1D; 1W/1M gộp từ 1D."""

    def __init__(self, symbol: str, source: str | None = None, **_):
        self.symbol = symbol.upper()

    def history(self, start: str, end: str | None = None, interval: str = "1D", **_):
        _, sources = _kim()
        df = sources.ohlcv(self.symbol, start=start, end=end or date.today().isoformat())
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        df = df.copy()
        t = pd.to_datetime(df["time"])
        if getattr(t.dt, "tz", None) is not None:
            t = t.dt.tz_localize(None)
        df["time"] = t.dt.normalize()
        iv = str(interval).upper()
        if iv in ("1W", "W", "1M", "M"):
            rule = "W-FRI" if iv.endswith("W") else "ME"
            g = df.set_index("time").resample(rule)
            agg = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            last_day = g["close"].apply(lambda s: s.index.max())
            agg.index = last_day.values
            df = agg.dropna(subset=["close"]).rename_axis("time").reset_index()
        return df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


class Company:
    """overview()/profile()/subsidiaries()/events() từ FHSC. news() đã bị gỡ ở thế hệ 6 → lỗi rõ."""

    def __init__(self, symbol: str, source: str | None = None, **_):
        self.symbol = symbol.upper()

    def _c(self):
        _kim()
        warnings.filterwarnings("ignore", module="vnstock")   # DataWarning về schema — không cần cho agent
        from vnstock import Reference
        return Reference("fhsc").company(self.symbol)

    def overview(self, *_, **__):
        return self._c().overview()

    def profile(self, *_, **__):
        return None          # FHSC: overview đã gồm phần mô tả (cột profile)

    def subsidiaries(self, *_, **__):
        return self._c().subsidiaries()

    def events(self, *_, **__):
        """Khung kiểu cũ: category ('DIVIDEND' cho cổ tức tiền), value_per_share (đồng),
        exright_date, payout_date, event_title."""
        d = self._c().events()
        if d is None or len(d) == 0:
            return pd.DataFrame()
        d = d.copy()
        out = pd.DataFrame({
            "category": d["event_type"].map(lambda x: "DIVIDEND" if x == "cash_dividend" else str(x).upper()),
            "value_per_share": pd.to_numeric(d.get("value"), errors="coerce"),
            "exright_date": d.get("ex_right_date"),
            "payout_date": d.get("payout_date"),
            "event_title": d.get("event_title"),
        })
        return out

    def news(self, *_, **__):
        raise VnDataError("Company.news() không còn ở vnstock thế hệ 6 — dùng RSS/CafeF")


class Listing:
    def __init__(self, source: str | None = None, **_):
        pass

    def all_symbols(self, *_, **__):
        fin, _ = _kim()
        return fin.listing_cached()[["symbol", "organ_name"]].copy()

    def symbols_by_industries(self, *_, **__):
        return symbols_by_industries()


class Trading:
    """foreign_trade(start, end) — cột kiểu cũ: trading_date, fr_buy_value_total,
    fr_sell_value_total, fr_net_value_total (đồng). Không có % sở hữu NN theo ngày."""

    def __init__(self, symbol: str, source: str | None = None, **_):
        self.symbol = symbol.upper()

    def foreign_trade(self, start: str, end: str | None = None, **_):
        _kim()
        from vnstock import Market
        last = None
        for src in ("ssi", "fhsc"):
            try:
                d = Market(src).equity(self.symbol).foreign_flow(start=start, end=end or date.today().isoformat())
                if d is None or len(d) == 0:
                    continue
                t = pd.to_datetime(d["time"])
                if getattr(t.dt, "tz", None) is not None:
                    t = t.dt.tz_localize(None)
                return pd.DataFrame({
                    "trading_date": t.dt.normalize(),
                    "fr_buy_value_total": pd.to_numeric(d["buy_val"], errors="coerce"),
                    "fr_sell_value_total": pd.to_numeric(d["sell_val"], errors="coerce"),
                    "fr_net_value_total": pd.to_numeric(d["net_val"], errors="coerce"),
                    "fr_net_volume_total": pd.to_numeric(d["net_vol"], errors="coerce"),
                }).sort_values("trading_date").reset_index(drop=True)
            except Exception as e:  # noqa: BLE001
                last = e
        if last:
            raise VnDataError(f"foreign_trade {self.symbol}: {last}")
        return pd.DataFrame()


# ── Giao diện kiểu thế hệ 5 (Market/Fundamental) cho vnstock_data_adapter ──────

class _UnifiedEquity:
    def __init__(self, symbol: str):
        self.symbol = symbol.upper()

    def income_statement(self, period: str = "quarter", **_):
        return unified_statement(self.symbol, "income_statement", period)

    def balance_sheet(self, period: str = "quarter", **_):
        return unified_statement(self.symbol, "balance_sheet", period)

    def cash_flow(self, period: str = "quarter", **_):
        return unified_statement(self.symbol, "cash_flow", period)

    def ratio(self, period: str = "quarter", **_):
        return unified_ratio(self.symbol, period)

    def financial_health(self, *_, **__):
        return health_scorecard(self.symbol, n=12)

    def ohlcv(self, start: str | None = None, end: str | None = None, **_):
        start = start or (date.today() - timedelta(days=365)).isoformat()
        return Quote(self.symbol).history(start=start, end=end)


class Fundamental:
    def __init__(self, *_, **__):
        _kim()

    def equity(self, symbol: str):
        return _UnifiedEquity(symbol)


class Market(Fundamental):
    pass
