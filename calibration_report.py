"""calibration_report.py — Accuracy report for TradingAgents-VN calibration data.

Usage:
    python calibration_report.py
    python calibration_report.py --ticker VCB
    python calibration_report.py --resolved-only

Reads and combines every calibration_store_<hostname>.db found in the
calibration directory (one per machine — see backtest.py), so running this
from any machine shows accuracy across all of them.
"""
import argparse
import os
import sqlite3
from pathlib import Path

CALIBRATION_DIR = Path(os.getenv(
    "TRADINGAGENTS_CALIBRATION_DIR", str(Path.home() / ".tradingagents" / "calibration")
)).expanduser()

_MIN_SAMPLE = 30
# Duplicated from backtest.py — kept as plain constants here rather than
# importing that module, to keep the two CLI entry points independent.
_BULLISH = {"BUY", "STRONG BUY", "OVERWEIGHT"}
_BEARISH = {"SELL", "STRONG SELL", "UNDERWEIGHT"}
_EV_BINS = [
    (float("-inf"), -10, "EV < -10%"),
    (-10,  -5, "-10% ≤ EV < -5%"),
    ( -5,   0,  "-5% ≤ EV <  0%"),
    (  0,   5,   "0% ≤ EV <  5%"),
    (  5,  10,   "5% ≤ EV < 10%"),
    ( 10, float("inf"), "EV ≥ 10%"),
]


def _ev_bucket(ev_pct) -> str:
    if ev_pct is None:
        return "EV unknown"
    for lo, hi, label in _EV_BINS:
        if lo <= ev_pct < hi:
            return label
    return "EV ≥ 10%"


def _calibration_db_paths() -> list[Path]:
    """Every per-machine calibration DB found in the calibration directory."""
    if not CALIBRATION_DIR.exists():
        return []
    return sorted(CALIBRATION_DIR.glob("calibration_store_*.db"))


def _load(ticker_filter: str | None, resolved_only: bool) -> list[dict]:
    sql = "SELECT * FROM calibration_runs WHERE 1=1"
    params = []
    if ticker_filter:
        sql += " AND ticker = ?"
        params.append(ticker_filter.upper())
    if resolved_only:
        sql += " AND resolved_at IS NOT NULL"

    rows: list[dict] = []
    for db_path in _calibration_db_paths():
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        for r in con.execute(sql, params).fetchall():
            d = dict(r)
            d["_source_host"] = db_path.stem.removeprefix("calibration_store_")
            rows.append(d)
        con.close()
    return rows


def _load_transitions(ticker_filter: str | None) -> list[dict]:
    sql = "SELECT * FROM signal_transitions WHERE 1=1"
    params = []
    if ticker_filter:
        sql += " AND ticker = ?"
        params.append(ticker_filter.upper())

    rows: list[dict] = []
    for db_path in _calibration_db_paths():
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        try:
            for r in con.execute(sql, params).fetchall():
                d = dict(r)
                d["_source_host"] = db_path.stem.removeprefix("calibration_store_")
                rows.append(d)
        except sqlite3.OperationalError:
            pass  # older DB file predates the signal_transitions table
        finally:
            con.close()
    return rows


def _transition_win(row: dict) -> int | None:
    """1 if the new signal's direction played out correctly by exit, 0 if not,
    None if unresolved or the new rating has no directional expectation (e.g. HOLD)."""
    if row["exit_date"] is None or row["return_pct"] is None:
        return None
    rating = (row["new_rating"] or "").upper()
    if rating in _BULLISH:
        return 1 if row["return_pct"] > 0 else 0
    if rating in _BEARISH:
        return 1 if row["return_pct"] < 0 else 0
    return None


