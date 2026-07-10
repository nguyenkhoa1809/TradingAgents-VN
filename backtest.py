"""backtest.py — TradingAgents-VN Backtest Mode

Usage:
    python backtest.py --ticker VCB --date 2026-05-01 --provider deepseek-pro
    python backtest.py --ticker HPG --date-range 2026-05-01:2026-05-31 --provider deepseek

Results are written to ~/.tradingagents/calibration/calibration_store_<hostname>.db
— one file per machine (env TRADINGAGENTS_CALIBRATION_DIR to point this at a
shared folder, e.g. OneDrive; each machine still only ever writes its own
file, so cloud sync stays safe). Run calibration_report.py to see combined
accuracy across every machine's file.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import logging
import os
import re
import socket
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

CALIBRATION_DIR = Path(os.getenv(
    "TRADINGAGENTS_CALIBRATION_DIR", str(Path.home() / ".tradingagents" / "calibration")
)).expanduser()
_SAFE_HOST = re.sub(
    r"[^A-Za-z0-9_-]+", "_",
    os.getenv("TRADINGAGENTS_CALIBRATION_HOSTNAME") or socket.gethostname(),
).strip("_") or "machine"
# Per-machine file — the ONLY file this process ever writes to. Safe under
# OneDrive/cloud sync because each device owns a distinct file (see memory.py
# for the same pattern applied to trading_memory.md).
CALIBRATION_DB = CALIBRATION_DIR / f"calibration_store_{_SAFE_HOST}.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calibration_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT    NOT NULL,
    ticker           TEXT    NOT NULL,
    rating           TEXT,
    ev_pct           REAL,
    conviction       TEXT,
    bull_prob        REAL,
    base_prob        REAL,
    bear_prob        REAL,
    entry_price      REAL,
    direction_correct INTEGER,
    actual_return_pct REAL,
    resolved_at      TEXT,
    pipeline_version TEXT
)
"""

_TRANSITIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signal_transitions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker           TEXT    NOT NULL,
    transition_date  TEXT    NOT NULL,
    prev_rating      TEXT,
    new_rating       TEXT,
    entry_price      REAL,
    exit_date        TEXT,
    exit_price       REAL,
    exit_rating      TEXT,
    holding_days     INTEGER,
    return_pct       REAL,
    ev_pct           REAL,
    conviction       TEXT,
    pipeline_version TEXT
)
"""

_BULLISH = {"BUY", "STRONG BUY", "OVERWEIGHT"}
_BEARISH = {"SELL", "STRONG SELL", "UNDERWEIGHT"}

_PROVIDER_PRESETS = {
    "claude": {
        "llm_provider":    "anthropic",
        "deep_think_llm":  "claude-sonnet-4-6",
        "quick_think_llm": "claude-haiku-4-5-20251001",
    },
    "deepseek": {
        "llm_provider":    "deepseek",
        "deep_think_llm":  "deepseek-v4-flash",
        "quick_think_llm": "deepseek-v4-flash",
    },
    "deepseek-pro": {
        "llm_provider":    "deepseek",
        "deep_think_llm":  "deepseek-v4-pro",
        "quick_think_llm": "deepseek-v4-flash",
    },
    "openai": {
        "llm_provider":    "openai",
        "deep_think_llm":  "gpt-5.5",
        "quick_think_llm": "gpt-5.4-mini",
    },
    "openrouter": {
        "llm_provider":    "openrouter",
        "deep_think_llm":  "google/gemini-2.5-pro",
        "quick_think_llm": "google/gemini-2.5-flash",
    },
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _init_db() -> sqlite3.Connection:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(CALIBRATION_DB))
    con.execute(_SCHEMA_SQL)
    con.execute(_TRANSITIONS_SCHEMA_SQL)
    con.commit()
    return con


def _save_record(con: sqlite3.Connection, date: str, ticker: str, fields: dict, version: str) -> None:
    con.execute(
        """INSERT INTO calibration_runs
               (date, ticker, rating, ev_pct, conviction,
                bull_prob, base_prob, bear_prob, entry_price, pipeline_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            date, ticker,
            fields["rating"], fields["ev_pct"], fields["conviction"],
            fields["bull_prob"], fields["base_prob"], fields["bear_prob"],
            fields["entry_price"], version,
        ),
    )
    con.commit()


# ── Signal transitions ────────────────────────────────────────────────────────
# Tracks entry/exit whenever a ticker's rating changes between two runs — the
# unit that actually matters to a PM: "khi pipeline đổi Hold→Buy, xác suất giá
# đi đúng hướng là bao nhiêu?". Reads are cross-machine (two machines run on
# alternating days, so comparing only the local file would wrongly treat every
# first run on a machine as "no history"); writes stay local — except closing
# an open transition, which may land in another machine's file, the same
# single-alternating-writer pattern TradingMemoryLog uses to resolve pending
# entries (tradingagents/agents/utils/memory.py).

