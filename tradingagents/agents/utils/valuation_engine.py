"""
valuation_engine.py
====================
Định giá deterministic thuần cho cổ phiếu VN (Task 8 / V1–V4).

Nguyên tắc: "Python tính — LLM diễn giải".
  - KHÔNG import agent, KHÔNG gọi LLM.
  - Tính từ số, trả về số + markdown block để tiêm vào financials payload.
  - Verify được bằng cách tắt LLM: gọi thẳng build_valuation_block(...) là ra số.

Các lớp định giá:
  V1  justified P/B (ngân hàng)     = (ROE_fwd − g) / (COE − g)
  V2  DDM (Gordon) + GD-eligibility cho mã trả cổ tức
  V3  sector multiples THỰC (median P/E, P/B, EV/EBITDA cùng ngành ICB)
  V4  reverse-DCF (market-implied growth)

Đơn vị: mọi giá trị/CP quy về **nghìn đồng** (khớp latest_price và quy ước report).
COE / risk-free / ERP để trong config (default_config.py["valuation"]) — cập nhật
định kỳ; hằng số module-level dưới đây chỉ là fallback.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta

import pandas as pd

# Reuse các helper đã kiểm chứng từ fetcher (single source of truth cho slicing).
from tradingagents.agents.utils.vn_financial_fetcher import _get, _filter, _safe


# ── Config fallback (ưu tiên default_config.py["valuation"] nếu có) ──────────
_FALLBACK = {
    "risk_free": 0.030,   # lợi suất TPCP 10Y VN — cập nhật hàng quý
    "erp": 0.085,         # ERP + country risk premium (Damodaran EM) — cập nhật/năm
    "default_beta": 1.0,  # Task 10 R1 sẽ wire beta thật vào đây
    "g_cap": 0.05,        # trần tăng trưởng bền vững perpetuity (gần lạm phát dài hạn)
    "g_coe_buffer": 0.02, # spread tối thiểu COE−g để Gordon ổn định
    "payout_max_gd": 0.80,   # payout tối đa vẫn coi là bền vững cho GD-eligible
    "streak_min_gd": 2,      # số năm tăng cổ tức tối thiểu (giới hạn bởi depth data ~2y)
    "ddm_min_payout": 0.30,  # payout tối thiểu để DDM có ý nghĩa (mã cổ tức thực)
    "sector_level": 3,       # cấp ICB dùng làm peer group
    "max_peers": 25,         # trần số mã peer fetch (bound API + wall-clock)
    "peer_sleep": 0.3,       # nghỉ giữa các call peer (golden tier 500/min)
    "dcf_horizon": 10,       # số năm high-growth cho reverse-DCF
}


def _cfg() -> dict:
    """Đọc config['valuation'] nếu có, merge lên fallback."""
    try:
        from tradingagents.dataflows.config import get_config
        v = get_config().get("valuation") or {}
    except Exception:
        v = {}
    out = dict(_FALLBACK)
    out.update({k: v[k] for k in v if k in _FALLBACK})
    return out


def _cache_dir() -> str:
    try:
        from tradingagents.dataflows.config import get_config
        base = get_config().get("data_cache_dir") or os.path.join(
            os.path.expanduser("~"), ".tradingagents", "cache"
        )
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".tradingagents", "cache")
    d = os.path.join(base, "valuation")
    os.makedirs(d, exist_ok=True)
    return d


def _f(v):
    """Ép về float hoặc None (bỏ NaN)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ── Khối tính lõi — pure functions, dễ unit-test ────────────────────────────

def cost_of_equity(beta, rf, erp):
    """COE (CAPM) = risk_free + beta × ERP."""
    beta = _f(beta)
    if beta is None:
        return None
    return rf + beta * erp


def sustainable_growth(roe, payout, cap):
    """g bền vững = ROE × (1 − payout), floor 0, cap ở `cap`."""
    roe = _f(roe)
    if roe is None:
        return None
    p = _f(payout)
    p = 0.0 if p is None else max(0.0, min(p, 1.0))
    g = roe * (1.0 - p)
    return max(0.0, min(g, cap))


