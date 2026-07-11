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

Ba lớp bổ sung (Task nâng cấp — tin cậy hơn, bớt lạc quan hệ thống):
  L1  P/B percentile band 5 năm     — phân phối P/B lịch sử của chính mã đó
      (P10/P25/P50/P75/P90 + percentile hiện tại); fair value band = [P25,P75]×BVPS,
      midpoint = P50×BVPS. Là "neo thực nghiệm" so được các phương pháp mô hình.
  L2  Sanity check corridor 2 chiều  — quy mỗi phương pháp về implied P/B; CHỈ loại
      khi ngoài CẢ band lịch sử [P10,P90] LẪN hành lang [corridor_low,high]×P/B_now
      (mã re-rate/de-rate mạnh không bị loại oan). Cross-method rescue: phương pháp
      bị loại được khôi phục khi fair value đồng thuận (±rescue_tolerance) với BẤT KỲ
      anchor còn sống (bằng chứng mạnh), hoặc với nhóm ≥2 phương pháp cùng bị loại
      (yếu hơn). ROE_fwd của justified P/B fade khi ROE giảm, floor tại COE.
  L3  Route ngành ICB + composite   — BANK / REAL_ESTATE / DEFAULT chọn bộ phương
      pháp & trọng số khác nhau (default_config["valuation"]["composite_weights"]);
      phương pháp bị loại → renormalize. Band mid bị HẠ vai trò (loại khỏi composite,
      chỉ tham khảo) khi P/B hiện tại là outlier (percentile ≤10 hoặc ≥90). Mô hình
      2 TIER: khi composite-tier < 2 phương pháp, PROMOTE reference-tier (phương pháp
      reliable nhưng không nằm trong trọng số route, vd sector với bank) chia đều phần
      trọng số thiếu. Chỉ khi sau promote vẫn < 2 → composite điểm = None NHƯNG luôn
      trả TP range [min,max] fair value (độ tin cậy thấp) — markdown luôn có 1 dòng TP.

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
    "pb_band_quarters": 20,      # số quý lịch sử cho band P/B (5 năm)
    "pb_band_min_quarters": 8,   # tối thiểu số quý để band có ý nghĩa
    "corridor_low": 0.6,         # sàn hành lang quanh P/B hiện tại
    "corridor_high": 1.5,        # trần hành lang quanh P/B hiện tại
    "rescue_tolerance": 1.3,     # max/min fair value để rescue nhóm đồng thuận chéo
    "composite_weights": {       # trọng số composite theo route ngành (xem default_config)
        "BANK":          {"justified_pb": 0.45, "ddm": 0.25, "pb_band": 0.30},
        "REAL_ESTATE":   {"pb_band": 0.60, "sector": 0.40},
        "DEFAULT":       {"sector": 0.40, "pb_band": 0.35, "ddm": 0.25},
        "DEFAULT_NODIV": {"sector": 0.55, "pb_band": 0.45},
    },
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


# ── L1: P/B percentile band lịch sử 5 năm ───────────────────────────────────

def _pb_quarterly_series(symbol: str, source: str, rq: pd.DataFrame,
                          cfg: dict, as_of: str) -> list:
    """Chuỗi P/B theo quý (mới→cũ), tối đa cfg['pb_band_quarters'] quý.

    Ưu tiên dùng frame rq đã fetch; nếu depth < 12 quý thì fetch thêm full ratio
    history (cache theo ngày giống sector_multiples) để có đủ ~5 năm.
    """
    want = int(cfg["pb_band_quarters"])

    def _extract(df: pd.DataFrame) -> list:
        if df.empty or "P/B" not in df.columns:
            return []
        xs = [_f(x) for x in df["P/B"].tolist()]
        return [x for x in xs if x is not None and x > 0]

    pbs = _extract(rq)
    if len(pbs) >= 12:
        return pbs[:want]

    ck = os.path.join(_cache_dir(), f"pbhist_{source}_{symbol.upper()}_{as_of}.json")
    if os.path.exists(ck):
        try:
            with open(ck, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, list) and len(cached) >= len(pbs):
                return [float(x) for x in cached][:want]
        except Exception:
            pass

    try:
        from vnstock_data import Finance
        raw = _safe(Finance(symbol=symbol, source=source).ratio)
        rqf = _filter(raw, "quarter", want)
        fresh = _extract(rqf)
        if len(fresh) > len(pbs):
            pbs = fresh
            try:
                with open(ck, "w", encoding="utf-8") as fh:
                    json.dump(pbs, fh)
            except Exception:
                pass
    except Exception:
        pass
    return pbs[:want]


