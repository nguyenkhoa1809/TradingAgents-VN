"""Đọc holdings.csv, expose set of tickers + per-fund breakdown.

Default format CSV: ticker,fund,weight,shares
Sửa load() nếu format thực tế của KIS export khác.
"""
import csv
from pathlib import Path
from collections import defaultdict

HOLDINGS_CSV = Path(__file__).parent.parent / "data" / "holdings.csv"


def load():
    """Return dict: {ticker: [{fund, weight, shares}, ...]}"""
    if not HOLDINGS_CSV.exists():
        return {}

    holdings = defaultdict(list)
    with open(HOLDINGS_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row["ticker"].strip().upper()
            holdings[ticker].append({
                "fund": row["fund"].strip(),
                "weight": float(row["weight"]),
                "shares": int(row["shares"]),
            })
    return dict(holdings)


def current_tickers():
    """Set các mã đang nắm — dùng để filter trong summarize."""
    return set(load().keys())


if __name__ == "__main__":
    h = load()
    print(f"Holdings: {len(h)} mã across funds")
    for t, positions in sorted(h.items()):
        funds = ", ".join(f"{p['fund']} {p['weight']}%" for p in positions)
        print(f"  {t}: {funds}")
