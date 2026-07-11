"""
consistency.py — Self-consistency sampling cho pipeline (mode rating).

Phase I (analysts) + debate chạy MỘT lần → checkpoint bất biến; chuỗi quyết định
Research Manager → Risk Officer → Portfolio Manager được sample N lần độc lập từ
checkpoint. Module này chứa các HÀM THUẦN (pure) để tổng hợp N sample:

  - vote_rating         : vote đa số, tie-break THẬN TRỌNG
                          (Sell > Underweight > Hold > Overweight > Buy).
  - ev_band_index       : ánh xạ EV(%) → band index theo ev_rating_band cố định.
  - ev_range_straddles  : dải [min,max] EV có vắt qua ranh giới band không.
  - downgrade_conviction: hạ 1 bậc CAO→TRUNG BÌNH→THẤP.
  - aggregate_samples   : gộp N sample → final_rating / EV min-median-max /
                          consensus / conviction cuối / chỉ số sample median.
  - parse_ev_pct        : trích EV(%) từ text PM (best-effort).
  - parse_conviction_label / parse_rating : trích nhãn từ text.
  - build_consistency_table_md : bảng markdown chèn vào section PM.
"""

from __future__ import annotations

import re
import statistics

# Thứ tự THẬN TRỌNG → phóng khoáng. Tie-break lấy phần tử sớm nhất (thận trọng nhất).
_CONSERVATIVE_ORDER = ["Sell", "Underweight", "Hold", "Overweight", "Buy"]
_CANON = {r.upper(): r for r in _CONSERVATIVE_ORDER}
_CANON.update({"STRONG BUY": "Buy", "STRONG SELL": "Sell", "NEUTRAL": "Hold",
               "OVERWEIGHT": "Overweight", "UNDERWEIGHT": "Underweight"})

# Ranh giới band EV (%) — khớp ev_rating_band_text trong default_config:
# EV<−12 Sell | −12..−5 Underweight | −5..5 Hold | 5..12 Overweight | >12 Buy
_EV_BOUNDARIES = [-12.0, -5.0, 5.0, 12.0]

_CONVICTION_ORDER = ["CAO", "TRUNG BÌNH", "THẤP"]


def canon_rating(r):
    if not r:
        return None
    return _CANON.get(re.sub(r"\s+", " ", str(r).strip().upper()))


def vote_rating(ratings: list) -> str | None:
    """Vote đa số; đồng phiếu → rating thận trọng hơn (theo _CONSERVATIVE_ORDER)."""
    canon = [c for c in (canon_rating(r) for r in ratings) if c]
    if not canon:
        return None
    counts = {}
    for c in canon:
        counts[c] = counts.get(c, 0) + 1
    top = max(counts.values())
    winners = [r for r in _CONSERVATIVE_ORDER if counts.get(r, 0) == top]
    return winners[0]  # sớm nhất = thận trọng nhất


def ev_band_index(ev: float) -> int:
    """0=Sell .. 4=Buy theo ranh giới _EV_BOUNDARIES."""
    b = _EV_BOUNDARIES
    if ev < b[0]:
        return 0
    if ev < b[1]:
        return 1
    if ev < b[2]:
        return 2
    if ev < b[3]:
        return 3
    return 4


def ev_range_straddles(ev_min, ev_max) -> bool:
    """dải EV [min,max] có nằm ở 2 band khác nhau không."""
    if ev_min is None or ev_max is None:
        return False
    return ev_band_index(ev_min) != ev_band_index(ev_max)


def downgrade_conviction(label: str, do: bool) -> str:
    """Hạ 1 bậc nếu do=True: CAO→TRUNG BÌNH→THẤP (THẤP giữ nguyên)."""
    lab = re.sub(r"\s+", " ", (label or "").strip().upper())
    if lab not in _CONVICTION_ORDER:
        lab = "TRUNG BÌNH"  # mặc định thận trọng khi không rõ
    if not do:
        return lab
    i = _CONVICTION_ORDER.index(lab)
    return _CONVICTION_ORDER[min(i + 1, len(_CONVICTION_ORDER) - 1)]


