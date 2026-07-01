"""
vn_technical_fetcher.py
-----------------------
Fetch + tính sẵn dữ liệu phân tích kỹ thuật (BSR-style) cho 1 mã VN:
  - Giá 52 tuần (weekly resample) + Volume + MA20
  - RSI(14), MACD(12,26,9) histogram
  - Tương quan vs VNINDEX & VN30 (chuẩn hoá 100)
  - Technical score (-6..+6) + tín hiệu, beta, 1Y performance

Trả về:
  summary_md  : markdown ngắn để chèn vào prompt LLM (tùy chọn)
  chart_json  : JSON string nhúng vào market_report cho render_report.py
  error       : str | None

Dùng:
  ctx = fetch_vn_technical_context("VHM")
  report += f"\\n<!-- VN_TECH_DATA {ctx['chart_json']} -->"
"""

import json
import time
from datetime import datetime, timedelta

import pandas as pd


def _safe_hist(symbol: str, start: str, end: str, source: str = "VCI", retries: int = 2):
    """Fetch daily history với retry (VCI hay lỗi ConnectionError thoáng qua)."""
    from vnstock_data import Quote
    for i in range(retries + 1):
        try:
            df = Quote(symbol=symbol, source=source).history(start=start, end=end, interval="1D")
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.copy()
            df["time"] = pd.to_datetime(df["time"])
            return df.sort_values("time").reset_index(drop=True)
        except Exception:
            if i < retries:
                time.sleep(1.0)
    return pd.DataFrame()


