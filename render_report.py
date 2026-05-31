"""
render_report.py
----------------
Converts TradingAgents markdown report files into a stunning, professional
HTML investment report and opens it in the default browser.

Usage:
    # Auto-detect the latest report:
    python render_report.py

    # Specify a report directory explicitly:
    python render_report.py --report-dir "reports/NVDA_20240510"

    # Specify a single complete_report.md file:
    python render_report.py --report-file "reports/NVDA_20240510/complete_report.md"

    # Render without opening browser:
    python render_report.py --no-open
"""

import argparse
import io
import os
import re
import sys
import webbrowser

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency: markdown → install if missing
# ---------------------------------------------------------------------------
try:
    import markdown
    from markdown.extensions.tables import TableExtension
    from markdown.extensions.fenced_code import FencedCodeExtension
except ImportError:
    print("[render_report] Installing 'markdown' package...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown"])
    import markdown
    from markdown.extensions.tables import TableExtension
    from markdown.extensions.fenced_code import FencedCodeExtension


# ---------------------------------------------------------------------------
# Section metadata
# ---------------------------------------------------------------------------
SECTION_META = {
    "market_report": {
        "title": "Market Analysis",
        "icon": "📈",
        "color": "#3b82f6",
        "gradient": "linear-gradient(135deg, #1e3a5f 0%, #1e40af 100%)",
        "badge": "Technical",
        "phase": "I",
    },
    "sentiment_report": {
        "title": "Sentiment Analysis",
        "icon": "💬",
        "color": "#8b5cf6",
        "gradient": "linear-gradient(135deg, #2e1065 0%, #7c3aed 100%)",
        "badge": "Social",
        "phase": "I",
    },
    "news_report": {
        "title": "News Analysis",
        "icon": "📰",
        "color": "#06b6d4",
        "gradient": "linear-gradient(135deg, #083344 0%, #0891b2 100%)",
        "badge": "News",
        "phase": "I",
    },
    "fundamentals_report": {
        "title": "Fundamentals Analysis",
        "icon": "🏦",
        "color": "#10b981",
        "gradient": "linear-gradient(135deg, #052e16 0%, #059669 100%)",
        "badge": "Fundamental",
        "phase": "I",
    },
    "investment_plan": {
        "title": "Research Team Decision",
        "icon": "🔬",
        "color": "#f59e0b",
        "gradient": "linear-gradient(135deg, #431407 0%, #b45309 100%)",
        "badge": "Research",
        "phase": "II",
    },
    "trader_investment_plan": {
        "title": "Trading Team Plan",
        "icon": "⚡",
        "color": "#f97316",
        "gradient": "linear-gradient(135deg, #431407 0%, #ea580c 100%)",
        "badge": "Trading",
        "phase": "III",
    },
    "final_trade_decision": {
        "title": "Portfolio Manager Decision",
        "icon": "🎯",
        "color": "#ec4899",
        "gradient": "linear-gradient(135deg, #500724 0%, #be185d 100%)",
        "badge": "Final",
        "phase": "V",
    },
}

PHASE_LABELS = {
    "I":   ("Analyst Team",        "#3b82f6"),
    "II":  ("Research Team",       "#f59e0b"),
    "III": ("Trading Team",        "#f97316"),
    "IV":  ("Risk Management",     "#ef4444"),
    "V":   ("Portfolio Management","#ec4899"),
}

SIGNAL_STYLES = {
    "STRONG BUY":  ("🚀", "#10b981", "#052e16", "STRONG BUY"),
    "STRONG SELL": ("🔻", "#ef4444", "#450a0a", "STRONG SELL"),
    "BUY":         ("🟢", "#10b981", "#052e16", "BUY"),
    "SELL":        ("🔴", "#ef4444", "#450a0a", "SELL"),
    "UNDERWEIGHT": ("🔴", "#ef4444", "#450a0a", "UNDERWEIGHT"),
    "OVERWEIGHT":  ("🟢", "#10b981", "#052e16", "OVERWEIGHT"),
    "HOLD":        ("🟡", "#f59e0b", "#451a03", "HOLD"),
    "NEUTRAL":     ("⚪", "#6b7280", "#1f2937", "NEUTRAL"),
}

