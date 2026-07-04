"""
vn_risk_metrics.py
==================
Risk metrics deterministic cho cổ phiếu VN (Task 10 / R1).

Nguyên tắc "Python tính — LLM diễn giải": tính beta, VaR, max drawdown,
volatility, ADTV, days-to-liquidate từ price history; inject vào payload. Ba
risk debator (aggressive/conservative/neutral) chỉ DIỄN GIẢI con số thật,
KHÔNG tự nghĩ ra mức rủi ro.

Verify tắt-LLM: gọi build_risk_metrics_block("VCB") là ra số.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

# Reuse fetch giá daily đã có retry của technical fetcher (single source).
from tradingagents.agents.utils.vn_technical_fetcher import _safe_hist


_FALLBACK = {
    "var_confidence": 0.95,     # mức tin cậy VaR
    "var_horizon_days": 20,     # chân trời VaR (ngày giao dịch)
    "adtv_window": 30,          # cửa sổ tính ADTV (ngày)
    "drawdown_years": 3,        # cửa sổ max drawdown
    "position_size_vnd": 50e9,  # quy mô vị thế KDEF giả định (50 tỷ) — cập nhật theo quỹ
    "participation_rate": 0.20, # % ADTV có thể tham gia/ngày khi thanh lý
    "benchmark": "VNINDEX",
}


def _cfg() -> dict:
    try:
        from tradingagents.dataflows.config import get_config
        v = get_config().get("risk_metrics") or {}
    except Exception:
        v = {}
    out = dict(_FALLBACK)
    out.update({k: v[k] for k in v if k in _FALLBACK})
    return out


def _f(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _pctf(v, d=1):
    return f"{v*100:.{d}f}%" if isinstance(v, (int, float)) else "—"


def compute_risk_metrics(symbol: str, source: str = "VCI",
                         trade_date: str | None = None,
                         position_size_vnd: float | None = None) -> dict:
    """Tính risk metrics từ price history. Trả dict số thô (không markdown)."""
    cfg = _cfg()
    end = datetime.strptime(trade_date[:10], "%Y-%m-%d") if trade_date else datetime.today()
    start = end - timedelta(days=int(cfg["drawdown_years"] * 365 + 30))
    s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    px = _safe_hist(symbol, s, e, source)
    if px.empty or len(px) < 60:
        # thử KBS fallback
        px = _safe_hist(symbol, s, e, "KBS")
    if px.empty or len(px) < 60:
        return {"error": f"không đủ dữ liệu giá cho {symbol}"}

    close = px["close"].astype(float).reset_index(drop=True)
    vol = px["volume"].astype(float).reset_index(drop=True) if "volume" in px.columns else None
    ret = close.pct_change().dropna()

    # Volatility annualized
    daily_vol = float(ret.std())
    volatility = daily_vol * math.sqrt(252)

    # Historical VaR: percentile của daily returns, scale √horizon
    z = 100 * (1 - cfg["var_confidence"])   # 5 cho 95%
    var_1d = -float(ret.quantile(z / 100.0))          # loss dương
    var_h = var_1d * math.sqrt(cfg["var_horizon_days"])

    # Max drawdown
    cummax = close.cummax()
    dd = (close / cummax - 1.0)
    max_dd = float(dd.min())

    # Beta vs benchmark
    beta = None
    vni = _safe_hist(cfg["benchmark"], s, e, source)
    if not vni.empty:
        m = pd.merge(px[["time", "close"]], vni[["time", "close"]],
                     on="time", suffixes=("", "_b"))
        if len(m) > 30:
            rt = m["close"].pct_change().dropna()
            rb = m["close_b"].pct_change().dropna()
            rt, rb = rt.align(rb, join="inner")
            var_b = rb.var()
            if var_b and var_b > 0:
                beta = round(float(rt.cov(rb) / var_b), 2)

    # ADTV (giá trị giao dịch bình quân ngày, VND) — close nghìn đ × 1000 × volume
    adtv_vnd = None
    if vol is not None and len(close) >= cfg["adtv_window"]:
        val = (close * 1000.0 * vol).tail(cfg["adtv_window"])
        adtv_vnd = float(val.mean())

    # Days-to-liquidate với size KDEF
    pos = position_size_vnd if position_size_vnd is not None else cfg["position_size_vnd"]
    days_to_liq = None
    if adtv_vnd and adtv_vnd > 0:
        days_to_liq = pos / (adtv_vnd * cfg["participation_rate"])

    return {
        "symbol": symbol,
        "beta": beta,
        "volatility_ann": volatility,
        "var_1d": var_1d,
        "var_horizon": var_h,
        "var_horizon_days": cfg["var_horizon_days"],
        "var_confidence": cfg["var_confidence"],
        "max_drawdown": max_dd,
        "adtv_vnd": adtv_vnd,
        "position_size_vnd": pos,
        "participation_rate": cfg["participation_rate"],
        "days_to_liquidate": days_to_liq,
        "n_days": len(close),
        "error": None,
    }


def build_risk_metrics_block(symbol: str, source: str = "VCI",
                             trade_date: str | None = None,
                             position_size_vnd: float | None = None) -> dict:
    """Trả {block, data, error} — markdown tiêm vào context risk analysts."""
    d = compute_risk_metrics(symbol, source, trade_date, position_size_vnd)
    if d.get("error"):
        return {"block": "", "data": {}, "error": d["error"]}

    adtv_ty = (d["adtv_vnd"] / 1e9) if d["adtv_vnd"] else None
    pos_ty = d["position_size_vnd"] / 1e9
    conf = int(d["var_confidence"] * 100)

    md = [
        "", "---",
        "## 🛡️ RISK METRICS DETERMINISTIC (tính từ price history — chỉ diễn giải, KHÔNG tự nghĩ mức rủi ro)",
        f"- **Beta** (vs VN-Index): **{d['beta'] if d['beta'] is not None else '—'}** · "
        f"**Volatility** (annualized): **{_pctf(d['volatility_ann'])}**",
        f"- **Historical VaR {conf}%** ({d['var_horizon_days']} ngày): **{_pctf(d['var_horizon'])}** "
        f"(1 ngày: {_pctf(d['var_1d'])})",
        f"- **Max Drawdown** ({_cfg()['drawdown_years']} năm): **{_pctf(d['max_drawdown'])}**",
    ]
    if adtv_ty is not None:
        md.append(f"- **ADTV** (30 ngày): **{adtv_ty:,.1f} tỷ đ/ngày**")
    if d["days_to_liquidate"] is not None:
        liq = d["days_to_liquidate"]
        flag = " ⚠️ THANH KHOẢN MỎNG" if liq > 5 else ""
        md.append(
            f"- **Days-to-liquidate** (vị thế {pos_ty:,.0f} tỷ, participation "
            f"{_pctf(d['participation_rate'],0)} ADTV/ngày): **{liq:,.1f} ngày**{flag}"
        )
    md.append("---")
    return {"block": "\n".join(md), "data": d, "error": None}


if __name__ == "__main__":
    import sys, json
    sym = sys.argv[1] if len(sys.argv) > 1 else "VCB"
    r = build_risk_metrics_block(sym)
    if r.get("error"):
        print("ERROR:", r["error"])
    else:
        print(r["block"])
        print("\nDATA:", json.dumps({k: v for k, v in r["data"].items() if k != "error"}, ensure_ascii=False))
