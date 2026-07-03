"""calibration_report.py — Accuracy report for TradingAgents-VN calibration data.

Usage:
    python calibration_report.py
    python calibration_report.py --ticker VCB
    python calibration_report.py --resolved-only
"""
import argparse
import sqlite3
from pathlib import Path

CALIBRATION_DB = Path.home() / ".tradingagents" / "calibration" / "calibration_store.db"

_MIN_SAMPLE = 30
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


def _load(ticker_filter: str | None, resolved_only: bool) -> list[dict]:
    if not CALIBRATION_DB.exists():
        return []
    con = sqlite3.connect(str(CALIBRATION_DB))
    con.row_factory = sqlite3.Row
    sql = "SELECT * FROM calibration_runs WHERE 1=1"
    params = []
    if ticker_filter:
        sql += " AND ticker = ?"
        params.append(ticker_filter.upper())
    if resolved_only:
        sql += " AND resolved_at IS NOT NULL"
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


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


def _summary(rows: list[dict]) -> None:
    print("\n" + "═"*60)
    print("  CALIBRATION REPORT — TradingAgents-VN")
    print("  DB:", CALIBRATION_DB)
    print("═"*60)

    _print_table("By Conviction", rows, "conviction")

    # EV bucket view
    for r in rows:
        r["_ev_bucket"] = _ev_bucket(r.get("ev_pct"))
    _print_table("By EV bucket", rows, "_ev_bucket")

    # Rating view
    _print_table("By Rating", rows, "rating")


def main() -> None:
    parser = argparse.ArgumentParser(description="TradingAgents-VN Calibration Report")
    parser.add_argument("--ticker",        help="Filter by ticker")
    parser.add_argument("--resolved-only", action="store_true", help="Only show resolved entries")
    args = parser.parse_args()

    if not CALIBRATION_DB.exists():
        print(f"No calibration DB found at {CALIBRATION_DB}")
        print("Run `python backtest.py` first to collect data.")
        return

    rows = _load(args.ticker, args.resolved_only)
    if not rows:
        print("No records found.")
        return

    _summary(rows)


if __name__ == "__main__":
    main()