def _weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily → weekly (W-FRI): close=last, volume=sum."""
    if df.empty:
        return df
    w = df.set_index("time").resample("W-FRI").agg(
        {"close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    return w


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - 100 / (1 + rs)).fillna(50)


def _macd(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal, (macd - signal)


def _r2(v):
    return None if v is None or pd.isna(v) else round(float(v), 2)


def fetch_vn_technical_context(symbol: str, source: str = "VCI", n_weeks: int = 52) -> dict:
    empty = {"summary_md": "", "chart_json": "", "error": None}
    try:
        import vnstock_data  # noqa: F401
    except ImportError:
        empty["error"] = "vnstock_data not available"
        return empty

    try:
        end = datetime.today()
        start = end - timedelta(days=520)  # ~1.4 năm để đủ MA50 tuần + beta
        s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

        # Thử source ưu tiên, fallback sang KBS nếu VCI lỗi/thiếu data
        sources = [source] + [x for x in ("VCI", "KBS") if x != source]
        px = pd.DataFrame()
        used_source = source
        for src in sources:
            px = _safe_hist(symbol, s, e, src)
            if not px.empty and len(px) >= 60:
                used_source = src
                break
        if px.empty or len(px) < 60:
            empty["error"] = "không đủ dữ liệu giá (đã thử " + "/".join(sources) + ")"
            return empty

        vni = _safe_hist("VNINDEX", s, e, used_source)
        vn30 = _safe_hist("VN30", s, e, used_source)

        # ── Weekly series ──────────────────────────────────────────────
        w = _weekly(px)
        close_w = w["close"]
        ma10 = close_w.rolling(10).mean()
        ma20 = close_w.rolling(20).mean()
        ma50 = close_w.rolling(50).mean()
        rsi_w = _rsi(close_w)
        _, _, macd_hist = _macd(close_w)

        # cắt 52 tuần cuối
        tail = w.tail(n_weeks)
        idx = tail.index
        weeks = [d.strftime("%Y-%m-%d") for d in idx]

        def arr(series):
            return [(_r2(series.get(d)) if d in series.index else None) for d in idx]

        close_arr = arr(close_w)
        vol_arr = [(_r2(w["volume"].get(d) / 1e6) if d in w.index else None) for d in idx]  # triệu CP
        ma10_arr = arr(ma10)
        ma20_arr = arr(ma20)
        ma50_arr = arr(ma50)
        rsi_arr = arr(rsi_w)
        macdh_arr = arr(macd_hist)

        # volume up/down theo tuần (close vs tuần trước)
        vol_up = []
        prev = None
        for d in idx:
            c = close_w.get(d)
            vol_up.append(bool(prev is not None and c is not None and c >= prev))
            prev = c

        # ── Tương quan vs index (chuẩn hoá 100 trong cửa sổ 52 tuần) ────
        def norm_idx(dfi):
            if dfi.empty:
                return []
            wi = _weekly(dfi)["close"].reindex(idx).ffill()
            base = wi.dropna().iloc[0] if not wi.dropna().empty else None
            if not base:
                return []
            return [(_r2(v / base * 100) if pd.notna(v) else None) for v in wi]

        tkr_norm = norm_idx(px)
        vni_norm = norm_idx(vni)
        vn30_norm = norm_idx(vn30)

        # ── Metrics ────────────────────────────────────────────────────
        last_close = float(close_w.iloc[-1])
        peak = float(close_w.max())
        rsi_last = float(rsi_w.iloc[-1])
        macdh_last = float(macd_hist.iloc[-1])
        ma10_l, ma20_l, ma50_l = ma10.iloc[-1], ma20.iloc[-1], ma50.iloc[-1]

        # 1Y performance (ticker vs VNI) trên daily
        def perf(dfd):
            if dfd.empty or len(dfd) < 2:
                return None
            cc = dfd["close"]
            return round((cc.iloc[-1] / cc.iloc[0] - 1) * 100, 1)
        perf_1y = perf(px)
        perf_vni = perf(vni)

        # Beta + alpha (daily returns, align theo ngày)
        beta = alpha = None
        if not vni.empty:
            m = pd.merge(px[["time", "close"]], vni[["time", "close"]],
                         on="time", suffixes=("", "_vni"))
            if len(m) > 30:
                rt = m["close"].pct_change().dropna()
                rv = m["close_vni"].pct_change().dropna()
                rt, rv = rt.align(rv, join="inner")
                var = rv.var()
                if var and var > 0:
                    beta = round(float(rt.cov(rv) / var), 2)
                # Alpha BETA-ADJUSTED (A6): alpha = r_stock − beta × r_index.
                # Không phải hiệu suất tương đối thuần (đó là 'relative return').
                if perf_1y is not None and perf_vni is not None and beta is not None:
                    alpha = round(perf_1y - beta * perf_vni, 0)

        # ── Technical score (-6..+6) ───────────────────────────────────
        sig = []
        def chk(cond):
            sig.append(1 if cond else -1)
        chk(ma10_l is not None and last_close > ma10_l)
        chk(ma20_l is not None and last_close > ma20_l)
        chk(ma50_l is not None and pd.notna(ma50_l) and last_close > ma50_l)
        chk(ma10_l is not None and ma20_l is not None and ma10_l > ma20_l)
        chk(rsi_last > 50)
        chk(macdh_last > 0)
        score = sum(sig)

        if score >= 4:
            signal, scolor = "STRONG BUY", "#22c55e"
        elif score >= 2:
            signal, scolor = "BUY", "#22c55e"
        elif score >= -1:
            signal, scolor = "NEUTRAL", "#f59e0b"
        elif score >= -3:
            signal, scolor = "SELL", "#f43f5e"
        else:
            signal, scolor = "STRONG SELL", "#f43f5e"

        rsi_label = "QUÁ MUA" if rsi_last >= 70 else "QUÁ BÁN" if rsi_last <= 30 else \
                    "TÍCH CỰC" if rsi_last > 50 else "TIÊU CỰC"
        macd_trend = "TÍCH CỰC" if macdh_last > 0 else "TIÊU CỰC"

        below_all = all(
            last_close < (x if x is not None and pd.notna(x) else 1e18)
            for x in [ma10_l, ma20_l, ma50_l]
        )
        note = (f"Giá {last_close:,.1f} nghìn đ" +
                (f" — dưới tất cả MA10/20/50" if below_all else "") +
                (f" · đỉnh 52T {peak:,.1f}" if peak > last_close else ""))

        chart_data = {
            "symbol": symbol,
            "score": score, "score_max": 6, "signal": signal, "signal_color": scolor,
            "rsi": round(rsi_last, 1), "rsi_label": rsi_label,
            "macd_trend": macd_trend, "macd_hist": round(macdh_last, 2),
            "perf_1y": perf_1y, "perf_vni": perf_vni,
            "beta": beta, "alpha": alpha,
            "note": note,
            "weeks": weeks,
            "close": close_arr, "ma10": ma10_arr, "ma20": ma20_arr, "ma50": ma50_arr,
            "volume": vol_arr, "vol_up": vol_up,
            "rsi_series": rsi_arr, "macd_hist_series": macdh_arr,
            "tkr_norm": tkr_norm, "vni_norm": vni_norm, "vn30_norm": vn30_norm,
            "range": f"{weeks[0]} → {weeks[-1]}" if weeks else "",
        }

        md = [
            "---",
            f"## 📈 KỸ THUẬT PRE-LOADED: {symbol} (52 tuần)",
            f"- Điểm KT: {score}/6 · **{signal}** · RSI {rsi_last:.1f} ({rsi_label}) · MACD {macd_trend}",
            f"- 1Y: {perf_1y}% (VNI {perf_vni}%) · Beta {beta} · Alpha {alpha}",
            "---",
        ]

        return {"summary_md": "\n".join(md), "chart_json": json.dumps(chart_data), "error": None}

    except Exception as ex:
        return {"summary_md": "", "chart_json": "", "error": str(ex)}


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "VHM"
    r = fetch_vn_technical_context(sym)
    if r["error"]:
        print("ERROR:", r["error"])
    else:
        d = json.loads(r["chart_json"])
        print(f"{sym}: score {d['score']}/6 {d['signal']} | RSI {d['rsi']} {d['rsi_label']} | "
              f"MACD {d['macd_trend']} | 1Y {d['perf_1y']}% vs VNI {d['perf_vni']}% | "
              f"beta {d['beta']} alpha {d['alpha']} | weeks {len(d['weeks'])}")
        print("note:", d["note"])