def pb_history_band(symbol: str, source: str, rq: pd.DataFrame, bvps_k,
                    pb_now, cfg: dict, as_of: str) -> dict:
    """Band P/B lịch sử 5 năm + fair value band từ BVPS hiện tại.

    Trả: {pb_current, pb_p10, pb_p25, pb_p50, pb_p75, pb_p90, pb_percentile_now,
          fv_band_low, fv_band_mid, fv_band_high, n_quarters}
    hoặc {"error": ...} nếu số quý < cfg['pb_band_min_quarters'].
    """
    pbs = _pb_quarterly_series(symbol, source, rq, cfg, as_of)
    n = len(pbs)
    if n < int(cfg["pb_band_min_quarters"]):
        return {"error": f"chỉ {n} quý P/B lịch sử (< {cfg['pb_band_min_quarters']}) — bỏ band",
                "n_quarters": n}

    s = pd.Series(pbs, dtype="float64")
    q = s.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    p10, p25, p50, p75, p90 = (float(q.loc[x]) for x in (0.10, 0.25, 0.50, 0.75, 0.90))

    pb_cur = _f(pb_now)
    pct_now = None
    if pb_cur is not None:
        pct_now = round(sum(1 for x in pbs if x <= pb_cur) / n * 100.0, 1)

    bv = _f(bvps_k)
    fv_low = p25 * bv if bv else None
    fv_mid = p50 * bv if bv else None
    fv_high = p75 * bv if bv else None

    return {
        "pb_current": pb_cur,
        "pb_p10": round(p10, 3), "pb_p25": round(p25, 3), "pb_p50": round(p50, 3),
        "pb_p75": round(p75, 3), "pb_p90": round(p90, 3),
        "pb_percentile_now": pct_now,
        "fv_band_low": fv_low, "fv_band_mid": fv_mid, "fv_band_high": fv_high,
        "n_quarters": n,
    }


# ── L3: route ngành ICB ─────────────────────────────────────────────────────

def _route_industry(symbol: str, source: str, is_bank: bool) -> dict:
    """Xác định nhóm ngành để route phương pháp: BANK / REAL_ESTATE / DEFAULT.

    is_bank (từ NIM detection) là tín hiệu ngân hàng tin cậy nhất → ưu tiên.
    Còn lại tra icb_name trong industry map (substring, robust theo cấp ICB).
    Trả: {route, icb_name}.
    """
    if is_bank:
        return {"route": "BANK", "icb_name": "Ngân hàng"}
    try:
        imap = _load_industry_map(source)
        if not imap.empty:
            rows = imap[imap["symbol"].astype(str) == symbol.upper()]
            names = " ".join(str(x) for x in rows.get("icb_name", pd.Series(dtype=str)).tolist())
            low = names.lower()
            if "bất động sản" in low or "real estate" in low:
                return {"route": "REAL_ESTATE", "icb_name": "Bất động sản"}
            if "ngân hàng" in low or "bank" in low:
                return {"route": "BANK", "icb_name": "Ngân hàng"}
            picked = next((str(x) for x in rows.get("icb_name", pd.Series(dtype=str)).tolist() if str(x)), "")
            return {"route": "DEFAULT", "icb_name": picked or "—"}
    except Exception:
        pass
    return {"route": "DEFAULT", "icb_name": "—"}


def _implied_pb(fair_value, bvps_k):
    """Quy fair value (nghìn đồng/CP) về implied P/B = fair_value / BVPS."""
    fv, bv = _f(fair_value), _f(bvps_k)
    if fv is None or not bv:
        return None
    return fv / bv