def _median_index(evs: list) -> int:
    """Index của sample có EV median (odd → giữa; even → lower-middle).
    EV None coi như -inf để không được chọn khi có số thật."""
    order = sorted(range(len(evs)), key=lambda i: (evs[i] is None, evs[i] if evs[i] is not None else 0))
    return order[(len(order) - 1) // 2]


def aggregate_samples(samples: list) -> dict:
    """samples: list dict {rating, ev_pct, conviction}. Trả tổng hợp.

    conviction cuối HẠ một bậc so với conviction của sample median nếu consensus
    KHÔNG tuyệt đối HOẶC dải EV [min,max] vắt qua ranh giới band.
    """
    n = len(samples)
    ratings = [s.get("rating") for s in samples]
    final_rating = vote_rating(ratings)

    evs = [s.get("ev_pct") for s in samples]
    ev_nums = [e for e in evs if e is not None]
    ev_min = min(ev_nums) if ev_nums else None
    ev_med = statistics.median(ev_nums) if ev_nums else None
    ev_max = max(ev_nums) if ev_nums else None

    consensus_count = sum(1 for r in ratings if canon_rating(r) == final_rating)
    unanimous = consensus_count == n and n > 0
    straddle = ev_range_straddles(ev_min, ev_max)

    mid = _median_index(evs) if samples else 0
    median_conviction = samples[mid].get("conviction") if samples else None
    final_conviction = downgrade_conviction(median_conviction, do=(not unanimous or straddle))

    return {
        "n_samples": n,
        "final_rating": final_rating,
        "ev_min": ev_min, "ev_median": ev_med, "ev_max": ev_max,
        "consensus_count": consensus_count,
        "consensus": f"{consensus_count}/{n}" if n else "0/0",
        "unanimous": unanimous,
        "ev_straddles_band": straddle,
        "median_index": mid,
        "final_conviction": final_conviction,
        "conviction_downgraded": (not unanimous or straddle),
    }


# ── Parsers (best-effort) ────────────────────────────────────────────────────

_EV_EQ_RE = re.compile(r"EV[^\n=%]{0,80}?=\s*([+-]?\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_PCT_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*%")
_CONV_RE = re.compile(r"(CAO|TRUNG\s+BÌNH|THẤP)", re.IGNORECASE)
_RATING_RE = re.compile(
    r"\*\*Rating\*\*\s*:\s*(Strong\s+Buy|Strong\s+Sell|Buy|Overweight|Hold|Underweight|Sell|Neutral)",
    re.IGNORECASE,
)


def parse_ev_pct(text: str):
    """EV(%) từ text PM. Ưu tiên mẫu 'EV ... = X%' (kết quả cuối), fallback %."""
    if not text:
        return None
    matches = _EV_EQ_RE.findall(text)
    if matches:
        return float(matches[-1].replace(",", "."))
    m = _PCT_RE.search(text)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_conviction_label(text: str):
    if not text:
        return None
    m = _CONV_RE.search(text)
    return re.sub(r"\s+", " ", m.group(1).upper()) if m else None


def parse_rating(text: str):
    if not text:
        return None
    m = _RATING_RE.search(text)
    return canon_rating(m.group(1)) if m else None


def _extract_sample(st: dict) -> dict:
    """Trích {rating, ev_pct, conviction, ...} từ 1 state đã chạy xong PM."""
    txt = st.get("final_trade_decision", "") or ""
    return {
        "rating": st.get("pm_rating") or parse_rating(txt),
        "ev_pct": parse_ev_pct(txt),
        "conviction": parse_conviction_label(txt),
        "final_trade_decision": txt,
        "investment_plan": st.get("investment_plan", ""),
        "risk_review": st.get("risk_review", ""),
        "pm_reason": st.get("pm_reason"),
    }


def resample_decisions(base_state: dict, n: int, rm_node, ro_node, pm_node,
                       on_error=None) -> list:
    """Sample #1 = base_state (đã chạy full 1 lần). Sample #2..N: gọi TRỰC TIẾP
    RM→RO→PM trên copy nông của base_state — KHÔNG chạy lại Phase I/debate (chỉ đọc
    analyst reports + debate history sẵn có). Trả list sample dicts.

    Bất biến kiểm thử: với n mẫu, mỗi node quyết định được gọi đúng (n−1) lần;
    graph/Phase-I không được đụng tới ở đây.
    """
    samples = [_extract_sample(base_state)]
    for i in range(n - 1):
        st = dict(base_state)  # copy nông; node trả dict mới, không mutate nested
        try:
            st.update(rm_node(st))
            st.update(ro_node(st))
            st.update(pm_node(st))
            samples.append(_extract_sample(st))
        except Exception as e:  # noqa: BLE001
            if on_error:
                on_error(i + 2, e)
    return samples


def apply_final_to_pm_text(text: str, final_rating: str, final_conviction: str) -> str:
    """Ghi đè dòng **Rating** và **Conviction** của text PM (sample median) bằng
    kết quả tổng hợp vote — để header/banner/bảng pipeline đều nhất quán final_rating.
    Chỉ đánh dấu '(vote N-samples)' để minh bạch nội dung là median + rating vote."""
    out = text or ""
    if final_rating:
        out, n = re.subn(r"(\*\*Rating\*\*\s*:\s*)([^\n]+)",
                         rf"\g<1>{final_rating} (self-consistency vote)", out, count=1)
        if n == 0:
            out = f"**Rating**: {final_rating} (self-consistency vote)\n" + out
    if final_conviction:
        out = re.sub(r"(\*\*Conviction\*\*\s*:\s*)([^\n]+)",
                     rf"\g<1>{final_conviction} (tổng hợp self-consistency)", out, count=1)
    return out


# ── Render bảng self-consistency ─────────────────────────────────────────────

def build_consistency_table_md(samples: list, agg: dict) -> str:
    """Bảng markdown 'Self-Consistency (N samples)' + dòng tổng hợp."""
    n = agg.get("n_samples", len(samples))
    lines = [
        "", "---",
        f"### 🎲 Self-Consistency ({n} samples)",
        "| Sample | Rating | EV | Conviction |",
        "|--------|--------|-----|------------|",
    ]
    for i, s in enumerate(samples, 1):
        ev = s.get("ev_pct")
        ev_s = f"{ev:+.1f}%" if ev is not None else "—"
        star = " (median)" if (i - 1) == agg.get("median_index") else ""
        lines.append(
            f"| #{i}{star} | {s.get('rating') or '—'} | {ev_s} | {s.get('conviction') or '—'} |"
        )
    ev_min, ev_max = agg.get("ev_min"), agg.get("ev_max")
    ev_range = (f"[{ev_min:+.1f}%, {ev_max:+.1f}%]" if ev_min is not None else "—")
    lines += [
        "",
        f"- **Vote (final rating)**: **{agg.get('final_rating') or '—'}** "
        f"· consensus {agg.get('consensus')}",
        f"- **Dải EV** (min/median/max): {ev_range} "
        f"(median {agg['ev_median']:+.1f}%)" if agg.get("ev_median") is not None
        else f"- **Dải EV**: {ev_range}",
        f"- **Conviction cuối**: {agg.get('final_conviction')}"
        + (" (đã hạ 1 bậc — consensus chưa tuyệt đối hoặc EV vắt ranh giới band)"
           if agg.get("conviction_downgraded") else ""),
        "---",
    ]
    return "\n".join(lines)