import re as _re
# Match authoritative signal lines in priority order:
#   1. "FINAL TRANSACTION PROPOSAL: **HOLD**"  (Trader/Risk agents)
#   2. "**Rating**: Underweight"               (Portfolio Manager)
#   3. "**Action**: Hold"                      (Portfolio Manager alternate)
_SIGNAL_KEYWORDS = r"(STRONG\s+BUY|STRONG\s+SELL|UNDERWEIGHT|OVERWEIGHT|BUY|SELL|HOLD|NEUTRAL)"
_FINAL_LINE_RE = _re.compile(
    r"(?:FINAL\s+(?:TRANSACTION\s+PROPOSAL|DECISION)"  # pattern 1
    r"|(?:\*{0,2}Rating\*{0,2}\s*:)"                   # pattern 2
    r"|(?:\*{0,2}Action\*{0,2}\s*:)"                   # pattern 3
    r")\s*\**\s*" + _SIGNAL_KEYWORDS,
    _re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def detect_signal(text: str) -> tuple[str, str, str, str]:
    """Return (emoji, fg_color, bg_color, label) for the trading signal.

    Strategy:
    1. Look for a 'FINAL TRANSACTION PROPOSAL' / 'FINAL DECISION' line and
       extract the signal keyword from that line only — avoids false matches
       from bull/bear discussion text that also contains 'BUY'/'SELL'.
    2. Fall back to first keyword match if no final-line found.
    """
    m = _FINAL_LINE_RE.search(text)
    if m:
        keyword = m.group(1).upper().replace("  ", " ")
        return SIGNAL_STYLES.get(keyword, ("⚪", "#6b7280", "#1f2937", keyword))

    # Fallback: scan whole text (legacy behaviour for complete_report.md format)
    upper = text.upper()
    for key, val in SIGNAL_STYLES.items():
        if key in upper:
            return val
    return ("⚪", "#6b7280", "#1f2937", "UNKNOWN")


def md_to_html(text: str) -> str:
    """Convert markdown text to HTML."""
    if not text:
        return ""
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "nl2br",
            "sane_lists",
            "attr_list",
        ]
    )
    return md.convert(text)


def extract_ticker_from_header(text: str) -> str:
    """Try to extract ticker symbol from report header."""
    m = re.search(r"Trading Analysis Report[:\s]+([A-Z0-9.\-^]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"#\s*([A-Z0-9.\-^]{1,10})\s", text)
    if m:
        return m.group(1).strip()
    return "N/A"


def find_latest_report_dir(base: Path) -> Path | None:
    """Search ~/.tradingagents/logs and ./reports for the most recent report."""
    candidates = []

    search_roots = [
        Path.home() / ".tradingagents" / "logs",
        base / "reports",
        base,
    ]

    for root in search_roots:
        if not root.exists():
            continue
        for p in root.rglob("complete_report.md"):
            candidates.append(p)

    if not candidates:
        return None

    # Pick most recently modified
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0].parent


def load_sections(report_dir: Path) -> dict[str, str]:
    """Load section markdown content from a report directory."""
    sections: dict[str, str] = {}

    # Try individual section files first (preferred)
    section_files = {
        "market_report":          "market_report.md",
        "sentiment_report":       "sentiment_report.md",
        "news_report":            "news_report.md",
        "fundamentals_report":    "fundamentals_report.md",
        "investment_plan":        "investment_plan.md",
        "trader_investment_plan": "trader_investment_plan.md",
        "final_trade_decision":   "final_trade_decision.md",
    }

    # Also try the 1_analysts, 2_research, etc. sub-dirs
    alt_files = {
        "market_report":          report_dir / "1_analysts" / "market.md",
        "sentiment_report":       report_dir / "1_analysts" / "sentiment.md",
        "news_report":            report_dir / "1_analysts" / "news.md",
        "fundamentals_report":    report_dir / "1_analysts" / "fundamentals.md",
        "investment_plan":        report_dir / "2_research" / "manager.md",
        "trader_investment_plan": report_dir / "3_trading" / "trader.md",
        "final_trade_decision":   report_dir / "5_portfolio" / "decision.md",
    }

    # Reports sub-folder inside the run dir
    reports_subdir = report_dir / "reports"

    for key, fname in section_files.items():
        # Try reports/ subfolder first
        path = reports_subdir / fname
        if not path.exists():
            path = report_dir / fname
        if path.exists():
            sections[key] = path.read_text(encoding="utf-8")
            continue

        # Try alternate structured folders
        alt = alt_files.get(key)
        if alt and alt.exists():
            sections[key] = alt.read_text(encoding="utf-8")

    return sections