def band_corridor_reliable(ipb, p10, p90, pb_now, corridor_low, corridor_high) -> tuple:
    """Sanity check 2 chiều (L2). reliable trừ khi implied P/B nằm ngoài CẢ:
      (a) band lịch sử [p10, p90], VÀ
      (b) corridor quanh P/B hiện tại [corridor_low·pb_now, corridor_high·pb_now].
    Nằm trong ít nhất một → reliable. Thiếu band/ipb → reliable (không đủ cơ sở loại).

    Trả (reliable: bool, reason: str). reason nêu cả hai mốc vi phạm khi loại.
    """
    ipb = _f(ipb)
    if ipb is None or p10 is None or p90 is None:
        return True, ""
    clow = (corridor_low * pb_now) if pb_now else None
    chigh = (corridor_high * pb_now) if pb_now else None
    out_band = ipb < p10 or ipb > p90
    out_corr = clow is not None and (ipb < clow or ipb > chigh)
    if not (out_band and out_corr):
        return True, ""
    parts = []
    if ipb > p90:
        parts.append(f"> P90 lịch sử {_xf(p90)}")
    elif ipb < p10:
        parts.append(f"< P10 lịch sử {_xf(p10)}")
    if ipb > chigh:
        parts.append(f"> {corridor_high:g}× P/B hiện tại {_xf(pb_now)} ({_xf(chigh)})")
    elif ipb < clow:
        parts.append(f"< {corridor_low:g}× P/B hiện tại {_xf(pb_now)} ({_xf(clow)})")
    return False, f"implied P/B {_xf(ipb)} " + " và ".join(parts)


_METHOD_LABELS = {
    "justified_pb": "Justified P/B", "ddm": "DDM (Gordon)",
    "sector": "Sector multiples", "pb_band": "P/B band (mid, P50)",
}
# Anchor cho rescue = các phương pháp mô hình độc lập (không tính band mid, vốn là
# cược mean-reversion thống kê chứ không phải định giá độc lập).
_ANCHOR_METHODS = ("justified_pb", "ddm", "sector")


def band_mid_demoted(pct_now) -> bool:
    """True nếu P/B hiện tại là outlier (percentile ≤10 hoặc ≥90) → hạ vai trò
    band mid khỏi composite (chỉ tham khảo, không làm anchor mean-reversion)."""
    p = _f(pct_now)
    return p is not None and (p <= 10 or p >= 90)


def apply_cross_method_rescue(methods: dict, rescue_tolerance: float) -> list:
    """Khôi phục reliable cho phương pháp mô hình bị loại vì 'sanity' khi fair value
    đồng thuận (max/min ≤ rescue_tolerance) với:
      Rule A — BẤT KỲ anchor còn sống (phương pháp mô hình reliable=True): đồng thuận
               với neo còn sống là bằng chứng MẠNH (ưu tiên), hoặc
      Rule B — nhóm ≥2 phương pháp cùng bị loại đồng thuận nhau (bằng chứng yếu hơn).
    Band mid KHÔNG dùng làm anchor (cược mean-reversion, không phải neo độc lập).
    Mutate methods tại chỗ; trả list tên phương pháp được rescue."""
    def _fv(n):
        return _f(methods.get(n, {}).get("fair_value"))

    rescued = []
    # Rule A: đồng thuận với anchor còn sống.
    anchors = {n: _fv(n) for n in _ANCHOR_METHODS
               if methods.get(n, {}).get("reliable") and _fv(n) and _fv(n) > 0}
    for n in _ANCHOR_METHODS:
        if methods.get(n, {}).get("drop_kind") != "sanity" or not _fv(n):
            continue
        fv = _fv(n)
        for aname, afv in anchors.items():
            if aname == n:
                continue
            if min(fv, afv) > 0 and max(fv, afv) / min(fv, afv) <= rescue_tolerance:
                methods[n]["reliable"] = True
                methods[n]["drop_kind"] = "rescued"
                methods[n]["reason"] = f"đồng thuận với {_METHOD_LABELS[aname]} — khôi phục"
                rescued.append(n)
                break

    # Rule B: nhóm ≥2 phương pháp còn bị loại đồng thuận nhau.
    remaining = [n for n in _ANCHOR_METHODS
                 if n not in rescued
                 and methods.get(n, {}).get("drop_kind") == "sanity" and _fv(n)]
    if len(remaining) >= 2:
        fvs = [_fv(n) for n in remaining]
        if min(fvs) > 0 and max(fvs) / min(fvs) <= rescue_tolerance:
            for n in remaining:
                methods[n]["reliable"] = True
                methods[n]["drop_kind"] = "rescued"
                methods[n]["reason"] = "đồng thuận chéo — regime shift so với band lịch sử"
                rescued.append(n)
    return rescued