def justified_pb(roe_fwd, coe, g, bvps_k):
    """V1: justified P/B = (ROE_fwd − g)/(COE − g); fair_value = jpb × BVPS."""
    roe_fwd, coe, g, bvps_k = _f(roe_fwd), _f(coe), _f(g), _f(bvps_k)
    if None in (roe_fwd, coe, g):
        return {"error": "thiếu input (ROE_fwd/COE/g)"}
    if coe <= g:
        return {"error": f"COE ({coe:.3f}) ≤ g ({g:.3f}) — công thức Gordon không áp dụng"}
    jpb = (roe_fwd - g) / (coe - g)
    fv = jpb * bvps_k if bvps_k else None
    return {"justified_pb": jpb, "fair_value": fv,
            "roe_fwd": roe_fwd, "coe": coe, "g": g, "bvps_k": bvps_k}


def gordon_ddm(d1_k, coe, g_div):
    """V2: DDM Gordon fair_value = D1 / (COE − g_div)."""
    d1_k, coe, g_div = _f(d1_k), _f(coe), _f(g_div)
    if None in (d1_k, coe, g_div):
        return {"error": "thiếu input (D1/COE/g_div)"}
    if coe <= g_div:
        return {"error": f"COE ({coe:.3f}) ≤ g_div ({g_div:.3f}) — DDM không áp dụng"}
    return {"fair_value": d1_k / (coe - g_div), "d1_k": d1_k, "coe": coe, "g_div": g_div}


def reverse_dcf(price_k, fcf0_k, coe, horizon, terminal_g=0.03):
    """V4: giải g (giai đoạn high-growth) sao cho PV(FCF) = giá thị trường.

    Mô hình 2 giai đoạn đơn giản trên FCF/CP:
      PV = Σ_{t=1..H} FCF0(1+g)^t/(1+r)^t + TV/(1+r)^H
      TV = FCF0(1+g)^H (1+terminal_g)/(r − terminal_g)
    Bisection trên g ∈ [−0.5, r−0.001].
    """
    price_k, fcf0_k, coe = _f(price_k), _f(fcf0_k), _f(coe)
    if None in (price_k, fcf0_k, coe) or price_k <= 0 or fcf0_k <= 0:
        return {"error": "thiếu/không hợp lệ (price/FCF/COE)"}
    if coe <= terminal_g:
        return {"error": "COE ≤ terminal_g"}

    def pv(g):
        total = 0.0
        for t in range(1, horizon + 1):
            total += fcf0_k * (1 + g) ** t / (1 + coe) ** t
        tv = fcf0_k * (1 + g) ** horizon * (1 + terminal_g) / (coe - terminal_g)
        return total + tv / (1 + coe) ** horizon

    lo, hi = -0.5, coe - 0.001
    if pv(lo) > price_k:
        return {"error": "giá thấp hơn cả kịch bản g rất âm — ngoài dải mô hình"}
    if pv(hi) < price_k:
        return {"implied_g": hi, "note": "giá ngầm định g ≥ COE (ngoài trần dải)"}
    for _ in range(60):
        mid = (lo + hi) / 2
        if pv(mid) < price_k:
            lo = mid
        else:
            hi = mid
    return {"implied_g": (lo + hi) / 2, "horizon": horizon, "terminal_g": terminal_g}


# ── V3: sector multiples thực (median cùng ngành ICB) ───────────────────────

def _load_industry_map(source: str) -> pd.DataFrame:
    """Listing.symbols_by_industries() — cache theo ngày (dùng chung mọi ticker)."""
    day = datetime.today().strftime("%Y-%m-%d")
    path = os.path.join(_cache_dir(), f"industry_map_{source}_{day}.parquet")
    if os.path.exists(path):
        try:
            return pd.read_parquet(path)
        except Exception:
            pass
    try:
        from vnstock_data import Listing
        df = Listing(source=source).symbols_by_industries()
        if isinstance(df, pd.DataFrame) and not df.empty:
            try:
                df.to_parquet(path)
            except Exception:
                pass
            return df
    except Exception:
        pass
    return pd.DataFrame()