def load_from_complete_report(report_file: Path) -> tuple[str, dict[str, str]]:
    """Parse a complete_report.md into sections."""
    text = report_file.read_text(encoding="utf-8")

    # Extract ticker from header
    ticker = extract_ticker_from_header(text)

    sections: dict[str, str] = {}

    # Split by ## headings
    parts = re.split(r"^##\s+", text, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.strip().split("\n", 1)
        heading = lines[0].strip().lower()
        content = lines[1].strip() if len(lines) > 1 else ""

        if "market" in heading and "analyst" in heading:
            sections["market_report"] = content
        elif "sentiment" in heading or "social" in heading:
            sections["sentiment_report"] = content
        elif "news" in heading:
            sections["news_report"] = content
        elif "fundamental" in heading:
            sections["fundamentals_report"] = content
        elif "research" in heading:
            sections["investment_plan"] = content
        elif "trading" in heading or "trader" in heading:
            sections["trader_investment_plan"] = content
        elif "portfolio" in heading or "risk" in heading:
            sections["final_trade_decision"] = content

    return ticker, sections


# ---------------------------------------------------------------------------
# HTML Generation
# ---------------------------------------------------------------------------

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;600&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg-primary:   #0a0f1e;
  --bg-secondary: #0f1629;
  --bg-card:      #111827;
  --bg-card-hover:#141e33;
  --border:       #1f2d45;
  --border-light: #2a3d5a;
  --text-primary: #f1f5f9;
  --text-secondary:#94a3b8;
  --text-muted:   #475569;
  --accent-blue:  #3b82f6;
  --accent-purple:#8b5cf6;
  --accent-cyan:  #06b6d4;
  --accent-green: #10b981;
  --accent-amber: #f59e0b;
  --accent-rose:  #f43f5e;
  --radius-sm:    6px;
  --radius-md:    12px;
  --radius-lg:    18px;
  --radius-xl:    24px;
  --shadow-card:  0 4px 24px rgba(0,0,0,0.4);
  --shadow-glow:  0 0 40px rgba(59,130,246,0.12);
}

html { scroll-behavior: smooth; }

body {
  font-family: 'Inter', -apple-system, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  min-height: 100vh;
  line-height: 1.65;
  font-size: 15px;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--bg-primary); }
::-webkit-scrollbar-thumb { background: var(--border-light); border-radius: 3px; }

/* ── Background grid ── */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(59,130,246,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(59,130,246,0.03) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events: none;
  z-index: 0;
}

/* ── Layout ── */
.wrapper {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 24px 80px;
  position: relative;
  z-index: 1;
}