def composite_fair_value(methods: dict, route: str, weights_cfg: dict,
                         pays_dividend: bool) -> dict:
    """Tổng hợp fair value theo mô hình 2 tier cho MỌI route.

    - Composite tier: các phương pháp nằm trong route's composite_weights.
    - Reference tier: phương pháp reliable=True KHÔNG nằm trong composite_weights
      (vd sector với BANK, justified P/B với DEFAULT) — bình thường chỉ tham khảo.

    Quy tắc: lấy composite-tier reliable + có fair_value. Nếu < 2 → PROMOTE các
    reference-tier reliable vào composite, chia đều "phần trọng số thiếu"
    (1 − tổng trọng số composite-tier sống sót) cho chúng, rồi renormalize. Chỉ
    khi sau promote vẫn < 2 → composite điểm = None + TP range [min,max] (mọi
    phương pháp có fair_value, kể cả reliable=False) để phase sau vẫn có số.

    Trả: {composite, weights_used, n_used, converged, tp_range_low,
          tp_range_high, promoted}.
    """
    key = route
    if route == "DEFAULT" and not pays_dividend:
        key = "DEFAULT_NODIV"
    base = dict(weights_cfg.get(key, weights_cfg.get("DEFAULT", {})))

    # TP range fallback: mọi phương pháp không error (có fair_value dương).
    all_fvs = [fv for m in methods.values()
               if (fv := _f(m.get("fair_value"))) is not None and fv > 0]
    tp_low = min(all_fvs) if all_fvs else None
    tp_high = max(all_fvs) if all_fvs else None

    def _fv_ok(name):
        m = methods.get(name)
        if not m or not m.get("reliable"):
            return None
        fv = _f(m.get("fair_value"))
        return fv if (fv is not None and fv > 0) else None

    # Composite tier.
    usable = {}
    for name, w in base.items():
        fv = _fv_ok(name)
        if fv is not None:
            usable[name] = (w, fv)

    # Promotion: composite tier < 2 → kéo reference-tier reliable vào, chia đều
    # phần trọng số thiếu.
    promoted = []
    if len(usable) < 2:
        ref = [name for name in methods
               if name not in base and _fv_ok(name) is not None]
        if ref:
            sum_surv = sum(w for w, _ in usable.values())
            missing = 1.0 - sum_surv
            if missing <= 0:
                missing = 1.0
            per = missing / len(ref)
            for name in ref:
                usable[name] = (per, _fv_ok(name))
                promoted.append(name)

    if len(usable) <= 1:
        return {"composite": None, "weights_used": {}, "n_used": len(usable),
                "converged": False, "tp_range_low": tp_low, "tp_range_high": tp_high,
                "promoted": []}

    wsum = sum(w for w, _ in usable.values())
    weights_used = {name: round(w / wsum, 4) for name, (w, _) in usable.items()}
    composite = sum((w / wsum) * fv for w, fv in usable.values())
    return {"composite": composite, "weights_used": weights_used,
            "n_used": len(usable), "converged": True,
            "tp_range_low": tp_low, "tp_range_high": tp_high, "promoted": promoted}


# ── Trích số từ frames đã fetch ─────────────────────────────────────────────