def _calibration_db_paths() -> list:
    """Every per-machine calibration DB found in the calibration directory."""
    if not CALIBRATION_DIR.exists():
        return []
    return sorted(CALIBRATION_DIR.glob("calibration_store_*.db"))


def _latest_cross_machine_rating(ticker: str, before_date: str):
    """(date, rating) of the most recent calibration_runs row for `ticker`
    with date < before_date, scanned across every per-machine DB. None if
    this ticker has no history yet on any machine before before_date."""
    best = None
    for db_path in _calibration_db_paths():
        con2 = sqlite3.connect(str(db_path))
        try:
            row = con2.execute(
                "SELECT date, rating FROM calibration_runs "
                "WHERE ticker = ? AND date < ? ORDER BY date DESC LIMIT 1",
                (ticker, before_date),
            ).fetchone()
        finally:
            con2.close()
        if row and (best is None or row[0] > best[0]):
            best = row
    return best


def _find_open_transition(ticker: str):
    """(db_path, row dict) of the most recent OPEN signal_transitions row for
    `ticker` across every per-machine DB, or None if there is no open position."""
    best = None
    for db_path in _calibration_db_paths():
        con2 = sqlite3.connect(str(db_path))
        con2.row_factory = sqlite3.Row
        try:
            row = con2.execute(
                "SELECT * FROM signal_transitions WHERE ticker = ? AND exit_date IS NULL "
                "ORDER BY transition_date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None  # older DB file predates the signal_transitions table
        finally:
            con2.close()
        if row and (best is None or row["transition_date"] > best[1]["transition_date"]):
            best = (db_path, dict(row))
    return best


def _open_transition(con: sqlite3.Connection, ticker: str, date: str,
                      prev_rating: str, new_rating: str, fields: dict, version: str) -> None:
    con.execute(
        """INSERT INTO signal_transitions
               (ticker, transition_date, prev_rating, new_rating, entry_price,
                ev_pct, conviction, pipeline_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticker, date, prev_rating, new_rating, fields["entry_price"],
         fields["ev_pct"], fields["conviction"], version),
    )
    con.commit()


def _close_open_transition(con: sqlite3.Connection, ticker: str, exit_date: str,
                            exit_price, exit_rating: str) -> None:
    """Close the ticker's open transition (if any), wherever it lives."""
    found = _find_open_transition(ticker)
    if found is None:
        return  # no open position anywhere yet (e.g. this is the very first transition)
    db_path, row = found

    holding_days = (
        datetime.strptime(exit_date, "%Y-%m-%d")
        - datetime.strptime(row["transition_date"], "%Y-%m-%d")
    ).days
    return_pct = (
        round((exit_price / row["entry_price"] - 1) * 100, 4)
        if row["entry_price"] and exit_price else None
    )

    target = con if db_path == CALIBRATION_DB else sqlite3.connect(str(db_path))
    try:
        target.execute(
            "UPDATE signal_transitions SET exit_date=?, exit_price=?, exit_rating=?, "
            "holding_days=?, return_pct=? WHERE id=?",
            (exit_date, exit_price, exit_rating, holding_days, return_pct, row["id"]),
        )
        target.commit()
    finally:
        if target is not con:
            target.close()


def _apply_transition(con: sqlite3.Connection, ticker: str, date: str,
                       prev, fields: dict, version: str) -> None:
    """Open/close signal_transitions when this run's rating differs from the
    ticker's last known rating anywhere (`prev`, from _latest_cross_machine_rating).
    `prev is None` means the ticker has no history yet on any machine — per
    spec, the first-ever run for a ticker is never treated as a transition."""
    if prev is None:
        return
    prev_rating = prev[1]
    new_rating = fields["rating"]
    if not new_rating or new_rating == prev_rating:
        return  # unchanged signal — calibration_runs already has the row, nothing else to do

    _close_open_transition(con, ticker, exit_date=date,
                            exit_price=fields["entry_price"], exit_rating=new_rating)
    _open_transition(con, ticker, date, prev_rating, new_rating, fields, version)


# ── Field extraction ──────────────────────────────────────────────────────────

def _extract_fields(final_state: dict) -> dict:
    """Parse calibration fields from pipeline final_state.

    EV and probability regexes are TEMPORARY — structured fields not yet
    promoted to PortfolioDecision schema (G2 still in progress).
    """
    text = final_state.get("final_trade_decision", "")

    # rating via detect_signal
    try:
        from render_report import detect_signal
        rating = detect_signal(text)[3]
    except Exception:
        rating = ""

    # EV %
    ev_m = re.search(r'Expected\s+Value[^:=]*[:=]\s*([+-]?\d+(?:[.,]\d+)?)\s*%', text, re.IGNORECASE)
    ev_pct = float(ev_m.group(1).replace(",", ".")) if ev_m else None

    # Conviction
    conv_m = re.search(r'\*\*Conviction\*\*\s*:\s*(CAO|TRUNG\s+BÌNH|THẤP)', text, re.IGNORECASE)
    conviction = re.sub(r'\s+', ' ', conv_m.group(1).upper()) if conv_m else ""

    # Bull / Base / Bear probabilities
    bull_m = re.search(r'Bull[^:=]*[:=]\s*(\d+(?:[.,]\d+)?)\s*%', text, re.IGNORECASE)
    base_m = re.search(r'Base[^:=]*[:=]\s*(\d+(?:[.,]\d+)?)\s*%', text, re.IGNORECASE)
    bear_m = re.search(r'Bear[^:=]*[:=]\s*(\d+(?:[.,]\d+)?)\s*%', text, re.IGNORECASE)
    bull_prob = float(bull_m.group(1).replace(",", ".")) if bull_m else None
    base_prob = float(base_m.group(1).replace(",", ".")) if base_m else None
    bear_prob = float(bear_m.group(1).replace(",", ".")) if bear_m else None

    # Entry price from financials_chart_json
    entry_price = None
    chart_json = final_state.get("financials_chart_json", "")
    if chart_json:
        try:
            fd = json.loads(chart_json)
            raw = fd.get("latest_price")
            if raw is not None:
                entry_price = float(raw)
        except Exception:
            pass

    return {
        "rating":     rating,
        "ev_pct":     ev_pct,
        "conviction": conviction,
        "bull_prob":  bull_prob,
        "base_prob":  base_prob,
        "bear_prob":  bear_prob,
        "entry_price": entry_price,
    }


# ── Outcome resolution ────────────────────────────────────────────────────────

def resolve_outcomes(con: sqlite3.Connection) -> int:
    """Fetch actual prices for entries ≥30 days old and unresolved; return count resolved.

    Also force-closes LOCAL open signal_transitions past the same 30-day window
    that no new signal ever came along to close (e.g. ticker stopped being
    analyzed) — see _resolve_stale_open_transitions.
    """
    cutoff = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = con.execute(
        "SELECT id, date, ticker, entry_price, rating FROM calibration_runs "
        "WHERE resolved_at IS NULL AND date <= ?",
        (cutoff,),
    ).fetchall()

    resolved = 0
    if rows:
        try:
            from vnstock_data import Quote
        except ImportError:
            logging.warning("vnstock_data not available — cannot resolve outcomes")
            Quote = None

        if Quote is not None:
            for row_id, trade_date, ticker, entry_price, rating in rows:
                try:
                    exit_date = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=31)).strftime("%Y-%m-%d")
                    exit_end  = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=45)).strftime("%Y-%m-%d")
                    q  = Quote(symbol=ticker, source="VCI")
                    px = q.history(start=exit_date, end=exit_end, interval="1D")
                    if px is None or px.empty:
                        continue
                    cc = next((c for c in px.columns if "close" in c.lower()), None)
                    if not cc:
                        continue
                    exit_price = float(px[cc].dropna().iloc[0])

                    # Use stored entry_price if available, else fetch from trade_date
                    if not entry_price:
                        entry_end = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                        px2 = q.history(start=trade_date, end=entry_end, interval="1D")
                        if px2 is None or px2.empty:
                            continue
                        entry_price = float(px2[cc].dropna().iloc[0])

                    actual_return = (exit_price / entry_price - 1) * 100
                    rating_upper  = (rating or "").upper()
                    if rating_upper in _BULLISH:
                        direction_correct = 1 if actual_return > 0 else 0
                    elif rating_upper in _BEARISH:
                        direction_correct = 1 if actual_return < 0 else 0
                    else:
                        direction_correct = None

                    con.execute(
                        "UPDATE calibration_runs SET direction_correct=?, actual_return_pct=?, resolved_at=? WHERE id=?",
                        (direction_correct, round(actual_return, 4), datetime.today().strftime("%Y-%m-%d"), row_id),
                    )
                    resolved += 1
                except Exception as e:
                    logging.warning("resolve_outcomes: failed for id=%s %s@%s: %s", row_id, ticker, trade_date, e)

            con.commit()

    resolved += _resolve_stale_open_transitions(con)
    return resolved