/* ── Header ── */
.report-header {
  padding: 56px 0 40px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 40px;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.header-badge {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  padding: 4px 10px;
  border-radius: 20px;
  background: rgba(59,130,246,0.15);
  color: var(--accent-blue);
  border: 1px solid rgba(59,130,246,0.25);
}

.header-date {
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.report-title {
  font-size: clamp(32px, 5vw, 52px);
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.1;
  margin-bottom: 16px;
  background: linear-gradient(135deg, #f1f5f9 0%, #94a3b8 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.ticker-highlight {
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.report-subtitle {
  font-size: 16px;
  color: var(--text-secondary);
  max-width: 600px;
}

/* ── Signal Banner ── */
.signal-banner {
  margin: 32px 0 40px;
  padding: 28px 32px;
  border-radius: var(--radius-xl);
  display: flex;
  align-items: center;
  gap: 24px;
  border: 1px solid;
  position: relative;
  overflow: hidden;
}

.signal-banner::before {
  content: '';
  position: absolute;
  inset: 0;
  opacity: 0.06;
  background: radial-gradient(ellipse at left, currentColor 0%, transparent 70%);
}

.signal-emoji {
  font-size: 48px;
  line-height: 1;
  flex-shrink: 0;
}

.signal-info { flex: 1; }

.signal-label-sm {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  opacity: 0.7;
  margin-bottom: 6px;
}

.signal-value {
  font-size: 36px;
  font-weight: 900;
  letter-spacing: -0.02em;
  line-height: 1;
}

.signal-desc {
  font-size: 13px;
  opacity: 0.65;
  margin-top: 6px;
}

/* ── Workflow timeline ── */
.workflow-bar {
  display: flex;
  gap: 0;
  margin-bottom: 40px;
  border-radius: var(--radius-md);
  overflow: hidden;
  border: 1px solid var(--border);
}

.workflow-step {
  flex: 1;
  padding: 14px 16px;
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  border-right: 1px solid var(--border);
  position: relative;
  background: var(--bg-secondary);
  transition: background 0.2s;
  cursor: default;
}
.workflow-step:last-child { border-right: none; }

.workflow-step.active {
  background: color-mix(in srgb, var(--step-color) 15%, var(--bg-secondary));
}

.workflow-step .step-num {
  display: block;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  opacity: 0.55;
  text-transform: uppercase;
  margin-bottom: 3px;
}

.workflow-step .step-name {
  color: var(--text-secondary);
}
.workflow-step.active .step-name {
  color: var(--step-color);
}

/* ── Navigation sidebar ── */
.layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 32px;
  align-items: start;
}

@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { display: none; }
}

.sidebar {
  position: sticky;
  top: 24px;
}

.sidebar-nav {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px 0;
  overflow: hidden;
}

.sidebar-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
  padding: 0 20px 12px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 8px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  text-decoration: none;
  transition: all 0.15s;
  border-left: 3px solid transparent;
}

.nav-item:hover {
  background: var(--bg-card-hover);
  color: var(--text-primary);
  border-left-color: var(--nav-color, var(--accent-blue));
}

.nav-item .nav-icon { font-size: 16px; flex-shrink: 0; }
.nav-item .nav-phase {
  font-size: 10px;
  font-weight: 700;
  background: rgba(255,255,255,0.07);
  padding: 2px 6px;
  border-radius: 4px;
  margin-left: auto;
  color: var(--text-muted);
}

/* ── Section Cards ── */
.section-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  margin-bottom: 28px;
  overflow: hidden;
  box-shadow: var(--shadow-card);
  transition: border-color 0.2s, box-shadow 0.2s;
}

.section-card:hover {
  border-color: var(--border-light);
  box-shadow: var(--shadow-card), var(--shadow-glow);
}

.card-header {
  padding: 24px 28px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: flex-start;
  gap: 16px;
}

.card-icon {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
}

.card-header-info { flex: 1; }

.card-phase {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 4px;
  opacity: 0.7;
}

.card-title {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.card-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 20px;
  border: 1px solid;
  letter-spacing: 0.06em;
  margin-top: 2px;
  align-self: flex-start;
  flex-shrink: 0;
}

.card-body {
  padding: 28px;
}

/* ── Markdown content styles ── */
.md-content h1, .md-content h2, .md-content h3,
.md-content h4, .md-content h5 {
  font-weight: 700;
  letter-spacing: -0.01em;
  margin: 28px 0 12px;
  color: var(--text-primary);
}
.md-content h1 { font-size: 24px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
.md-content h2 { font-size: 20px; }
.md-content h3 { font-size: 17px; color: var(--text-secondary); }
.md-content h4 { font-size: 15px; color: var(--text-secondary); }

.md-content p {
  margin: 12px 0;
  color: var(--text-secondary);
  line-height: 1.75;
}

.md-content ul, .md-content ol {
  margin: 12px 0;
  padding-left: 24px;
  color: var(--text-secondary);
}

.md-content li { margin: 6px 0; line-height: 1.65; }

.md-content strong {
  color: var(--text-primary);
  font-weight: 600;
}

.md-content em { color: var(--text-secondary); font-style: italic; }

.md-content blockquote {
  border-left: 3px solid var(--accent-blue);
  padding: 12px 20px;
  margin: 16px 0;
  background: rgba(59,130,246,0.06);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  color: var(--text-secondary);
  font-style: italic;
}

.md-content code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  background: rgba(59,130,246,0.1);
  color: var(--accent-cyan);
  padding: 2px 7px;
  border-radius: 4px;
}

.md-content pre {
  background: #0d1117;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 20px;
  margin: 16px 0;
  overflow-x: auto;
}

.md-content pre code {
  background: none;
  color: #e2e8f0;
  padding: 0;
  font-size: 13px;
}

.md-content table {
  width: 100%;
  border-collapse: collapse;
  margin: 20px 0;
  font-size: 14px;
}