def _print_transition_report(rows: list[dict]) -> None:
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["prev_rating"] or "—", r["new_rating"] or "—")].append(r)

    print(f"\n{'═'*60}")
    print("  Signal Transition Accuracy")
    print(f"{'═'*60}")

    for prev, new in sorted(groups):
        grp     = groups[(prev, new)]
        closed  = [r for r in grp if r["exit_date"]]
        scored  = [r for r in closed if _transition_win(r) is not None]
        wins    = [r for r in scored if _transition_win(r) == 1]
        avg_ret = (
            sum(r["return_pct"] for r in closed if r["return_pct"] is not None) / len(closed)
            if closed else None
        )
        avg_hold = (
            sum(r["holding_days"] for r in closed if r["holding_days"] is not None) / len(closed)
            if closed else None
        )
        win_str  = f"win rate {len(wins)/len(scored)*100:.0f}%" if scored else "win rate —"
        ret_str  = f"avg return {avg_ret:+.1f}%" if avg_ret is not None else "avg return —"
        hold_str = f"avg holding {avg_hold:.0f} days" if avg_hold is not None else "avg holding —"
        label = f"{prev} → {new}"
        print(f"  {label:<22}: {len(grp)} transitions ({len(closed)} closed), {win_str}, {ret_str}, {hold_str}")

    high_ev = [
        r for r in rows
        if (r.get("conviction") or "").upper() in ("CAO", "HIGH")
        and r.get("ev_pct") is not None and r["ev_pct"] > 10
        and r["exit_date"]
    ]
    scored_hi = [r for r in high_ev if _transition_win(r) is not None]
    if scored_hi:
        wins_hi = [r for r in scored_hi if _transition_win(r) == 1]
        print(f"\n  Top performing: Conviction HIGH, EV > 10% → win rate "
              f"{len(wins_hi)/len(scored_hi)*100:.0f}% (n={len(scored_hi)})")
    print()


def _print_table(title: str, rows: list[dict], group_key: str) -> None:
    from collections import defaultdict
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r.get(group_key) or "—"].append(r)

    resolved = [r for r in rows if r["resolved_at"]]
    pending  = len(rows) - len(resolved)

    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"  Total: {len(rows)}  |  Resolved: {len(resolved)}  |  Pending: {pending}")
    if len(resolved) < _MIN_SAMPLE:
        print(f"  ⚠  Sample < {_MIN_SAMPLE} — accuracy estimates unreliable")
    print(f"{'─'*60}")
    print(f"  {'Group':<22}  {'N':>4}  {'Resolved':>8}  {'Correct':>7}  {'Accuracy':>8}  {'Avg return':>10}")
    print(f"  {'─'*22}  {'─'*4}  {'─'*8}  {'─'*7}  {'─'*8}  {'─'*10}")

    for key in sorted(groups):
        grp    = groups[key]
        res    = [r for r in grp if r["resolved_at"]]
        correct = [r for r in res if r["direction_correct"] == 1]
        avg_ret = (
            sum(r["actual_return_pct"] for r in res if r["actual_return_pct"] is not None)
            / len(res) if res else None
        )
        acc_str = f"{len(correct)/len(res)*100:.0f}%" if res else "—"
        ret_str = f"{avg_ret:+.1f}%" if avg_ret is not None else "—"
        sample_warn = " ⚠" if res and len(res) < _MIN_SAMPLE else ""
        print(f"  {key:<22}  {len(grp):>4}  {len(res):>8}  {len(correct):>7}  {acc_str:>8}  {ret_str:>10}{sample_warn}")

    print()


def _summary(rows: list[dict], ticker_filter: str | None = None) -> None:
    hosts = sorted({r["_source_host"] for r in rows})
    print("\n" + "═"*60)
    print("  CALIBRATION REPORT — TradingAgents-VN")
    print("  Dir:", CALIBRATION_DIR)
    print("  Machines:", ", ".join(hosts) if hosts else "—")
    print("═"*60)

    _print_table("By Conviction", rows, "conviction")

    # EV bucket view
    for r in rows:
        r["_ev_bucket"] = _ev_bucket(r.get("ev_pct"))
    _print_table("By EV bucket", rows, "_ev_bucket")

    # Rating view
    _print_table("By Rating", rows, "rating")

    transitions = _load_transitions(ticker_filter)
    if transitions:
        _print_transition_report(transitions)


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingAgents-VN Calibration Report")
    parser.add_argument("--ticker",        help="Filter by ticker")
    parser.add_argument("--resolved-only", action="store_true", help="Only show resolved entries")
    args = parser.parse_args()

    if not _calibration_db_paths():
        print(f"No calibration DB found under {CALIBRATION_DIR}")
        print("Run `python backtest.py` first to collect data.")
        return

    rows = _load(args.ticker, args.resolved_only)
    if not rows:
        print("No records found.")
        return

    _summary(rows, args.ticker)


if __name__ == "__main__":
    main()
