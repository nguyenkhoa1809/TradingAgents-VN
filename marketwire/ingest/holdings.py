"""Đọc coverage.csv, expose set of tickers đang cover + weight.

Format CSV: ticker,weight (weight dạng chuỗi phần trăm, vd "9.00%")
"""
import csv
from pathlib import Path

HOLDINGS_CSV = Path(__file__).parent.parent / "data" / "coverage.csv"


def load():
    """Return dict: {ticker: weight (float, %)}"""
    if not HOLDINGS_CSV.exists():
        return {}

    holdings = {}
    with open(HOLDINGS_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row["ticker"].strip().upper()
            holdings[ticker] = float(row["weight"].strip().rstrip("%"))
    return holdings


def current_tickers():
    """Set các mã đang cover — dùng để filter trong summarize."""
    return set(load().keys())


if __name__ == "__main__":
    h = load()
    print(f"Coverage: {len(h)} mã")
    for t, w in sorted(h.items(), key=lambda x: -x[1]):
        print(f"  {t}: {w}%")