.md-content th {
  background: rgba(59,130,246,0.12);
  color: var(--accent-blue);
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 12px 16px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.md-content td {
  padding: 11px 16px;
  border-bottom: 1px solid var(--border);
  color: var(--text-secondary);
  line-height: 1.5;
}

.md-content tr:last-child td { border-bottom: none; }
.md-content tr:hover td { background: rgba(255,255,255,0.02); }

.md-content a {
  color: var(--accent-blue);
  text-decoration: none;
}
.md-content a:hover { text-decoration: underline; }

.md-content hr {
  border: none;
  border-top: 1px solid var(--border);
  margin: 24px 0;
}

/* ── Stats bar ── */
.stats-bar {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 16px;
  margin-bottom: 40px;
}

.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px;
  text-align: center;
  transition: border-color 0.2s, transform 0.2s;
}

.stat-card:hover {
  border-color: var(--border-light);
  transform: translateY(-2px);
}

.stat-value {
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

/* ── Footer ── */
.report-footer {
  margin-top: 60px;
  padding-top: 28px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.footer-brand {
  font-size: 13px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
}

.footer-brand strong { color: var(--text-secondary); }

.footer-ts {
  font-size: 12px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
}

/* ── Animations ── */
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: translateY(0); }
}

.section-card {
  animation: fadeInUp 0.4s ease both;
}

.section-card:nth-child(1) { animation-delay: 0.05s; }
.section-card:nth-child(2) { animation-delay: 0.10s; }
.section-card:nth-child(3) { animation-delay: 0.15s; }
.section-card:nth-child(4) { animation-delay: 0.20s; }
.section-card:nth-child(5) { animation-delay: 0.25s; }
.section-card:nth-child(6) { animation-delay: 0.30s; }
.section-card:nth-child(7) { animation-delay: 0.35s; }

/* ── Print ── */
@media print {
  body { background: white; color: black; }
  .sidebar { display: none; }
  .layout { grid-template-columns: 1fr; }
  .section-card { break-inside: avoid; box-shadow: none; border: 1px solid #ddd; }
}
"""

JS = """
// Smooth active nav highlighting on scroll
const sections = document.querySelectorAll('.section-card[id]');
const navItems = document.querySelectorAll('.nav-item');

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const id = entry.target.id;
            navItems.forEach(item => {
                item.style.background = '';
                item.style.color = '';
                item.style.borderLeftColor = 'transparent';
                if (item.getAttribute('href') === '#' + id) {
                    item.style.background = 'rgba(255,255,255,0.05)';
                    item.style.color = 'var(--text-primary)';
                    item.style.borderLeftColor = item.dataset.color || '#3b82f6';
                }
            });
        }
    });
}, { threshold: 0.2, rootMargin: '-10% 0px -60% 0px' });

sections.forEach(s => observer.observe(s));