def _resolve_stale_open_transitions(con: sqlite3.Connection) -> int:
    """Force-close LOCAL open transitions ≥30 days old that no new signal ever
    closed. exit_rating stays NULL — this distinguishes a timeout-close from a
    real signal-triggered close (which always sets exit_rating)."""
    cutoff = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = con.execute(
        "SELECT id, ticker, transition_date, entry_price FROM signal_transitions "
        "WHERE exit_date IS NULL AND transition_date <= ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        return 0

    try:
        from vnstock_data import Quote
    except ImportError:
        logging.warning("vnstock_data not available — cannot resolve stale transitions")
        return 0

    resolved = 0
    today = datetime.today().strftime("%Y-%m-%d")
    for row_id, ticker, t_date, entry_price in rows:
        try:
            start = (datetime.strptime(t_date, "%Y-%m-%d") + timedelta(days=31)).strftime("%Y-%m-%d")
            end   = (datetime.strptime(t_date, "%Y-%m-%d") + timedelta(days=45)).strftime("%Y-%m-%d")
            q  = Quote(symbol=ticker, source="VCI")
            px = q.history(start=start, end=end, interval="1D")
            if px is None or px.empty:
                continue
            cc = next((c for c in px.columns if "close" in c.lower()), None)
            if not cc:
                continue
            exit_price = float(px[cc].dropna().iloc[0])
            holding_days = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(t_date, "%Y-%m-%d")).days
            return_pct = round((exit_price / entry_price - 1) * 100, 4) if entry_price else None

            con.execute(
                "UPDATE signal_transitions SET exit_date=?, exit_price=?, holding_days=?, return_pct=? WHERE id=?",
                (today, exit_price, holding_days, return_pct, row_id),
            )
            resolved += 1
        except Exception as e:
            logging.warning("resolve stale transition failed id=%s %s@%s: %s", row_id, ticker, t_date, e)

    con.commit()
    return resolved


