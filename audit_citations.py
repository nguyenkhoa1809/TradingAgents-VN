"""Audit existing HTML reports for ungrounded citations.

Scans all HTML files under reports/ and flags any mention of CTCK names
or analyst patterns.  Since original context is unavailable for old reports,
every match is flagged as "possibly ungrounded" for manual review.

Usage:
    python audit_citations.py
Output:
    ungrounded_citations_audit.md
"""

import re
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from tradingagents.agents.utils.citation_validator import scan_text_for_citations

REPORTS_DIR = Path(__file__).parent / "reports"
OUTPUT_FILE = Path(__file__).parent / "ungrounded_citations_audit.md"

# Strip HTML tags
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s{3,}")


def strip_html(html: str) -> str:
    text = _HTML_TAG.sub(" ", html)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def extract_ticker_from_path(path: Path) -> str:
    # reports/VCB/VCB_2026-...html  → "VCB"
    parts = path.parts
    try:
        reports_idx = next(i for i, p in enumerate(parts) if p == "reports")
        return parts[reports_idx + 1].upper()
    except (StopIteration, IndexError):
        return ""


def main():
    html_files = sorted(REPORTS_DIR.rglob("*.html"))
    # Exclude vendor/demo files
    html_files = [
        f for f in html_files
        if "site-packages" not in str(f) and "demo_report" not in str(f)
        and "AGENT_GUIDE" not in f.name
    ]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_hits = 0
    report_lines = [
        f"# Ungrounded Citations Audit",
        f"",
        f"Generated: {now}  ",
        f"Reports scanned: {len(html_files)}  ",
        f"",
        f"> **Note**: Context unavailable for existing reports — every match is",
        f"> flagged as *possibly* ungrounded. Verify manually before taking action.",
        f"",
        f"---",
        f"",
    ]

    per_report = {}
    for html_path in html_files:
        ticker = extract_ticker_from_path(html_path)
        try:
            raw = html_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            report_lines.append(f"⚠️ Could not read `{html_path.name}`: {e}\n")
            continue

        text = strip_html(raw)
        hits = scan_text_for_citations(text, ticker)
        if hits:
            per_report[html_path] = (ticker, hits)
            total_hits += len(hits)

    # ── Summary table ─────────────────────────────────────────────────────────
    report_lines.append(f"## Summary — {total_hits} possible citations across {len(per_report)} reports\n")
    report_lines.append("| Report | Ticker | Hits |")
    report_lines.append("|--------|--------|------|")
    for path, (ticker, hits) in sorted(per_report.items(), key=lambda x: -len(x[1][1])):
        report_lines.append(f"| {path.name} | {ticker} | {len(hits)} |")
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")

    # ── Per-report detail ─────────────────────────────────────────────────────
    report_lines.append("## Detail\n")
    for path, (ticker, hits) in sorted(per_report.items()):
        report_lines.append(f"### `{path.name}` (ticker: {ticker})\n")
        for h in hits:
            report_lines.append(f"- {h}")
        report_lines.append("")

    output = "\n".join(report_lines)
    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"Audit complete: {total_hits} hits in {len(per_report)}/{len(html_files)} reports.")
    print(f"Output -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