def _avg_roe_3y(ra: pd.DataFrame):
    """ROE trung bình 3 năm gần nhất (ratio ROE (%) là fraction)."""
    if ra.empty or "ROE (%)" not in ra.columns:
        return None
    vals = [_f(x) for x in ra["ROE (%)"].tolist()]
    vals = [x for x in vals if x is not None][:3]
    return sum(vals) / len(vals) if vals else None


def roe_forward(ra: pd.DataFrame, coe) -> dict:
    """ROE_fwd bớt lạc quan hệ thống (L2).

    - ROE_fwd mặc định = trung bình 3 năm (như cũ).
    - Nếu ROE đang giảm (năm gần nhất < TB 3 năm) → fade thêm quanh xu hướng giảm:
        ROE_fwd = ROE_gần_nhất − 0.5 × (TB_3y − ROE_gần_nhất)
      (tức tiếp tục giảm nửa nhịp giảm gần nhất — thận trọng hơn cả năm gần nhất).
    - Floor tại COE: không cho ROE_fwd < COE (tránh justified P/B < 1 vô nghĩa với
      ngân hàng đầu ngành). Chạm floor → floored=True để flag.

    Trả: {roe_fwd, roe_latest, roe_avg3y, declining, floored} hoặc {} nếu thiếu data.
    """
    avg3y = _avg_roe_3y(ra)
    if avg3y is None:
        return {}
    vals = [_f(x) for x in ra["ROE (%)"].tolist()]
    vals = [x for x in vals if x is not None]
    roe_latest = vals[0] if vals else None
    if roe_latest is None:
        return {"roe_fwd": avg3y, "roe_latest": None, "roe_avg3y": avg3y,
                "declining": False, "floored": False}

    declining = roe_latest < avg3y
    if declining:
        roe_fwd = roe_latest - 0.5 * (avg3y - roe_latest)
    else:
        roe_fwd = avg3y

    floored = False
    if coe is not None and roe_fwd < coe:
        roe_fwd = coe
        floored = True

    return {"roe_fwd": roe_fwd, "roe_latest": roe_latest, "roe_avg3y": avg3y,
            "declining": declining, "floored": floored}


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

        # ROE_fwd với fade khi ROE giảm (L2) — bớt lạc quan hệ thống, floor tại COE.
        roe_info = roe_forward(ra, coe)
        roe_fwd = roe_info.get("roe_fwd")
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

        # L1 — P/B percentile band lịch sử 5 năm (neo thực nghiệm cho sanity check).
        band = pb_history_band(symbol, source, rq, bvps_k, pb_now, cfg, as_of)

        # L3 — route ngành để chọn phương pháp + trọng số.
        route_info = _route_industry(symbol, source, is_bank)
        route = route_info["route"]

        data: dict = {
            "as_of": as_of, "is_bank": is_bank, "price_k": price_k,
            "beta": beta_used, "coe": coe, "roe_fwd": roe_fwd, "g": g,
            "bvps_k": bvps_k, "eps_ttm_k": eps_ttm_k, "pb_now": pb_now,
            "roe_info": roe_info, "pb_band": band, "route": route,
            "icb_name": route_info.get("icb_name"),
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
        # Sector fair value: DEFAULT ưu tiên median P/E × EPS, fallback median P/B × BVPS;
        # BANK/REAL_ESTATE dùng median P/B × BVPS (P/E ngành ngân hàng nhiễu).
        sector_fv = None
        if not sect.get("error"):
            if route == "DEFAULT" and sect.get("median_pe") and eps_ttm_k and eps_ttm_k > 0:
                sector_fv = sect["median_pe"] * eps_ttm_k
            elif sect.get("median_pb") and bvps_k:
                sector_fv = sect["median_pb"] * bvps_k
        data["sector_fair_value"] = sector_fv

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

        # ── L2 — sanity check corridor 2 chiều + cross-method rescue ───────────
        # Mỗi phương pháp: {fair_value, implied_pb, reliable, reason, drop_kind}.
        # drop_kind: None=reliable, "sanity"=ngoài band+corridor, "structural"=không
        # phù hợp route/không đủ điều kiện, "error", "no_fv", "outlier_demote".
        # Chỉ drop_kind="sanity" mới đủ tư cách được rescue.
        band_ok = not band.get("error")
        p10 = band.get("pb_p10") if band_ok else None
        p90 = band.get("pb_p90") if band_ok else None
        pct_now = band.get("pb_percentile_now") if band_ok else None
        clow = (cfg["corridor_low"] * pb_now) if pb_now else None
        chigh = (cfg["corridor_high"] * pb_now) if pb_now else None

        def _sanity(fair_value, no_fv_reason: str = ""):
            """Loại CHỈ KHI implied P/B ngoài CẢ band [P10,P90] LẪN corridor
            [clow,chigh] (band_corridor_reliable). Nằm trong ít nhất một → reliable."""
            fv = _f(fair_value)
            if fv is None or fv <= 0:
                return {"fair_value": None, "implied_pb": None, "reliable": False,
                        "reason": no_fv_reason or "không có fair value", "drop_kind": "no_fv"}
            ipb = _implied_pb(fv, bvps_k)
            if not band_ok or ipb is None:
                return {"fair_value": fv, "implied_pb": ipb, "reliable": True,
                        "reason": "", "drop_kind": None}
            reliable, reason = band_corridor_reliable(
                ipb, p10, p90, pb_now, cfg["corridor_low"], cfg["corridor_high"])
            return {"fair_value": fv, "implied_pb": ipb, "reliable": reliable,
                    "reason": reason, "drop_kind": None if reliable else "sanity"}

        methods: dict = {}

        # justified P/B — REAL_ESTATE không phù hợp (cần RNAV, structural)
        if route == "REAL_ESTATE":
            methods["justified_pb"] = {
                "fair_value": jpb.get("fair_value"), "implied_pb": jpb.get("justified_pb"),
                "reliable": False, "drop_kind": "structural",
                "reason": "không phù hợp cho BĐS — cần RNAV (quỹ đất/presales)"}
        elif jpb.get("error"):
            methods["justified_pb"] = {"fair_value": None, "implied_pb": None,
                                        "reliable": False, "reason": jpb["error"],
                                        "drop_kind": "error"}
        else:
            m = _sanity(jpb.get("fair_value"))
            if m.get("implied_pb") is None:
                m["implied_pb"] = jpb.get("justified_pb")
            if roe_info.get("floored") and m["reliable"]:
                m["reason"] = "ROE_fwd chạm sàn COE (mã đầu ngành ROE giảm mạnh)"
            methods["justified_pb"] = m

        # DDM — REAL_ESTATE không phù hợp; mã không phải cổ tức thực → structural
        if route == "REAL_ESTATE":
            methods["ddm"] = {"fair_value": ddm.get("fair_value"), "implied_pb": None,
                              "reliable": False, "drop_kind": "structural",
                              "reason": "không phù hợp cho BĐS — cần RNAV (quỹ đất/presales)"}
        elif not (div.get("pays_dividend") and is_div_stock):
            methods["ddm"] = {"fair_value": None, "implied_pb": None, "reliable": False,
                              "drop_kind": "structural",
                              "reason": f"payout {_pctf(payout) if payout is not None else '—'} "
                                        f"< {_pctf(cfg['ddm_min_payout'],0)} — không phải mã cổ tức"}
        elif ddm.get("error"):
            methods["ddm"] = {"fair_value": None, "implied_pb": None,
                              "reliable": False, "reason": ddm["error"], "drop_kind": "error"}
        else:
            m = _sanity(ddm.get("fair_value"))
            if m.get("implied_pb") is None:
                m["implied_pb"] = _implied_pb(ddm.get("fair_value"), bvps_k)
            methods["ddm"] = m

        # sector multiples
        if sect.get("error"):
            methods["sector"] = {"fair_value": None, "implied_pb": None,
                                  "reliable": False, "reason": sect["error"],
                                  "drop_kind": "error"}
        else:
            methods["sector"] = _sanity(sector_fv, "thiếu median P/E & P/B")

        # pb_band mid — implied P/B = P50, luôn trong band theo định nghĩa.
        # HẠ vai trò (loại khỏi composite) khi P/B hiện tại là outlier (pct ≤10 / ≥90).
        if band_ok and band.get("fv_band_mid"):
            if band_mid_demoted(pct_now):
                methods["pb_band"] = {
                    "fair_value": band["fv_band_mid"], "implied_pb": band.get("pb_p50"),
                    "reliable": False, "drop_kind": "outlier_demote",
                    "reason": f"P/B hiện tại ở P{_kf(pct_now,0)} — band lịch sử là cược "
                              f"mean-reversion, không dùng làm anchor"}
            else:
                methods["pb_band"] = {"fair_value": band["fv_band_mid"],
                                      "implied_pb": band.get("pb_p50"),
                                      "reliable": True, "reason": "", "drop_kind": None}
        else:
            methods["pb_band"] = {"fair_value": None, "implied_pb": None,
                                  "reliable": False, "drop_kind": "no_fv",
                                  "reason": band.get("error", "không có band")}

        # Cross-method rescue: ≥2 phương pháp (trừ band mid) bị loại vì "sanity"
        # nhưng fair value đồng thuận trong ±rescue_tolerance → khôi phục cả nhóm.
        apply_cross_method_rescue(methods, cfg["rescue_tolerance"])
        data["methods"] = methods

        # ── L3 — composite fair value ─────────────────────────────────────────
        comp = composite_fair_value(methods, route, cfg["composite_weights"],
                                     bool(div.get("pays_dividend")))
        data["composite"] = comp

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
            if roe_info.get("declining"):
                md.append(f"- ⚠ ROE đang giảm: năm gần nhất **{_pctf(roe_info.get('roe_latest'))}** "
                          f"< bq 3 năm **{_pctf(roe_info.get('roe_avg3y'))}** → ROE_fwd fade xuống "
                          f"**{_pctf(roe_fwd)}**" +
                          (" (chạm sàn COE)" if roe_info.get("floored") else ""))
            else:
                md.append(f"- ROE_fwd (bq 3 năm) = **{_pctf(roe_fwd)}**")
            md.append(f"- BVPS = **{_kf(bvps_k)} nghìn đ** · P/B hiện tại = **{_xf(pb_now)}**")
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
            if sector_fv:
                _basis = ("median P/E × EPS" if (route == "DEFAULT" and sect.get("median_pe")
                          and eps_ttm_k) else "median P/B × BVPS")
                md.append(f"- Fair value ({_basis}) = **{_kf(sector_fv)} nghìn đ/CP**")
        md.append("")

        # V4
        if rdcf and not rdcf.get("error"):
            md.append("### Reverse-DCF — tăng trưởng thị trường đang ngầm định")
            md.append(f"- Giá hiện tại ngầm định FCF/CP tăng **{_pctf(rdcf['implied_g'])}/năm** "
                      f"trong {rdcf.get('horizon', cfg['dcf_horizon'])} năm "
                      f"(terminal g = {_pctf(rdcf.get('terminal_g', 0.03))}).")
            md.append("- Dùng cho debate: Bull lập luận vì sao thực tế cao hơn, Bear vì sao thấp hơn.")
            md.append("")

        # ── L1/L2/L3 — Band + bảng tổng hợp + composite ───────────────────────
        md.append("### 📌 Tổng hợp định giá (composite) & sanity check bằng band lịch sử")

        # Band lịch sử
        if band_ok:
            md.append(f"- P/B hiện tại **{_xf(band.get('pb_current'))}** = percentile "
                      f"**P{_kf(band.get('pb_percentile_now'),0)}** trong "
                      f"{band['n_quarters']} quý (5 năm) · "
                      f"band P10–P90 = [{_xf(band['pb_p10'])}, {_xf(band['pb_p90'])}]")
            md.append(f"- Fair value band [P25×BVPS, P75×BVPS] = "
                      f"[**{_kf(band['fv_band_low'])}**, **{_kf(band['fv_band_high'])}**] nghìn đ · "
                      f"midpoint (P50) = **{_kf(band['fv_band_mid'])}** nghìn đ")
            if methods["pb_band"].get("drop_kind") == "outlier_demote":
                md.append(f"- ⚠ P/B hiện tại ở P{_kf(pct_now,0)} (outlier) — band mid CHỈ tham khảo, "
                          f"KHÔNG vào composite (cược mean-reversion không dùng làm anchor).")
        else:
            md.append(f"- Band P/B lịch sử: {band.get('error', 'không có')} (bỏ sanity check band)")
        md.append("")

        # Bảng phương pháp
        md.append("| Phương pháp | Fair value (nghìn đ) | Implied P/B | Đáng tin | Ghi chú |")
        md.append("|---|---|---|---|---|")
        for name in ("justified_pb", "ddm", "sector", "pb_band"):
            m = methods.get(name, {})
            fv_s = _kf(m.get("fair_value")) if m.get("fair_value") else "—"
            ipb_s = _xf(m.get("implied_pb")) if m.get("implied_pb") is not None else "—"
            ok_s = "✓" if m.get("reliable") else "✗"
            note = (m.get("reason") or "").replace("|", "/")
            md.append(f"| {_METHOD_LABELS[name]} | {fv_s} | {ipb_s} | {ok_s} | {note} |")
        md.append("")

        # Composite TP
        if comp.get("converged") and comp.get("composite"):
            tp = comp["composite"]
            up = (tp / price_k - 1) if price_k else None
            ud = "upside" if (up is not None and up >= 0) else "downside"
            md.append(f"- **TP (composite fair value): {_kf(tp)} nghìn đ**" +
                      (f" → {ud} **{_pctf(up)}** so giá {_kf(price_k)} nghìn đ" if up is not None else ""))
            wstr = " + ".join(f"{_pctf(w,0)} {_METHOD_LABELS[n]}"
                              for n, w in comp["weights_used"].items())
            md.append(f"- Route ngành: **{route}** ({route_info.get('icb_name','—')}) · "
                      f"trọng số (đã renormalize): {wstr}")
            if comp.get("promoted"):
                pstr = ", ".join(_METHOD_LABELS[n] for n in comp["promoted"])
                md.append(f"- *Promote reference-tier ({pstr}) vào composite vì composite "
                          f"tier < 2 phương pháp sau khi loại/hạ.*")
        else:
            lo, hi = comp.get("tp_range_low"), comp.get("tp_range_high")
            if lo is not None and hi is not None:
                up_lo = (lo / price_k - 1) if price_k else None
                up_hi = (hi / price_k - 1) if price_k else None
                rng = (f" (so giá {_kf(price_k)}: {_pctf(up_lo)} … {_pctf(up_hi)})"
                       if up_lo is not None else "")
                md.append(f"- **TP range = [{_kf(lo)}, {_kf(hi)}] nghìn đ — ĐỘ TIN CẬY THẤP "
                          f"(dùng range, KHÔNG dùng điểm)**{rng}")
                md.append(f"- *Chỉ {comp.get('n_used',0)} phương pháp reliable (< 2) — các phương "
                          f"pháp phân kỳ; range trải trên MỌI phương pháp có fair value.*")
            else:
                md.append("- **TP: không đủ dữ liệu định giá (không phương pháp nào ra fair value).**")
            md.append(f"- Route ngành: **{route}** ({route_info.get('icb_name','—')})")
        _promoted = comp.get("promoted") or []
        if route == "REAL_ESTATE":
            md.append("- *Composite BĐS là proxy (band + sector), CHƯA có RNAV "
                      "(quỹ đất/presales) — cần bổ sung để định giá đầy đủ.*")
        elif route == "BANK" and "sector" not in _promoted:
            md.append("- *Sector multiples chỉ hiển thị tham khảo, không vào composite ngân hàng.*")
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