# ── Pipeline version ──────────────────────────────────────────────────────────

def _pipeline_version() -> str:
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent),
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ── Date range parser ─────────────────────────────────────────────────────────

def _expand_date_range(date_range: str) -> list:
    parts = date_range.split(":")
    if len(parts) != 2:
        raise ValueError(f"--date-range must be YYYY-MM-DD:YYYY-MM-DD, got: {date_range!r}")
    start = datetime.strptime(parts[0].strip(), "%Y-%m-%d")
    end   = datetime.strptime(parts[1].strip(), "%Y-%m-%d")
    dates, cur = [], start
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


# ── Main backtest runner ──────────────────────────────────────────────────────

def run_backtest(ticker: str, dates: list, provider: str) -> None:
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.default_config import DEFAULT_CONFIG

    if provider not in _PROVIDER_PRESETS:
        raise ValueError(f"Unknown provider {provider!r}. Choose from: {list(_PROVIDER_PRESETS)}")

    config = DEFAULT_CONFIG.copy()
    config.update(_PROVIDER_PRESETS[provider])

    con      = _init_db()
    n_resolved = resolve_outcomes(con)
    if n_resolved:
        logging.info("Resolved %d pending outcome(s)", n_resolved)

    version = _pipeline_version()

    for date in dates:
        logging.info("Backtest %s @ %s", ticker, date)
        ta = TradingAgentsGraph(debug=False, config=config)
        try:
            final_state, signal = ta.propagate(ticker, date, run_type="backtest")
            fields = _extract_fields(final_state)
            _save_record(con, date, ticker, fields, version)
            prev = _latest_cross_machine_rating(ticker, date)
            _apply_transition(con, ticker, date, prev, fields, version)
            logging.info(
                "  saved — rating=%-12s  conviction=%-12s  ev_pct=%s",
                fields["rating"], fields["conviction"],
                f"{fields['ev_pct']:.1f}%" if fields["ev_pct"] is not None else "—",
            )
        except Exception as e:
            logging.error("FAILED %s @ %s: %s", ticker, date, e)

    con.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="TradingAgents-VN Backtest Mode")
    parser.add_argument("--ticker",   required=True, help="VN ticker, e.g. VCB")
    parser.add_argument("--provider", default="deepseek-pro",
                        help=f"LLM provider preset ({', '.join(_PROVIDER_PRESETS)})")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--date",       help="Single trade date YYYY-MM-DD")
    grp.add_argument("--date-range", help="Date range YYYY-MM-DD:YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    dates = [args.date] if args.date else _expand_date_range(args.date_range)
    run_backtest(args.ticker, dates, args.provider)

    print(f"\nCalibration DB: {CALIBRATION_DB}")
    print("Run `python calibration_report.py` to view accuracy.")


if __name__ == "__main__":
    main()