def _peer_latest_multiples(sym: str, source: str) -> dict:
    """P/E, P/B, EV/EBITDA của quý gần nhất cho 1 peer."""
    try:
        from vnstock_data import Finance
        rq = _filter(_safe(Finance(symbol=sym, source=source).ratio), "quarter", 1)
        if rq.empty:
            return {}
        r = rq.iloc[0]
        return {"pe": _f(_get(r, "P/E")), "pb": _f(_get(r, "P/B")),
                "ev_ebitda": _f(_get(r, "EV/EBITDA"))}
    except Exception:
        return {}


def sector_multiples(symbol: str, source: str, cfg: dict, as_of: str) -> dict:
    """Median P/E, P/B, EV/EBITDA của các mã cùng ngành ICB (cache theo ngày).

    Trả: {n_peers, icb_name, icb_code, level, median_pe, median_pb,
          median_ev_ebitda, as_of, capped} hoặc {"error": ...}.
    """
    level = cfg["sector_level"]
    imap = _load_industry_map(source)
    if imap.empty:
        return {"error": "không lấy được industry map"}

    sym = symbol.upper()
    me = imap[(imap["symbol"] == sym) & (imap["icb_level"] == level)]
    if me.empty:
        return {"error": f"{sym} không có icb_level={level}"}
    icb_code = str(me.iloc[0]["icb_code"])
    icb_name = str(me.iloc[0]["icb_name"])

    # Cache median theo (icb_code, level, ngày) — dùng lại cho mọi mã cùng ngành.
    ck = os.path.join(_cache_dir(), f"sector_{source}_{icb_code}_L{level}_{as_of}.json")
    if os.path.exists(ck):
        try:
            with open(ck, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass

    peers = (imap[(imap["icb_code"].astype(str) == icb_code)
                  & (imap["icb_level"] == level)]["symbol"].astype(str).unique().tolist())
    peers = [p for p in peers if p != sym]
    capped = len(peers) > cfg["max_peers"]
    peers = sorted(peers)[: cfg["max_peers"]]

    pes, pbs, evs = [], [], []
    for p in peers:
        m = _peer_latest_multiples(p, source)
        if m.get("pe") and m["pe"] > 0:
            pes.append(m["pe"])
        if m.get("pb") and m["pb"] > 0:
            pbs.append(m["pb"])
        if m.get("ev_ebitda") and m["ev_ebitda"] > 0:
            evs.append(m["ev_ebitda"])
        time.sleep(cfg["peer_sleep"])

    def _med(xs):
        return round(float(pd.Series(xs).median()), 2) if xs else None

    out = {"icb_code": icb_code, "icb_name": icb_name, "level": level,
           "n_peers": len(peers), "capped": capped, "as_of": as_of,
           "median_pe": _med(pes), "median_pb": _med(pbs),
           "median_ev_ebitda": _med(evs),
           "n_pe": len(pes), "n_pb": len(pbs), "n_ev": len(evs)}
    try:
        with open(ck, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
    except Exception:
        pass
    return out


# ── Trích số từ frames đã fetch ─────────────────────────────────────────────

def _avg_roe_3y(ra: pd.DataFrame):
    """ROE_forward xấp xỉ = trung bình 3 năm gần nhất (ratio ROE (%) là fraction)."""
    if ra.empty or "ROE (%)" not in ra.columns:
        return None
    vals = [_f(x) for x in ra["ROE (%)"].tolist()]
    vals = [x for x in vals if x is not None][:3]
    return sum(vals) / len(vals) if vals else None


def _bvps_k(ra: pd.DataFrame, is_bank: bool, price_k=None, pb=None):
    """BVPS (nghìn đồng/CP). Thứ tự: cột trực tiếp → OE/shares → giá/P·B.

    LƯU Ý (VCI): cột 'Book Value/Share (VND)' thường trống và 'Owners Equity'
    hay = 0 ⇒ derive từ price/(P/B) là cách robust nhất (P/B do vnstock tính
    nhất quán với giá). fair_value = justified_pb × BVPS khi đó tương đương so
    justified P/B với P/B hiện tại — đúng về mặt phương pháp.
    """
    if not ra.empty:
        v = _f(_get(ra.iloc[0], "Book Value/Share (VND)"))
        if v and v > 0:
            return v / 1000.0
    # Owners Equity ở VCI thường = 0 / sai đơn vị ⇒ bỏ, derive từ giá/P·B (robust).
    if price_k and pb and pb > 0:
        return price_k / pb
    return None


def _eps_ttm_k(ia: pd.DataFrame):
    """EPS năm gần nhất (nghìn đồng/CP)."""
    if ia.empty:
        return None
    v = _f(_get(ia.iloc[0], "EPS basic (VND)", "EPS"))
    return v / 1000.0 if v is not None else None


def _shares(ra: pd.DataFrame):
    """Số CP lưu hành. LƯU Ý: cột 'Outstanding Shares (mil)' của VCI thực chất
    chứa RAW count (đã đối chiếu Market Cap / giá), KHÔNG phải triệu ⇒ dùng thẳng."""
    if ra.empty:
        return None
    sh = _f(_get(ra.iloc[0], "Outstanding Shares (mil)"))
    return sh if sh and sh > 0 else None


def _fcf_by_year(ca: pd.DataFrame) -> dict:
    """FCF = CFO − CapEx (A3), theo năm (VND)."""
    out = {}
    if ca.empty or "report_period" not in ca.columns:
        return out
    for _, r in ca.iterrows():
        yr = str(r.get("report_period", ""))[:4]
        cfo = _f(_get(r, "Net cash inflows/(outflows) from operating activities"))
        capex = _f(_get(r, "Purchases of fixed assets and other long term assets"))
        if cfo is not None and capex is not None:
            out[yr] = cfo - abs(capex)
    return out


def _lnst_by_year(ia: pd.DataFrame) -> dict:
    out = {}
    if ia.empty or "report_period" not in ia.columns:
        return out
    for _, r in ia.iterrows():
        yr = str(r.get("report_period", ""))[:4]
        v = _f(_get(r, "Attributable to parent company", "Net profit"))
        if v is not None:
            out[yr] = v
    return out


def _fetch_cash_dividends(symbol: str, source: str) -> dict:
    """Cổ tức tiền mặt theo năm (nghìn đồng/CP) từ Company.events().

    LƯU Ý: events() giới hạn ~50 dòng ⇒ depth thực tế chỉ ~2 năm gần nhất.
    """
    out = {}
    try:
        from vnstock_data import Company
        df = Company(symbol=symbol, source=source).events()
        if not isinstance(df, pd.DataFrame) or df.empty:
            return out
        if "category" in df.columns:
            df = df[df["category"].astype(str).str.upper() == "DIVIDEND"]
        for _, r in df.iterrows():
            vps = _f(r.get("value_per_share"))
            pd_ = str(r.get("payout_date") or r.get("exright_date") or "")[:4]
            if vps and vps > 0 and pd_.isdigit():
                out[pd_] = out.get(pd_, 0.0) + vps / 1000.0   # VND → nghìn đồng
    except Exception:
        pass
    return out


def dividend_profile(div_by_year: dict, lnst_by_year: dict, fcf_by_year: dict,
                     shares, eps_ttm_k, price_k, trade_date: str, cfg: dict) -> dict:
    """V2: payout, streak, FCF coverage, yield, GD-eligible."""
    if not div_by_year:
        return {"pays_dividend": False}

    years = sorted(div_by_year.keys())
    latest_yr = years[-1]
    dps_latest = div_by_year[latest_yr]         # nghìn đồng/CP (tổng năm gần nhất)

    # payout = DPS / EPS
    payout = (dps_latest / eps_ttm_k) if (eps_ttm_k and eps_ttm_k > 0) else None
    div_yield = (dps_latest / price_k) if (price_k and price_k > 0) else None

    # streak: số năm tăng cổ tức liên tiếp (từ mới về cũ) — giới hạn bởi depth data
    streak = 1
    for i in range(len(years) - 1, 0, -1):
        if div_by_year[years[i]] > div_by_year[years[i - 1]] + 1e-9:
            streak += 1
        else:
            break

    # FCF coverage: dùng năm gần nhất CÓ CẢ cổ tức lẫn FCF (dividend năm nay có
    # thể chưa có báo cáo dòng tiền tương ứng). Bank → fcf_by_year rỗng ⇒ None.
    fcf_cov = None
    common = sorted(set(years) & set(fcf_by_year.keys()))
    if shares and common:
        cy = common[-1]
        total_div_vnd = div_by_year[cy] * 1000.0 * shares
        if total_div_vnd > 0:
            fcf_cov = fcf_by_year[cy] / total_div_vnd

    gd_eligible = bool(
        streak >= cfg["streak_min_gd"]
        and (payout is not None and 0 < payout <= cfg["payout_max_gd"])
        and (fcf_cov is None or fcf_cov >= 1.0)
    )

    return {
        "pays_dividend": True,
        "dps_latest_k": dps_latest,
        "div_by_year": div_by_year,
        "payout_ratio": payout,
        "div_yield": div_yield,
        "streak_years": streak,
        "fcf_coverage": fcf_cov,
        "gd_eligible": gd_eligible,
        "data_years": len(years),
    }


# ── Định dạng ───────────────────────────────────────────────────────────────

def _pctf(v, d=1):
    return f"{v * 100:.{d}f}%" if isinstance(v, (int, float)) else "—"


def _kf(v, d=1):
    return f"{v:,.{d}f}" if isinstance(v, (int, float)) else "—"


def _xf(v, d=2):
    return f"{v:,.{d}f}x" if isinstance(v, (int, float)) else "—"


# ── Entry point: gọi từ build_financials_payload ────────────────────────────

def build_valuation_block(symbol: str, frames: dict, source: str = "VCI",
                          trade_date: str | None = None, beta: float | None = None) -> dict:
    """Tính toàn bộ định giá deterministic → markdown block + dict số thô.

    Args:
        frames: {ra, rq, ia, iq, ba, ca, latest_price, is_bank} từ fetcher.
        beta:   None → dùng cfg['default_beta'] (Task 10 sẽ wire beta thật).

    Returns:
        {valuation_md, data, error}
    """
    cfg = _cfg()
    as_of = (trade_date or datetime.today().strftime("%Y-%m-%d"))[:10]

    try:
        ra = frames.get("ra", pd.DataFrame())
        ia = frames.get("ia", pd.DataFrame())
        ca = frames.get("ca", pd.DataFrame())
        rq = frames.get("rq", pd.DataFrame())
        is_bank = bool(frames.get("is_bank", False))
        price_k = _f(frames.get("latest_price"))

        beta_used = _f(beta) if beta is not None else cfg["default_beta"]
        coe = cost_of_equity(beta_used, cfg["risk_free"], cfg["erp"])

        # P/B hiện tại (quý gần nhất, fallback năm) — để derive BVPS khi cột trống.
        pb_now = None
        if not rq.empty:
            pb_now = _f(_get(rq.iloc[0], "P/B"))
        if pb_now is None and not ra.empty:
            pb_now = _f(_get(ra.iloc[0], "P/B"))

        roe_fwd = _avg_roe_3y(ra)
        bvps_k = _bvps_k(ra, is_bank, price_k=price_k, pb=pb_now)
        eps_ttm_k = _eps_ttm_k(ia)
        shares = _shares(ra)

        # V2 cổ tức trước (payout dùng cho g)
        div_by_year = _fetch_cash_dividends(symbol, source)
        div = dividend_profile(div_by_year, _lnst_by_year(ia), _fcf_by_year(ca),
                               shares, eps_ttm_k, price_k, as_of, cfg)
        payout = div.get("payout_ratio")
        g = sustainable_growth(roe_fwd, payout, cfg["g_cap"])
        # Clamp g để spread COE−g ≥ buffer → tránh justified P/B / DDM nổ khi g→COE.
        if g is not None and coe is not None:
            g = max(0.0, min(g, coe - cfg["g_coe_buffer"]))

        data: dict = {
            "as_of": as_of, "is_bank": is_bank, "price_k": price_k,
            "beta": beta_used, "coe": coe, "roe_fwd": roe_fwd, "g": g,
            "bvps_k": bvps_k, "eps_ttm_k": eps_ttm_k, "pb_now": pb_now,
        }

        # V1 — justified P/B
        jpb = justified_pb(roe_fwd, coe, g, bvps_k)
        data["justified_pb"] = jpb

        # V2 — DDM: chỉ có ý nghĩa cho mã cổ tức THỰC (payout đủ cao). Mã payout
        # thấp (growth) không định giá bằng DDM → gắn cờ thay vì ra số vô nghĩa.
        ddm = {}
        is_div_stock = bool(payout is not None and payout >= cfg["ddm_min_payout"])
        if div.get("pays_dividend") and is_div_stock:
            g_div = g if g is not None else 0.0   # g đã clamp ≤ COE−buffer
            d1_k = (div["dps_latest_k"] * (1 + g_div)) if div.get("dps_latest_k") else None
            ddm = gordon_ddm(d1_k, coe, g_div)
        data["dividend"] = div
        data["is_dividend_stock"] = is_div_stock
        data["ddm"] = ddm

        # V3 — sector multiples thực
        sect = sector_multiples(symbol, source, cfg, as_of)
        data["sector"] = sect

        # V4 — reverse-DCF (non-bank, cần FCF dương)
        rdcf = {}
        if not is_bank and shares:
            fcf_years = _fcf_by_year(ca)
            if fcf_years:
                latest_fcf = fcf_years[sorted(fcf_years.keys())[-1]]
                fcf_ps_k = (latest_fcf / shares) / 1000.0 if latest_fcf else None
                if fcf_ps_k and fcf_ps_k > 0:
                    rdcf = reverse_dcf(price_k, fcf_ps_k, coe, cfg["dcf_horizon"])
        data["reverse_dcf"] = rdcf

        # ── Markdown ────────────────────────────────────────────────────────
        md = ["", "---",
              f"## 🧮 ĐỊNH GIÁ DETERMINISTIC (tính bằng máy — chỉ diễn giải, KHÔNG tự tính lại)",
              f"*COE = risk_free {_pctf(cfg['risk_free'])} + beta {beta_used:.2f} × ERP "
              f"{_pctf(cfg['erp'])} = **{_pctf(coe)}** · g bền vững = ROE_fwd × (1−payout) = "
              f"**{_pctf(g) if g is not None else '—'}** (cap {_pctf(cfg['g_cap'],0)})*",
              ""]

        # V1
        md.append("### Justified P/B" + (" (phương pháp chính cho ngân hàng)" if is_bank else ""))
        if jpb.get("error"):
            md.append(f"- Không tính được: {jpb['error']}")
        else:
            md.append(f"- ROE_fwd (bq 3 năm) = **{_pctf(roe_fwd)}** · BVPS = **{_kf(bvps_k)} nghìn đ** · "
                      f"P/B hiện tại = **{_xf(pb_now)}**")
            md.append(f"- Justified P/B = ({_pctf(roe_fwd)} − {_pctf(g)}) / ({_pctf(coe)} − {_pctf(g)}) "
                      f"= **{_xf(jpb['justified_pb'])}** "
                      f"({'rẻ hơn' if pb_now and jpb['justified_pb'] > pb_now else 'đắt hơn'} P/B hiện tại)")
            fv = jpb.get("fair_value")
            if fv:
                up = (fv / price_k - 1) if price_k else None
                md.append(f"- **Fair value = {_xf(jpb['justified_pb'])} × {_kf(bvps_k)} = "
                          f"{_kf(fv)} nghìn đ/CP**" +
                          (f" → upside **{_pctf(up)}** so giá {_kf(price_k)}" if up is not None else ""))
        md.append("")

        # V2
        md.append("### Cổ tức & GD-eligibility (mandate cổ tức tăng liên tục)")
        if not div.get("pays_dividend"):
            md.append("- Không tìm thấy dữ liệu cổ tức tiền mặt (Company.events).")
        else:
            md.append(f"- DPS gần nhất = **{_kf(div['dps_latest_k'],2)} nghìn đ** · "
                      f"Payout = **{_pctf(payout) if payout is not None else '—'}** · "
                      f"Div yield = **{_pctf(div['div_yield']) if div['div_yield'] is not None else '—'}**")
            cov = div.get("fcf_coverage")
            cov_str = _xf(cov) if cov is not None else ("n/a (ngân hàng)" if is_bank else "n/a")
            md.append(f"- Chuỗi năm tăng cổ tức = **{div['streak_years']}** "
                      f"(⚠ depth data ~{div['data_years']} năm — events() giới hạn) · "
                      f"FCF coverage = {cov_str}")
            md.append(f"- **GD-eligible: {'CÓ' if div['gd_eligible'] else 'KHÔNG'}** "
                      f"(ngưỡng: streak ≥ {cfg['streak_min_gd']}, payout ≤ {_pctf(cfg['payout_max_gd'],0)}, "
                      f"FCF cover ≥ 1.0x)")
            if not is_div_stock:
                md.append(f"- *DDM bỏ qua: payout {_pctf(payout) if payout is not None else '—'} "
                          f"< {_pctf(cfg['ddm_min_payout'],0)} — đây là mã tăng trưởng, "
                          f"không định giá bằng cổ tức. Dùng justified P/B hoặc P/E ngành.*")
            elif not ddm.get("error") and ddm.get("fair_value"):
                fv = ddm["fair_value"]
                up = (fv / price_k - 1) if price_k else None
                md.append(f"- **DDM (Gordon) fair value = {_kf(ddm['d1_k'],2)} / "
                          f"({_pctf(coe)} − {_pctf(ddm['g_div'])}) = {_kf(fv)} nghìn đ/CP**" +
                          (f" → upside **{_pctf(up)}**" if up is not None else ""))
            elif ddm.get("error"):
                md.append(f"- DDM không áp dụng: {ddm['error']}")
        md.append("")

        # V3
        md.append("### Sector multiples THỰC (median cùng ngành — thay 'Benchmark VN' tự bịa)")
        if sect.get("error"):
            md.append(f"- Không tính được: {sect['error']}")
        else:
            md.append(f"- Ngành ICB (cấp {sect['level']}): **{sect['icb_name']}** "
                      f"({sect['n_peers']} mã peer{', có cap' if sect.get('capped') else ''}, "
                      f"tại {sect['as_of']})")
            md.append(f"- Median **P/E = {_xf(sect['median_pe'])}** (n={sect['n_pe']}) · "
                      f"**P/B = {_xf(sect['median_pb'])}** (n={sect['n_pb']}) · "
                      f"**EV/EBITDA = {_xf(sect['median_ev_ebitda'])}** (n={sect['n_ev']})")
        md.append("")

        # V4
        if rdcf and not rdcf.get("error"):
            md.append("### Reverse-DCF — tăng trưởng thị trường đang ngầm định")
            md.append(f"- Giá hiện tại ngầm định FCF/CP tăng **{_pctf(rdcf['implied_g'])}/năm** "
                      f"trong {rdcf.get('horizon', cfg['dcf_horizon'])} năm "
                      f"(terminal g = {_pctf(rdcf.get('terminal_g', 0.03))}).")
            md.append("- Dùng cho debate: Bull lập luận vì sao thực tế cao hơn, Bear vì sao thấp hơn.")
            md.append("")

        md.append("---")
        return {"valuation_md": "\n".join(md), "data": data, "error": None}

    except Exception as e:
        return {"valuation_md": "", "data": {}, "error": str(e)}


# ── Chạy độc lập để verify (tắt LLM vẫn ra số) ──────────────────────────────

if __name__ == "__main__":
    import sys
    from tradingagents.agents.utils.vn_financial_fetcher import fetch_vn_financial_context

    sym = sys.argv[1] if len(sys.argv) > 1 else "VCB"
    ctx = fetch_vn_financial_context(sym)
    if ctx.get("error"):
        print("FETCH ERROR:", ctx["error"])
        sys.exit(1)
    res = build_valuation_block(sym, ctx.get("frames", {}))
    if res.get("error"):
        print("VALUATION ERROR:", res["error"])
    else:
        print(res["valuation_md"])