// Animate stats numbers
document.querySelectorAll('.stat-value[data-target]').forEach(el => {
    const target = parseFloat(el.dataset.target);
    const isInt = Number.isInteger(target);
    let start = null;
    const duration = 1000;
    const step = (ts) => {
        if (!start) start = ts;
        const progress = Math.min((ts - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const val = target * eased;
        el.textContent = isInt ? Math.round(val) : val.toFixed(1);
        if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
});
"""


def build_html(ticker: str, analysis_date: str, sections: dict[str, str], generated_at: str) -> str:
    """Build the complete HTML report string."""

    # Count sections present
    n_sections = len([k for k in sections if sections[k]])
    active_phases = set()
    for key, content in sections.items():
        if content and key in SECTION_META:
            active_phases.add(SECTION_META[key]["phase"])

    # Detect final signal
    signal_emoji, signal_fg, signal_bg, signal_label = "⚪", "#6b7280", "#1f2937", "PENDING"
    if sections.get("final_trade_decision"):
        signal_emoji, signal_fg, signal_bg, signal_label = detect_signal(sections["final_trade_decision"])

    # ── Workflow bar ──────────────────────────────────────────────
    phases = [("I", "Analyst Team"), ("II", "Research Team"), ("III", "Trader"),
              ("IV", "Risk Mgmt"), ("V", "Portfolio Mgr")]
    workflow_html = '<div class="workflow-bar">'
    for ph_id, ph_name in phases:
        is_active = ph_id in active_phases
        color = PHASE_LABELS[ph_id][1]
        cls = "workflow-step active" if is_active else "workflow-step"
        style = f"--step-color:{color};" if is_active else ""
        workflow_html += (
            f'<div class="{cls}" style="{style}">'
            f'<span class="step-num">Phase {ph_id}</span>'
            f'<span class="step-name">{ph_name}</span>'
            f'</div>'
        )
    workflow_html += '</div>'

    # ── Sidebar nav ───────────────────────────────────────────────
    nav_html = '<div class="sidebar-nav"><div class="sidebar-title">Sections</div>'
    for key, content in sections.items():
        if not content or key not in SECTION_META:
            continue
        meta = SECTION_META[key]
        nav_html += (
            f'<a class="nav-item" href="#{key}" '
            f'style="--nav-color:{meta["color"]}" data-color="{meta["color"]}">'
            f'<span class="nav-icon">{meta["icon"]}</span>'
            f'<span>{meta["title"]}</span>'
            f'<span class="nav-phase">{meta["phase"]}</span>'
            f'</a>'
        )
    nav_html += '</div>'

    # ── Section cards ─────────────────────────────────────────────
    cards_html = ""
    for key, content in sections.items():
        if not content or key not in SECTION_META:
            continue
        meta = SECTION_META[key]
        body_html = md_to_html(content)

        # Icon background gradient
        icon_style = f'background: {meta["gradient"]}; color: {meta["color"]};'
        badge_style = (
            f'background: color-mix(in srgb, {meta["color"]} 15%, transparent);'
            f'color: {meta["color"]};'
            f'border-color: color-mix(in srgb, {meta["color"]} 30%, transparent);'
        )
        phase_style = f'color: {meta["color"]};'

        cards_html += f"""
<div class="section-card" id="{key}">
  <div class="card-header">
    <div class="card-icon" style="{icon_style}">{meta['icon']}</div>
    <div class="card-header-info">
      <div class="card-phase" style="{phase_style}">Phase {meta['phase']} · {PHASE_LABELS[meta['phase']][0]}</div>
      <div class="card-title">{meta['title']}</div>
    </div>
    <div class="card-badge" style="{badge_style}">{meta['badge']}</div>
  </div>
  <div class="card-body">
    <div class="md-content">{body_html}</div>
  </div>
</div>
"""

    # ── Signal banner ─────────────────────────────────────────────
    signal_border = f"color: {signal_fg}; border-color: {signal_fg}; background: {signal_bg};"
    signal_banner = f"""
<div class="signal-banner" style="{signal_border}">
  <div class="signal-emoji">{signal_emoji}</div>
  <div class="signal-info">
    <div class="signal-label-sm" style="color:{signal_fg}">Final Trading Signal</div>
    <div class="signal-value" style="color:{signal_fg}">{signal_label}</div>
    <div class="signal-desc" style="color:{signal_fg}">
      Generated by Multi-Agent AI Analysis Framework
    </div>
  </div>
</div>
"""

    # ── Stats bar ─────────────────────────────────────────────────
    stats_bar = f"""
<div class="stats-bar">
  <div class="stat-card">
    <div class="stat-value" style="color:#3b82f6" data-target="{n_sections}">{n_sections}</div>
    <div class="stat-label">Reports</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#8b5cf6" data-target="{len(active_phases)}">{len(active_phases)}</div>
    <div class="stat-label">Phases</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:{signal_fg}">{signal_label}</div>
    <div class="stat-label">Signal</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#10b981">{analysis_date}</div>
    <div class="stat-label">Analysis Date</div>
  </div>
</div>
"""

    # ── Full HTML ─────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{ticker} — Investment Analysis Report · TradingAgents</title>
  <meta name="description" content="AI-powered multi-agent investment analysis for {ticker} on {analysis_date}">
  <style>{CSS}</style>
</head>
<body>
<div class="wrapper">

  <!-- Header -->
  <header class="report-header">
    <div class="header-meta">
      <span class="header-badge">TradingAgents AI</span>
      <span class="header-date">🕐 Generated {generated_at}</span>
    </div>
    <h1 class="report-title">
      <span class="ticker-highlight">{ticker}</span> Investment Report
    </h1>
    <p class="report-subtitle">
      Multi-agent LLM analysis covering market technicals, fundamentals, news sentiment,
      bull/bear debate, risk management and portfolio decision.
    </p>
  </header>

  <!-- Signal Banner -->
  {signal_banner}

  <!-- Stats Bar -->
  {stats_bar}

  <!-- Workflow -->
  {workflow_html}

  <!-- Main layout -->
  <div class="layout">
    <aside class="sidebar">
      {nav_html}
    </aside>
    <main>
      {cards_html}
    </main>
  </div>

  <!-- Footer -->
  <footer class="report-footer">
    <div class="footer-brand">
      🤖 <strong>TradingAgents</strong> · Multi-Agent LLM Financial Framework
    </div>
    <div class="footer-ts">{generated_at}</div>
  </footer>

</div>
<script>{JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Render TradingAgents report as a beautiful HTML page."
    )
    parser.add_argument(
        "--report-dir", "-d",
        help="Path to the report directory (e.g. reports/NVDA_20240510). "
             "If not given, auto-detects the latest report.",
    )
    parser.add_argument(
        "--report-file", "-f",
        help="Path to a complete_report.md file.",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output HTML file path (default: <report_dir>/report.html).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't open browser after generating.",
    )
    parser.add_argument(
        "--ticker",
        default="",
        help="Override ticker symbol displayed in the report.",
    )
    parser.add_argument(
        "--date",
        default="",
        help="Override analysis date displayed in the report.",
    )
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- Locate report ---
    report_dir: Path | None = None
    ticker = args.ticker
    analysis_date = args.date or datetime.now().strftime("%Y-%m-%d")
    sections: dict[str, str] = {}

    if args.report_file:
        report_file = Path(args.report_file)
        if not report_file.exists():
            print(f"[ERROR] Report file not found: {report_file}")
            sys.exit(1)
        report_dir = report_file.parent
        ticker_found, sections = load_from_complete_report(report_file)
        if not ticker:
            ticker = ticker_found

    elif args.report_dir:
        report_dir = Path(args.report_dir)
        if not report_dir.exists():
            print(f"[ERROR] Report directory not found: {report_dir}")
            sys.exit(1)
        sections = load_sections(report_dir)

        # Try complete_report.md fallback
        cr = report_dir / "complete_report.md"
        if not sections and cr.exists():
            ticker_found, sections = load_from_complete_report(cr)
            if not ticker:
                ticker = ticker_found

        # Infer ticker from folder name
        if not ticker:
            ticker = report_dir.name.split("_")[0].upper()

    else:
        # Auto-detect latest report
        print("[render_report] No report specified — searching for latest report...")
        report_dir = find_latest_report_dir(base_dir)
        if not report_dir:
            print(
                "[ERROR] No reports found.\n"
                "  Run TradingAgents first, or use --report-dir / --report-file.\n"
                "  Example: python render_report.py --report-dir reports/NVDA_20240510"
            )
            sys.exit(1)
        print(f"[render_report] Found report: {report_dir}")
        sections = load_sections(report_dir)

        cr = report_dir / "complete_report.md"
        if not sections and cr.exists():
            ticker_found, sections = load_from_complete_report(cr)
            if not ticker:
                ticker = ticker_found

        if not ticker:
            ticker = report_dir.name.split("_")[0].upper()

    # Infer analysis date from directory path if not given
    if not analysis_date or analysis_date == datetime.now().strftime("%Y-%m-%d"):
        # Try YYYY-MM-DD pattern in path
        m = re.search(r"(\d{4}-\d{2}-\d{2})", str(report_dir))
        if m:
            analysis_date = m.group(1)

    if not sections:
        print("[ERROR] No report sections found. Make sure the report directory contains .md files.")
        sys.exit(1)

    print(f"[render_report] Ticker       : {ticker or 'N/A'}")
    print(f"[render_report] Analysis date: {analysis_date}")
    print(f"[render_report] Sections     : {list(sections.keys())}")

    # --- Build HTML ---
    html = build_html(ticker or "N/A", analysis_date, sections, generated_at)

    # --- Write output ---
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = (report_dir / "report.html") if report_dir else (base_dir / "report.html")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"[render_report] OK Report saved: {out_path.resolve()}")

    # --- Open browser ---
    if not args.no_open:
        url = out_path.resolve().as_uri()
        print(f"[render_report] >> Opening browser: {url}")
        webbrowser.open(url)


if __name__ == "__main__":
    main()
