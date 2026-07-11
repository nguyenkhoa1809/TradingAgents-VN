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
import json
import os
import re
import sys
import webbrowser

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from datetime import datetime
from pathlib import Path

# Optional: VN chart renderer (requires tradingagents package in PYTHONPATH)
_render_vn_charts_html = None
try:
    from tradingagents.agents.utils.vn_financial_fetcher import render_vn_charts_html as _render_vn_charts_html
except Exception:
    pass


_VN_CHART_RE = re.compile(r"<!--\s*VN_CHART_DATA\s+(\{.*?\})\s*-->", re.DOTALL)
_VN_TECH_RE  = re.compile(r"<!--\s*VN_TECH_DATA\s+(\{.*?\})\s*-->", re.DOTALL)


def _extract_vn_chart_data(content: str) -> tuple[str, dict]:
    """
    Strips the <!-- VN_CHART_DATA {...} --> comment from content.
    Returns (cleaned_content, chart_data_dict).
    """
    m = _VN_CHART_RE.search(content)
    if not m:
        return content, {}
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        data = {}
    cleaned = _VN_CHART_RE.sub("", content).strip()
    return cleaned, data


def _extract_vn_tech_data(content: str) -> tuple[str, dict]:
    """Strips the <!-- VN_TECH_DATA {...} --> comment (market report technicals)."""
    m = _VN_TECH_RE.search(content)
    if not m:
        return content, {}
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        data = {}
    cleaned = _VN_TECH_RE.sub("", content).strip()
    return cleaned, data

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
    "risk_review": {
        "title": "Risk Officer Review",
        "icon": "🛡️",
        "color": "#ef4444",
        "gradient": "linear-gradient(135deg, #450a0a 0%, #dc2626 100%)",
        "badge": "Risk",
        "phase": "IV",
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


def _inline_md(text: str) -> str:
    """Escape HTML then render inline **bold** — for short strings (banner items)
    that must go through a render step instead of leaking raw markdown (A9)."""
    s = _html.escape(text)
    s = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return s


def _normalize_md_tables(text: str) -> str:
    """Ensure a blank line precedes every markdown table block.

    The LLM often emits a table right after a heading/paragraph with no blank
    line; python-markdown's ``tables`` extension then fails to recognise it and
    the raw ``| ... |`` / ``|---|`` leaks into the HTML (A4). Inserting the
    blank line makes the parser convert it to a real <table>.
    """
    def is_row(s: str) -> bool:
        s = s.strip()
        return s.startswith("|") and s.count("|") >= 2

    out: list[str] = []
    for line in text.split("\n"):
        if is_row(line) and out and out[-1].strip() != "" and not is_row(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


_SOURCE_TAG_RE = _re.compile(r"\[nguồn:\s*([^\]]{1,200})\]", _re.IGNORECASE)
_UNVERIFIED_TAG_RE = _re.compile(r"\[CHƯA KIỂM CHỨNG\]", _re.IGNORECASE)


def _convert_citation_tags(text: str) -> str:
    """C4: Convert [nguồn: ...] and [CHƯA KIỂM CHỨNG] to styled HTML spans.

    Applied globally in md_to_html so citation tags render properly in the report.
    """
    text = _SOURCE_TAG_RE.sub(
        lambda m: f'<span class="src-tag" title="Nguồn: {_html.escape(m.group(1))}">'
                  f'[nguồn: {_html.escape(m.group(1)[:60])}{"…" if len(m.group(1)) > 60 else ""}]</span>',
        text,
    )
    text = _UNVERIFIED_TAG_RE.sub(
        '<span class="unverified-tag">[CHƯA KIỂM CHỨNG]</span>',
        text,
    )
    return text


def md_to_html(text: str) -> str:
    """Convert markdown text to HTML.

    All output (every phase) flows through here, so sentiment markers and table
    normalisation are applied centrally — no agent text reaches the HTML raw.
    """
    if not text:
        return ""
    text = _convert_sentiment_markers(text)   # [TÍCH CỰC] → badge, mọi section (A4)
    text = _convert_citation_tags(text)        # [nguồn:...] / [CHƯA KIỂM CHỨNG] → spans (C4)
    text = _normalize_md_tables(text)          # bảng markdown → <table> (A4)
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


# ── News digest enhancers ──────────────────────────────────────────────────
# Sentiment markers the analyst can emit, mapped to badge class + label
_SENTIMENT_MAP = {
    "TÍCH CỰC":  ("sent-bull", "TÍCH CỰC"),
    "BULLISH":   ("sent-bull", "TÍCH CỰC"),
    "TIÊU CỰC":  ("sent-bear", "TIÊU CỰC"),
    "BEARISH":   ("sent-bear", "TIÊU CỰC"),
    "TRUNG LẬP": ("sent-neu",  "TRUNG LẬP"),
    "NEUTRAL":   ("sent-neu",  "TRUNG LẬP"),
}
_SENT_RE = _re.compile(
    r"\[\s*(" + "|".join(_re.escape(k) for k in _SENTIMENT_MAP) + r")\s*\]",
    _re.IGNORECASE,
)
# Signed percentage: +12.5% / -3,2%  →  colored green/red
_SIGNED_PCT_RE = _re.compile(r"(?<![\w])([+\-−])\s?(\d[\d.,]*\s?%)")
# Neutral numeric highlight: 12.5%, 2.5x, 3,8 lần, 5.234 tỷ, 1.200 đồng
_NUM_HL_RE = _re.compile(
    r"(?<![\w.])(\d[\d.,]*\s?(?:%|tỷ|nghìn tỷ|lần|x|đồng|VND|tỉ))(?![\w])",
    _re.IGNORECASE,
)


def _convert_sentiment_markers(text: str) -> str:
    """Replace [TÍCH CỰC]/[BULLISH]/... markers with styled HTML badges."""
    def repl(m):
        key = m.group(1).upper()
        cls, label = _SENTIMENT_MAP.get(key, ("sent-neu", key))
        return f'<span class="sent {cls}">{label}</span>'
    return _SENT_RE.sub(repl, text)


def _highlight_numbers(text: str) -> str:
    """Wrap financial figures in styled spans for a catchy news digest.

    Runs on markdown text before conversion; python-markdown passes the
    inline HTML spans through untouched. Skips lines inside markdown tables
    (pipes) to avoid breaking column alignment.
    """
    out_lines = []
    for line in text.split("\n"):
        if "|" in line and line.strip().startswith("|"):
            out_lines.append(line)  # leave tables alone
            continue
        line = _SIGNED_PCT_RE.sub(
            lambda m: (
                f'<span class="{"num-pos" if m.group(1) in "+" else "num-neg"}">'
                f'{m.group(1)}{m.group(2)}</span>'
            ),
            line,
        )
        line = _NUM_HL_RE.sub(lambda m: f'<span class="num-hl">{m.group(1)}</span>', line)
        out_lines.append(line)
    return "\n".join(out_lines)


def enhance_news_digest(text: str) -> str:
    """Apply sentiment badges + number highlighting to news markdown."""
    if not text:
        return text
    text = _convert_sentiment_markers(text)
    text = _highlight_numbers(text)
    return text


# ── Report reconciliation validator (A7) ───────────────────────────────────
# WARN by default (log + banner). Set TRADINGAGENTS_STRICT_VALIDATION=1 to raise
# and block render when a report fails consistency checks.
STRICT_VALIDATION = os.getenv("TRADINGAGENTS_STRICT_VALIDATION", "") in ("1", "true", "True")

_PCT_ABSURD_RE = _re.compile(r"([+\-−]?\d[\d.,]*)\s*%")
_PCT_ABSURD_LIMIT = 500.0  # |%| lớn hơn ngưỡng này gần như chắc là lỗi format (A5/A6)

# D3: Bắt lỗi hướng so sánh multiple (vd "target 12x cao hơn hiện tại 13x" — sai số học)
# Pattern: số x ... (cao hơn|thấp hơn) ... số x  (khoảng cách tối đa 80 chars mỗi bên)
_MULTIPLES_CMP_RE = _re.compile(
    r"(\d[\d.,]+)\s*[xX×]\b[^\n]{0,80}?"
    r"\b(cao hơn|thấp hơn|higher than|lower than)\b[^\n]{0,80}?"
    r"(\d[\d.,]+)\s*[xX×]\b",
    _re.IGNORECASE,
)


def _parse_num_plain(s: str) -> "float | None":
    """Parse số dạng '12.0', '13,19', '1,250' — dùng locale-aware logic như _pct_magnitude."""
    s = s.strip()
    if "." in s and "," in s:
        s = s.replace(",", "")
    elif "," in s:
        # phẩy thập phân VN nếu nhóm cuối ≤2 chữ số
        if len(s.split(",")[-1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _pct_magnitude(raw: str) -> float | None:
    """Parse a percent token's magnitude, tolerant of cả locale EN lẫn VN.

    Phải phân biệt phẩy thập phân VN ('12,94' = 12.94) với phẩy ngăn nghìn EN
    ('1,250' = 1250) — nếu không '12,94%' bị hiểu nhầm thành 1294% (false positive).
    Quy tắc: có cả '.' và ',' → ',' là ngăn nghìn; chỉ có ',' và nhóm cuối ≤2 chữ số
    → ',' là dấu thập phân; còn lại → ngăn nghìn.
    """
    s = raw.replace("−", "-").replace(" ", "")
    if "." in s and "," in s:
        s = s.replace(",", "")
    elif "," in s:
        if len(s.split(",")[-1]) <= 2:
            s = s.replace(",", ".", 1).replace(",", "")  # phẩy thập phân VN
        else:
            s = s.replace(",", "")                         # phẩy ngăn nghìn
    try:
        return abs(float(s))
    except ValueError:
        return None


def validate_report(sections: dict[str, str], financials: dict | None = None) -> list[str]:
    """Reconciliation checks chạy trước render (A7). Trả về list cảnh báo.

    Hiện kiểm:
      - Phần trăm vô lý (|%| > 500) — bắt lỗi giá trị tuyệt đối bị gắn '%'
        hoặc alpha/growth tính sai (vd '+319.0%').
    Mở rộng được: thêm assert FCF một chuỗi, net_margin == LNST/DT, ROE/P/B khớp
    giữa các phase khi đã có parser bảng cho từng phase.
    """
    warnings: list[str] = []
    for key, text in sections.items():
        if not text:
            continue
        # A5/A8: phần trăm vô lý (|%| > 500)
        for m in _PCT_ABSURD_RE.finditer(text):
            mag = _pct_magnitude(m.group(1))
            if mag is not None and mag > _PCT_ABSURD_LIMIT:
                ctx = text[max(0, m.start() - 30): m.end() + 10].replace("\n", " ")
                warnings.append(f"[{key}] % vô lý: '{m.group(0).strip()}' (…{ctx.strip()}…)")
        # D3: hướng so sánh multiple sai số học
        for m in _MULTIPLES_CMP_RE.finditer(text):
            n1 = _parse_num_plain(m.group(1))
            direction = m.group(2).lower()
            n2 = _parse_num_plain(m.group(3))
            if n1 is None or n2 is None or n1 == n2:
                continue
            up_words   = ("cao hơn", "higher than")
            down_words = ("thấp hơn", "lower than")
            wrong = (direction in up_words and n1 < n2) or (direction in down_words and n1 > n2)
            if wrong:
                snippet = m.group(0).replace("\n", " ")[:80]
                warnings.append(
                    f"[{key}] Multiple direction sai: '{snippet}' "
                    f"({n1}x không {direction} {n2}x)"
                )
    # G3: Trader input must be present and non-stub when PM runs — CHỈ áp dụng cho
    # pipeline_mode="full". Ở mode "rating" không có Trader (Risk Officer thay thế),
    # nên sự hiện diện của section risk_review = trader vắng mặt là HỢP LỆ, không cảnh báo.
    _rating_mode = bool(sections.get("risk_review", "").strip())
    if not _rating_mode:
        _trader_text = sections.get("trader_investment_plan", "").strip()
        if not _trader_text or "[MISSING" in _trader_text:
            warnings.append(
                "[trader_investment_plan] MISSING — Trader phase produced no output; "
                "PM decision is based on incomplete upstream input"
            )

    # E2: PM **Rating** field must match what detect_signal extracts for the banner
    pm_text = sections.get("final_trade_decision", "")
    if pm_text:
        _pm_rating_m = _re.search(
            r'\*\*Rating\*\*\s*:\s*(Strong\s+Buy|Strong\s+Sell|Buy|Overweight|Hold|Underweight|Sell|Neutral)',
            pm_text, _re.IGNORECASE
        )
        if _pm_rating_m:
            _pm_field = _re.sub(r"\s+", " ", _pm_rating_m.group(1).strip().upper())
            _banner_signal = detect_signal(pm_text)[3].upper()
            if _banner_signal not in ("PENDING", "UNKNOWN") and _pm_field != _banner_signal:
                warnings.append(
                    f"[final_trade_decision] Rating field '{_pm_rating_m.group(1)}' "
                    f"!= banner signal '{_banner_signal}' — PM text is inconsistent"
                )

    return warnings


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
  --accent-pink:  #ec4899;
  --accent-teal:  #14b8a6;
  --fs-caption: 11px;
  --fs-small: 13px;
  --fs-base: 15px;
  --fs-body: 16px;
  --fs-lg: 19px;
  --fs-xl: 23px;
  --fs-x2: 28px;
  --fs-title: clamp(32px, 5vw, 52px);
  --fs-display: 46px;
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
  line-height: 1.68;
  font-size: var(--fs-body);
  font-feature-settings: 'tnum', 'cv01';
  -webkit-font-smoothing: antialiased;
}
/* Số liệu trong văn bản: tabular figures dễ đọc/căn cột */
.md-content :is(td, th) { font-variant-numeric: tabular-nums; }

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
  max-width: 1380px;
  margin: 0 auto;
  padding: 0 32px 80px;
  position: relative;
  z-index: 1;
}

/* ── Header (full-width signal banner) ── */
.report-header {
  padding: 40px 36px 34px;
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  margin-bottom: 36px;
  display: flex;
  align-items: center;
  gap: 36px;
  position: relative;
  overflow: hidden;
}
.header-left {
  flex: 1;
  min-width: 0;
  position: relative;
  z-index: 1;
}
.header-signal-box {
  flex-shrink: 0;
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  text-align: right;
  gap: 3px;
}
.header-signal-box .sig-emoji { font-size: var(--fs-x2); line-height: 1; margin-bottom: 4px; }
.header-signal-box .sig-label-sm { font-size: var(--fs-caption); letter-spacing: 0.16em; text-transform: uppercase; font-weight: 700; opacity: 0.85; }
.header-signal-box .sig-value { font-size: var(--fs-display); font-weight: 900; letter-spacing: -0.01em; line-height: 1; }
.header-signal-box .sig-date { font-size: var(--fs-small); font-weight: 500; opacity: 0.8; margin-top: 5px; }
.header-signal-box .sig-conviction { font-size: var(--fs-small); font-weight: 700; letter-spacing: 0.06em; margin-top: 4px; }

@media (max-width: 720px) {
  .report-header { flex-direction: column; align-items: flex-start; gap: 20px; padding: 28px 22px; }
  .header-signal-box { align-items: flex-start; text-align: left; }
  .header-signal-box .sig-value { font-size: var(--fs-display); }
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.header-badge {
  font-size: var(--fs-caption);
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
  font-size: var(--fs-small);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.header-models {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.model-chip {
  font-size: var(--fs-caption);
  padding: 3px 9px;
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.05);
  color: var(--text-muted);
}

.model-chip .chip-label {
  color: var(--text-secondary);
  font-weight: 600;
  margin-right: 4px;
}

.cost-chip {
  font-size: var(--fs-caption);
  padding: 3px 9px;
  border-radius: 12px;
  border: 1px solid rgba(34,197,94,0.3);
  background: rgba(34,197,94,0.08);
  color: #4ade80;
  font-weight: 600;
}

.report-title {
  font-size: var(--fs-title);
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
  background: linear-gradient(135deg, #ec4899, #a855f7);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.report-subtitle {
  font-size: var(--fs-body);
  color: var(--text-secondary);
  max-width: 640px;
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
  font-size: var(--fs-display);
  line-height: 1;
  flex-shrink: 0;
}

.signal-info { flex: 1; }

.signal-label-sm {
  font-size: var(--fs-caption);
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  opacity: 0.7;
  margin-bottom: 6px;
}

.signal-value {
  font-size: var(--fs-display);
  font-weight: 900;
  letter-spacing: -0.02em;
  line-height: 1;
}

.signal-desc {
  font-size: var(--fs-small);
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
  font-size: var(--fs-small);
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
  font-size: var(--fs-caption);
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

/* ── Horizontal sticky top navigation ── */
.layout { display: block; }
.topnav {
  position: sticky;
  top: 0;
  z-index: 50;
  margin: 0 0 28px;
  padding: 10px 8px;
  background: rgba(10,15,30,0.82);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  display: flex;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: none;
}
.topnav::-webkit-scrollbar { display: none; }
.nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  font-size: var(--fs-base);
  font-weight: 600;
  color: var(--text-secondary);
  text-decoration: none;
  white-space: nowrap;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  transition: all 0.15s;
  flex-shrink: 0;
}
.nav-item:hover {
  background: var(--bg-card-hover);
  color: var(--text-primary);
}
.nav-item.active {
  background: color-mix(in srgb, var(--nav-color, var(--accent-blue)) 16%, transparent);
  color: var(--nav-color, var(--accent-blue));
  border-color: color-mix(in srgb, var(--nav-color, var(--accent-blue)) 35%, transparent);
}
.nav-item .nav-icon { font-size: var(--fs-base); flex-shrink: 0; }
.nav-item .nav-phase {
  font-size: var(--fs-caption);
  font-weight: 700;
  background: rgba(255,255,255,0.07);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--text-muted);
}

/* Offset anchor jumps so sticky nav doesn't cover section headers */
.section-card { scroll-margin-top: 80px; }

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
  font-size: var(--fs-xl);
  flex-shrink: 0;
}

.card-header-info { flex: 1; }

.card-phase {
  font-size: var(--fs-caption);
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: 4px;
  opacity: 0.7;
}

.card-title {
  font-size: var(--fs-xl);
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.card-badge {
  font-size: var(--fs-caption);
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
.md-content h1 { font-size: var(--fs-x2); border-bottom: 1px solid var(--border); padding-bottom: 10px; }
.md-content h2 {
  font-size: var(--fs-xl);
  padding-left: 13px;
  border-left: 4px solid var(--accent-pink);
  line-height: 1.3;
}
.md-content h3 { font-size: var(--fs-lg); color: #cbd5e1; }
.md-content h4 { font-size: var(--fs-body); color: var(--text-secondary); }

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
  font-size: var(--fs-small);
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
  font-size: var(--fs-small);
}

.md-content table {
  width: 100%;
  border-collapse: collapse;
  margin: 20px 0;
  font-size: var(--fs-base);
}

.md-content th {
  background: rgba(59,130,246,0.12);
  color: var(--accent-blue);
  font-weight: 600;
  font-size: var(--fs-small);
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

/* ── Number highlighting (news digest) ── */
.num-hl {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
  color: #fbbf24;
  background: rgba(251,191,36,0.10);
  padding: 0 4px;
  border-radius: 4px;
}
.num-pos {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  color: #34d399;
  background: rgba(52,211,153,0.12);
  padding: 0 4px;
  border-radius: 4px;
}
.num-neg {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  color: #f87171;
  background: rgba(248,113,113,0.12);
  padding: 0 4px;
  border-radius: 4px;
}

/* ── Sentiment badges ── */
.sent {
  display: inline-block;
  font-size: var(--fs-caption);
  font-weight: 700;
  letter-spacing: 0.04em;
  padding: 2px 9px;
  border-radius: 20px;
  margin-right: 6px;
  vertical-align: middle;
  border: 1px solid;
}
.sent-bull { color: #34d399; background: rgba(52,211,153,0.12); border-color: rgba(52,211,153,0.35); }
.sent-bear { color: #f87171; background: rgba(248,113,113,0.12); border-color: rgba(248,113,113,0.35); }
.sent-neu  { color: #94a3b8; background: rgba(148,163,184,0.10); border-color: rgba(148,163,184,0.30); }

/* ── Fact-check citation tags (C4) ── */
.src-tag {
  display: inline-block; font-size: var(--fs-caption); font-weight: 500;
  padding: 1px 6px; border-radius: 4px; margin: 0 2px;
  color: #60a5fa; background: rgba(96,165,250,0.10); border: 1px solid rgba(96,165,250,0.30);
  cursor: help;
}
.unverified-tag {
  display: inline-block; font-size: var(--fs-caption); font-weight: 600;
  padding: 1px 6px; border-radius: 4px; margin: 0 2px;
  color: #fb923c; background: rgba(251,146,60,0.12); border: 1px solid rgba(251,146,60,0.35);
}

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
  font-size: var(--fs-x2);
  font-weight: 800;
  letter-spacing: -0.02em;
  margin-bottom: 4px;
}

.stat-label {
  font-size: var(--fs-small);
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
  font-size: var(--fs-small);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
}

.footer-brand strong { color: var(--text-secondary); }

.footer-ts {
  font-size: var(--fs-small);
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

/* ── Hero metrics strip (BSR-style key figures) ── */
.hero-metrics-wrap { margin: 0 0 26px; }
.hm-ticker {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 14px;
}
.hm-ticker .hm-tk {
  font-size: var(--fs-xl);
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--text-primary);
}
.hm-ticker .hm-sector {
  font-size: var(--fs-small);
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.hero-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
  gap: 14px;
}
.hm-card {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 15px 18px 15px 20px;
  overflow: hidden;
}
.hm-card::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: var(--hm-accent, var(--accent-blue));
}
.hm-label {
  font-size: var(--fs-caption);
  font-weight: 600;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 9px;
}
.hm-value {
  font-family: 'JetBrains Mono', monospace;
  font-size: var(--fs-x2);
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1;
  color: var(--text-primary);
  font-feature-settings: 'tnum';
}
.hm-value .hm-unit {
  font-family: 'Inter', sans-serif;
  font-size: var(--fs-small);
  font-weight: 500;
  color: var(--text-secondary);
  margin-left: 4px;
}
.hm-sub { font-size: var(--fs-caption); font-weight: 600; margin-top: 8px; }
.hm-up   { color: #34d399; }
.hm-down { color: #f87171; }
.hm-flat { color: var(--text-muted); }

/* ── Executive summary hero (top of report) ── */
.exec-hero {
  margin: 0 0 28px;
  border-radius: var(--radius-xl);
  border: 1px solid var(--border-light);
  background: linear-gradient(160deg, #101a30 0%, #0c1322 100%);
  overflow: hidden;
}
.exec-hero-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 26px;
  border-bottom: 1px solid var(--border);
  background: rgba(59,130,246,0.06);
}
.exec-hero-head .eh-icon { font-size: var(--fs-xl); }
.exec-hero-head .eh-title { font-size: var(--fs-body); font-weight: 800; letter-spacing: -0.01em; }
.exec-hero-body { padding: 22px 26px; }
.exec-hero-body .md-content p { margin: 10px 0; }
.exec-hero-body .md-content ul { margin: 8px 0; }

/* ── Financial summary block (tables + charts) ── */
.fin-block {
  margin: 0 0 28px;
  border-radius: var(--radius-xl);
  border: 1px solid var(--border);
  background: var(--bg-card);
  overflow: hidden;
}
.fin-block-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 26px;
  border-bottom: 1px solid var(--border);
}
.fin-block-head .fb-icon { font-size: var(--fs-lg); }
.fin-block-head .fb-title { font-size: var(--fs-body); font-weight: 800; }
.fin-block-body { padding: 22px 26px; }
.fin-tables {
  display: grid;
  grid-template-columns: 1fr;
  gap: 22px;
  margin-bottom: 26px;
}
@media (min-width: 920px) { .fin-tables { grid-template-columns: 1fr 1fr; } }
.fin-table-wrap { min-width: 0; overflow-x: auto; }
.fin-table-wrap h4 {
  font-size: var(--fs-small);
  font-weight: 700;
  color: var(--text-secondary);
  margin-bottom: 10px;
  letter-spacing: 0.02em;
}
.fin-note {
  font-size: var(--fs-small);
  color: var(--text-muted);
  margin-top: 8px;
}
table.fin-tbl { width: 100%; border-collapse: collapse; font-size: var(--fs-small); }
table.fin-tbl th {
  background: rgba(59,130,246,0.12);
  color: var(--accent-blue);
  font-weight: 600;
  text-align: right;
  padding: 7px 10px;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0;
}
table.fin-tbl th:first-child { text-align: left; }
table.fin-tbl td {
  padding: 6px 10px;
  text-align: right;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
  font-family: 'JetBrains Mono', monospace;
  color: var(--text-secondary);
}
table.fin-tbl td:first-child { text-align: left; font-family: 'Inter', sans-serif; color: var(--text-primary); font-weight: 600; }
table.fin-tbl tr:hover td { background: rgba(255,255,255,0.025); }
table.fin-tbl td.pos { color: #34d399; }
table.fin-tbl td.neg { color: #f87171; }

/* ── Interactive charts ── */
.charts-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 18px;
}
@media (min-width: 760px) { .charts-grid { grid-template-columns: 1fr 1fr; } }
.vchart {
  position: relative;
  min-width: 0;
  background: linear-gradient(180deg,#0f1729,#0c1322);
  border: 1px solid #1c2740;
  border-radius: 14px;
  padding: 4px;
}
.vchart svg { display: block; width: 100%; height: auto; }
.vchart-tip {
  position: absolute;
  pointer-events: none;
  z-index: 20;
  background: rgba(8,12,22,0.96);
  border: 1px solid #2a3d5a;
  border-radius: 8px;
  padding: 8px 11px;
  font-size: var(--fs-caption);
  color: #e2e8f0;
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  white-space: nowrap;
  opacity: 0;
  transition: opacity 0.12s;
}
.vchart-tip .tip-x { font-weight: 700; margin-bottom: 4px; color: #94a3b8; }
.vchart-tip .tip-row { display: flex; align-items: center; gap: 7px; line-height: 1.5; }
.vchart-tip .tip-dot { width: 9px; height: 9px; border-radius: 2px; flex-shrink: 0; }
.vchart-tip .tip-val { margin-left: auto; font-family: 'JetBrains Mono', monospace; font-weight: 600; }

/* ── Validator warning banner (A7) ── */
.validator-banner {
  margin: 0 0 24px;
  padding: 14px 20px;
  border-radius: var(--radius-lg);
  border: 1px solid rgba(245,158,11,0.45);
  background: rgba(245,158,11,0.08);
  color: #fbbf24;
  font-size: var(--fs-small);
}
.validator-banner ul { margin: 8px 0 0 18px; }
.validator-banner li { margin: 3px 0; color: var(--text-secondary); font-family: 'JetBrains Mono', monospace; font-size: var(--fs-small); }

/* ── Technical analysis block (BSR-style) ── */
.tech-block { margin-bottom: 24px; }
.tech-score-card {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 22px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
  margin-bottom: 18px;
}
.ts-main { flex: 1; min-width: 200px; }
.ts-label {
  font-size: var(--fs-caption); font-weight: 600; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px;
}
.ts-score-row { display: flex; align-items: center; gap: 16px; }
.ts-score { font-size: var(--fs-display); font-weight: 900; line-height: 1; font-family: 'JetBrains Mono', monospace; }
.ts-score-max { font-size: var(--fs-xl); opacity: 0.55; }
.ts-pill {
  font-size: var(--fs-base); font-weight: 800; letter-spacing: 0.04em;
  padding: 8px 18px; border-radius: 22px; border: 1px solid;
}
.ts-note { font-size: var(--fs-small); color: var(--text-secondary); margin-top: 10px; }
.ts-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(125px, 1fr));
  gap: 10px;
  flex: 2;
  min-width: 260px;
}
.ts-metric {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 11px 13px 11px 15px;
  overflow: hidden;
}
.ts-metric::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px; background: var(--hm-accent, var(--accent-blue));
}
.ts-m-label {
  font-size: var(--fs-caption); font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--text-muted); margin-bottom: 5px;
}
.ts-m-value { font-size: var(--fs-lg); font-weight: 800; font-family: 'JetBrains Mono', monospace; line-height: 1; }
.ts-m-sub { font-size: var(--fs-caption); color: var(--text-muted); margin-top: 4px; }
.tech-price { margin-bottom: 18px; }

/* ── Agent rating summary table (E3) ── */
.art-wrap {
  margin: 0 0 28px;
  border-radius: var(--radius-xl);
  border: 1px solid var(--border-light);
  background: var(--bg-card);
  overflow: hidden;
}
.art-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 26px;
  border-bottom: 1px solid var(--border);
  background: rgba(59,130,246,0.06);
}
.art-head .art-icon { font-size: var(--fs-xl); }
.art-head .art-title { font-size: var(--fs-body); font-weight: 800; letter-spacing: -0.01em; flex: 1; }
.art-body { padding: 18px 26px 22px; }
table.art-tbl { width: 100%; border-collapse: collapse; font-size: var(--fs-base); }
table.art-tbl th {
  background: rgba(59,130,246,0.10);
  color: var(--accent-blue);
  font-weight: 700;
  font-size: var(--fs-small);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
table.art-tbl td {
  padding: 11px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text-secondary);
  vertical-align: middle;
}
table.art-tbl tr:last-child td { border-bottom: none; }
table.art-tbl tr.art-pm td { background: rgba(236,72,153,0.06); }
table.art-tbl tr:hover td { background: rgba(255,255,255,0.02); }
.art-agent { font-weight: 600; color: var(--text-primary); }
.art-pill {
  display: inline-block; font-size: var(--fs-small); font-weight: 700;
  letter-spacing: 0.04em; padding: 3px 10px; border-radius: 12px; border: 1px solid;
}
.art-pill-buy       { color: #34d399; background: rgba(52,211,153,0.14); border-color: rgba(52,211,153,0.4); }
.art-pill-overweight{ color: #86efac; background: rgba(134,239,172,0.14); border-color: rgba(134,239,172,0.4); }
.art-pill-hold      { color: #fbbf24; background: rgba(251,191,36,0.13); border-color: rgba(251,191,36,0.4); }
.art-pill-underweight{color: #fca5a5; background: rgba(252,165,165,0.13); border-color: rgba(252,165,165,0.4); }
.art-pill-sell      { color: #f87171; background: rgba(248,113,113,0.14); border-color: rgba(248,113,113,0.4); }
.art-pill-missing   { color: var(--text-muted); background: transparent; border-color: var(--border); font-style: italic; }
.art-role-interim { font-size: var(--fs-caption); color: var(--text-muted); opacity: 0.6; }
.art-role-final   { font-size: var(--fs-caption); font-weight: 700; color: var(--accent-pink); letter-spacing: 0.04em; }
.art-reason       { font-size: var(--fs-small); color: var(--text-secondary); }
.art-reason-empty { opacity: 0.35; }
.art-override-badge {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: var(--fs-caption); font-weight: 700; letter-spacing: 0.05em;
  padding: 3px 10px; border-radius: 12px;
  color: #fbbf24; background: rgba(245,158,11,0.15); border: 1px solid rgba(245,158,11,0.45);
  margin-left: 10px;
}

/* ── Print ── */
@media print {
  body { background: white; color: black; }
  .sidebar { display: none; }
  .layout { grid-template-columns: 1fr; }
  .section-card { break-inside: avoid; box-shadow: none; border: 1px solid #ddd; }
}
"""

JS = """
// Scroll-spy: highlight the active section in the horizontal nav
const sections = document.querySelectorAll('.section-card[id]');
const navItems = document.querySelectorAll('.nav-item');

function setActive(id) {
    navItems.forEach(item => {
        const on = item.getAttribute('href') === '#' + id;
        item.classList.toggle('active', on);
        if (on) {
            // keep the active chip visible in the horizontal scroll strip
            item.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
        }
    });
}

// Scroll-based spy: active = last section whose top has passed the trigger line.
// IntersectionObserver misfires on long sections (next section peeks in before
// user finishes reading current one, flipping the active tab prematurely).
const NAV_H = 60; // approx topnav height px
function updateActiveNav() {
    const trigger = NAV_H + 20;
    let best = null;
    sections.forEach(s => {
        const top = s.getBoundingClientRect().top;
        if (top <= trigger) {
            if (!best || top > best.top) best = { id: s.id, top };
        }
    });
    const id = best ? best.id : (sections[0] ? sections[0].id : null);
    if (id) setActive(id);
}
window.addEventListener('scroll', updateActiveNav, { passive: true });
updateActiveNav();

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

// ── Interactive bar+line charts (self-contained, no external lib) ──────────
const SVGNS = 'http://www.w3.org/2000/svg';
function fmtNum(v, big) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    if (big) {
        const a = Math.abs(v);
        if (a >= 1000) return (v / 1000).toLocaleString('en-US', {maximumFractionDigits: 1}) + 'K';
        return v.toLocaleString('en-US', {maximumFractionDigits: 0});
    }
    return v.toLocaleString('en-US', {maximumFractionDigits: 2});
}
function niceMax(v) {
    if (v <= 0) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / exp;
    let nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return nf * exp;
}
function el(tag, attrs, parent) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
}

function drawChart(box) {
    let spec;
    try { spec = JSON.parse(box.dataset.spec); } catch (e) { return; }
    box.innerHTML = '';
    const W = box.clientWidth || 360, H = spec.height || 290;
    const labels = spec.labels || [];
    const bars = spec.bars || [], lines = spec.lines || [], bands = spec.bands || [];
    const bigL = !!spec.bigLeft;
    const hasRight = !!spec.unitRight || bars.some(b => b.axis === 'right') || lines.some(l => l.axis === 'right');
    const PAD = {l: 52, r: hasRight ? 46 : 16, t: 42, b: (labels.length > 14 ? 62 : 46)};
    const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
    const n = labels.length;

    const leftSeries  = [...bars.filter(b => b.axis !== 'right'), ...lines.filter(l => l.axis !== 'right')];
    const rightSeries = [...bars.filter(b => b.axis === 'right'), ...lines.filter(l => l.axis === 'right')];

    function bounds(series, opt) {
        opt = opt || {};
        if (opt.min != null && opt.max != null) return [opt.min, opt.max];
        let vals = [];
        series.forEach(s => (s.data || []).forEach(v => { if (v != null) vals.push(v); }));
        if (!vals.length) return [0, 1];
        const dmax = Math.max(...vals), dmin = Math.min(...vals);
        if (opt.zero === false) {
            const pad = (dmax - dmin) * 0.10 || Math.abs(dmax) * 0.05 || 1;
            return [dmin - pad, dmax + pad];
        }
        let mx = niceMax(Math.max(Math.abs(dmax), Math.abs(dmin), 1));
        let mn = Math.min(0, dmin);
        if (mn < 0) mn = -niceMax(Math.abs(mn));
        return [mn, mx];
    }
    const [lMin, lMax] = bounds(leftSeries, {min: spec.leftMin, max: spec.leftMax, zero: spec.leftZero});
    const [rMin, rMax] = bounds(rightSeries, {zero: true});

    const ySvg = el('svg', {viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'xMidYMid meet',
        'font-family': 'Inter, system-ui, sans-serif'}, box);
    const yL = v => PAD.t + ih * (1 - (v - lMin) / (lMax - lMin || 1));
    const yR = v => PAD.t + ih * (1 - (v - rMin) / (rMax - rMin || 1));
    const band = iw / Math.max(n, 1);
    const xc = i => PAD.l + band * (i + 0.5);

    // Title
    if (spec.title) el('text', {x: PAD.l, y: 22, fill: '#e2e8f0', 'font-size': 13,
        'font-weight': 700}, ySvg).textContent = spec.title;
    if (spec.unitLeft) el('text', {x: PAD.l, y: 36, fill: '#5b6b85', 'font-size': 11}, ySvg)
        .textContent = 'đơn vị: ' + spec.unitLeft;

    // Gridlines + Y labels
    for (let p = 0; p <= 4; p++) {
        const v = lMin + (lMax - lMin) * p / 4, y = yL(v);
        el('line', {x1: PAD.l, y1: y, x2: PAD.l + iw, y2: y, stroke: '#1e293b',
            'stroke-width': 1, 'stroke-dasharray': '3 4'}, ySvg);
        el('text', {x: PAD.l - 7, y: y + 3.5, 'text-anchor': 'end', fill: '#5b6b85',
            'font-size': 11}, ySvg).textContent = fmtNum(v, bigL);
        if (hasRight) {
            const rv = rMin + (rMax - rMin) * p / 4;
            el('text', {x: PAD.l + iw + 7, y: y + 3.5, 'text-anchor': 'start', fill: '#7c6a4a',
                'font-size': 11}, ySvg).textContent = fmtNum(rv, false);
        }
    }
    if (lMin < 0) el('line', {x1: PAD.l, y1: yL(0), x2: PAD.l + iw, y2: yL(0),
        stroke: '#33455f', 'stroke-width': 1.4}, ySvg);

    // Reference bands (e.g. RSI 30/70)
    bands.forEach(b => {
        const y = yL(b.y);
        el('line', {x1: PAD.l, y1: y, x2: PAD.l + iw, y2: y, stroke: b.color || '#475569',
            'stroke-width': 1, 'stroke-dasharray': '5 4', opacity: 0.7}, ySvg);
        if (b.label) el('text', {x: PAD.l + iw - 2, y: y - 4, 'text-anchor': 'end',
            fill: b.color || '#64748b', 'font-size': 11}, ySvg).textContent = b.label;
    });

    // Grouped bars (per-series axis aware)
    const nb = bars.length;
    const grpW = band * (nb > 1 ? 0.6 : 0.74), barW = grpW / Math.max(nb, 1);
    bars.forEach((s, si) => {
        const yf = s.axis === 'right' ? yR : yL;
        const aMin = s.axis === 'right' ? rMin : lMin;
        const colors = Array.isArray(s.colors) ? s.colors : null;
        labels.forEach((_, i) => {
            const v = s.data[i];
            if (v == null) return;
            const bx = xc(i) - grpW / 2 + si * barW;
            const base = aMin < 0 ? yf(0) : (PAD.t + ih);
            const yt = yf(v);
            const hgt = Math.max(1.2, Math.abs(yt - base));
            const ry = Math.min(yt, base);
            el('rect', {x: bx + barW * 0.08, y: ry, width: barW * 0.84, height: hgt,
                rx: nb > 1 ? 3 : 1.5, fill: colors ? colors[i] : s.color,
                opacity: s.axis === 'right' ? 0.5 : 0.92}, ySvg);
        });
    });

    // Lines
    lines.forEach(s => {
        const yf = s.axis === 'right' ? yR : yL;
        const showDots = s.markers === true || (s.markers !== false && n <= 16);
        let d = '', started = false;
        const pts = [];
        labels.forEach((_, i) => {
            const v = s.data[i];
            if (v == null) { started = false; return; }
            const x = xc(i), y = yf(v);
            d += (started ? ' L' : ' M') + x.toFixed(1) + ' ' + y.toFixed(1);
            started = true;
            pts.push([x, y]);
        });
        if (d) {
            const p = el('path', {d: d.trim(), fill: 'none', stroke: s.color,
                'stroke-width': s.width || 2.4, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, ySvg);
            if (s.dash) p.setAttribute('stroke-dasharray', '6 4');
        }
        if (showDots) pts.forEach(([x, y]) => el('circle', {cx: x, cy: y, r: 3.2,
            fill: '#0c1322', stroke: s.color, 'stroke-width': 2}, ySvg));
    });

    // X labels (thin out when dense; rotate for readability)
    const step = Math.max(1, Math.ceil(n / 13));
    const rot = n > 14;
    labels.forEach((lab, i) => {
        if (i % step !== 0 && i !== n - 1) return;
        const x = xc(i), y = PAD.t + ih + (rot ? 12 : 16);
        const t = el('text', {x: x, y: y, fill: '#8295b0', 'font-size': 11}, ySvg);
        if (rot) {
            t.setAttribute('text-anchor', 'end');
            t.setAttribute('transform', `rotate(-45 ${x} ${y})`);
        } else {
            t.setAttribute('text-anchor', 'middle');
        }
        t.textContent = lab;
    });

    // Legend (top-right)
    const allS = [...bars.map(b => ({...b, _bar: true})), ...lines];
    let lx = PAD.l + iw, ly = 14;
    [...allS].reverse().forEach(s => {
        const tw = s.name.length * 6.0 + 16;
        lx -= tw;
        el('rect', {x: lx, y: ly - 8, width: 10, height: 10, rx: 2, fill: s.color}, ySvg);
        el('text', {x: lx + 14, y: ly, fill: '#94a3b8', 'font-size': 11}, ySvg).textContent = s.name;
        lx -= 10;
    });

    // Hover tooltip
    const tip = document.createElement('div');
    tip.className = 'vchart-tip';
    box.appendChild(tip);
    const guide = el('line', {x1: 0, y1: PAD.t, x2: 0, y2: PAD.t + ih, stroke: '#3b82f6',
        'stroke-width': 1, 'stroke-dasharray': '2 3', opacity: 0}, ySvg);
    labels.forEach((lab, i) => {
        const hit = el('rect', {x: PAD.l + band * i, y: PAD.t, width: band, height: ih,
            fill: 'transparent'}, ySvg);
        hit.style.cursor = 'crosshair';
        hit.addEventListener('mouseenter', () => {
            guide.setAttribute('x1', xc(i)); guide.setAttribute('x2', xc(i));
            guide.setAttribute('opacity', 0.6);
            let rows = '';
            allS.forEach(s => {
                const v = s.data[i];
                const u = s.axis === 'right' ? (spec.unitRight || '') : (s._bar || s.axis !== 'right' ? (spec.unitLeft || '') : '');
                rows += `<div class="tip-row"><span class="tip-dot" style="background:${s.color}"></span>`
                      + `<span>${s.name}</span><span class="tip-val">${fmtNum(v, bigL && s._bar)}</span></div>`;
            });
            tip.innerHTML = `<div class="tip-x">${lab}</div>${rows}`;
            tip.style.opacity = 1;
            const px = (xc(i) / W) * box.clientWidth;
            tip.style.left = Math.min(box.clientWidth - 150, Math.max(4, px + 10)) + 'px';
            tip.style.top = '38px';
        });
        hit.addEventListener('mouseleave', () => {
            tip.style.opacity = 0; guide.setAttribute('opacity', 0);
        });
    });
}

function drawAllCharts() { document.querySelectorAll('.vchart[data-spec]').forEach(drawChart); }
let _crt;
window.addEventListener('resize', () => { clearTimeout(_crt); _crt = setTimeout(drawAllCharts, 150); });
drawAllCharts();
"""


import html as _html

# Heading that opens the executive-summary block inside fundamentals_report
_EXEC_RE = re.compile(
    r"(^#{1,3}\s*📋[^\n]*\n.*?)(?=^#{1,3}\s|\Z)",
    re.DOTALL | re.MULTILINE,
)


def _extract_executive_summary(md: str) -> tuple[str, str]:
    """Pull the '📋 Tóm Tắt Đầu Tư' block out of fundamentals markdown.
    Returns (exec_block_md, remaining_md). Empty exec_block if not found."""
    if not md:
        return "", md
    m = _EXEC_RE.search(md)
    if not m:
        return "", md
    block = m.group(1).strip()
    # Drop the leading heading line itself — we render our own hero header
    block = re.sub(r"^#{1,3}\s*📋[^\n]*\n", "", block, count=1).strip()
    remaining = (md[:m.start()] + md[m.end():]).strip()
    return block, remaining


def _fnum(v, dec=1) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if dec == 0:
        return f"{v:,.0f}"
    return f"{v:,.{dec}f}"


def _spec_attr(spec: dict) -> str:
    """JSON spec → safe double-quoted HTML attribute."""
    return _html.escape(json.dumps(spec, ensure_ascii=False), quote=True)


def _build_hero_metrics(chart_data: dict, ticker: str) -> str:
    """BSR-style hero strip: Giá · P/E · P/B · ROE · LNST với số monospace,
    descriptor và mũi tên xanh/đỏ. '' nếu không đủ dữ liệu."""
    if not chart_data:
        return ""

    is_bank = chart_data.get("is_bank", False)
    years   = chart_data.get("years", []) or []
    price   = chart_data.get("latest_price")
    pe      = [v for v in (chart_data.get("pe") or [])]
    pb      = [v for v in (chart_data.get("pb") or [])]
    roe     = chart_data.get("roe_pct") or []
    npf     = chart_data.get("netprofit_bn") or []

    def last(lst):
        for v in reversed(lst):
            if v is not None:
                return v
        return None

    def median(lst):
        xs = sorted(v for v in lst if v is not None)
        if not xs:
            return None
        m = len(xs) // 2
        return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2

    cards = []

    def card(label, value, unit="", sub="", sub_cls="hm-flat", accent="#3b82f6"):
        unit_html = f'<span class="hm-unit">{unit}</span>' if unit else ""
        sub_html = f'<div class="hm-sub {sub_cls}">{sub}</div>' if sub else ""
        cards.append(
            f'<div class="hm-card" style="--hm-accent:{accent}">'
            f'<div class="hm-label">{label}</div>'
            f'<div class="hm-value">{value}{unit_html}</div>'
            f'{sub_html}</div>'
        )

    # Giá hiện tại
    if price is not None:
        card("Giá hiện tại", f"{price:,.1f}", "nghìn đ", accent="#3b82f6")

    # P/E
    pe_l, pe_med = last(pe), median(pe)
    if pe_l is not None:
        if pe_med and pe_l > pe_med * 1.1:
            sub, cls = "▲ Cao hơn TB 5N", "hm-down"
        elif pe_med and pe_l < pe_med * 0.9:
            sub, cls = "▼ Thấp hơn TB 5N", "hm-up"
        else:
            sub, cls = "◆ Quanh TB 5N", "hm-flat"
        card("P/E (TTM)", f"{pe_l:,.1f}", "x", sub, cls, accent="#a855f7")

    # P/B
    pb_l = last(pb)
    if pb_l is not None:
        yr = years[-1] if years else ""
        card(f"P/B {yr}", f"{pb_l:,.2f}", "x", "", "hm-flat", accent="#06b6d4")

    # ROE + xu hướng
    roe_l = last(roe)
    if roe_l is not None:
        roe_prev = None
        vals = [v for v in roe if v is not None]
        if len(vals) >= 2:
            roe_prev = vals[-2]
        if roe_prev is not None and roe_l > roe_prev + 0.3:
            sub, cls = f"↑ phục hồi từ {roe_prev:.1f}%", "hm-up"
        elif roe_prev is not None and roe_l < roe_prev - 0.3:
            sub, cls = f"↓ giảm từ {roe_prev:.1f}%", "hm-down"
        else:
            sub, cls = "→ đi ngang", "hm-flat"
        card("ROE", f"{roe_l:,.1f}", "%", sub, cls, accent="#10b981")

    # LNST năm gần nhất + YoY
    npf_l = last(npf)
    if npf_l is not None:
        vals = [v for v in npf if v is not None]
        yoy = ""
        cls = "hm-flat"
        if len(vals) >= 2 and vals[-2]:
            ch = (vals[-1] - vals[-2]) / abs(vals[-2]) * 100
            if ch >= 0:
                yoy, cls = f"↑ +{ch:,.0f}% YoY", "hm-up"
            else:
                yoy, cls = f"↓ {ch:,.0f}% YoY", "hm-down"
        yr = years[-1] if years else ""
        card(f"LNST {yr}", f"{npf_l:,.0f}", "tỷ", yoy, cls, accent="#22c55e")

    if len(cards) < 2:
        return ""

    sector = "Ngân hàng" if is_bank else "Doanh nghiệp"
    return (
        '<section class="hero-metrics-wrap">'
        f'<div class="hm-ticker"><span class="hm-tk">{ticker}</span>'
        f'<span class="hm-sector">{sector} · cập nhật {years[-1] if years else ""}</span></div>'
        f'<div class="hero-metrics">{"".join(cards)}</div>'
        '</section>'
    )


def _build_financial_block(chart_data: dict) -> str:
    """Build the top financial summary: 5Y + quarterly tables + interactive
    bar/line charts. Returns '' if there is no usable data."""
    if not chart_data:
        return ""

    is_bank = chart_data.get("is_bank", False)
    # sector_class: fallback cho chart_json cũ (đã cache trước khi thêm key này).
    sclass  = chart_data.get("sector_class") or ("BANK" if is_bank else "GENERIC")
    years   = chart_data.get("years", []) or []
    rev     = chart_data.get("revenue_bn", []) or []
    npf     = chart_data.get("netprofit_bn", []) or []
    eff     = chart_data.get("efficiency_pct", []) or []
    pe      = chart_data.get("pe", []) or []
    pb      = chart_data.get("pb", []) or []
    roe     = chart_data.get("roe_pct", []) or []
    roa     = chart_data.get("roa_pct", []) or []
    nim     = chart_data.get("nim_pct", []) or []
    npl     = chart_data.get("npl_pct", []) or []
    quarters = chart_data.get("quarters", []) or []
    q_rev    = chart_data.get("q_revenue_bn", []) or []
    q_pf     = chart_data.get("q_profit_bn", []) or []
    q_eff    = chart_data.get("q_efficiency_pct", []) or []

    if not years and not quarters:
        return ""

    def g(lst, i):
        return lst[i] if i < len(lst) and lst[i] is not None else None

    # Nhãn cột thu nhập/hiệu quả theo ngành (Task: BANK→TOI/CIR, SECURITIES→DT
    # hoạt động/Biên LN, GENERIC→Doanh thu/Biên LN). Giá trị cột hiệu quả lấy
    # TRỰC TIẾP từ chart_data (efficiency_pct/q_efficiency_pct) đã tính sẵn ở
    # fetcher — renderer chỉ format, không tính lại (CIR cần opex, renderer
    # không có field đó).
    rev_lbl, eff_lbl = {
        "BANK":       ("TOI", "CIR"),
        "SECURITIES": ("DT hoạt động", "Biên LN"),
    }.get(sclass, ("Doanh thu", "Biên LN"))

    # ── Tables ──────────────────────────────────────────────────────────
    yr_rows = ""
    for i, y in enumerate(years):
        if sclass == "BANK":
            yr_rows += (
                f"<tr><td>{y}</td>"
                f"<td>{_fnum(g(rev,i),0)}</td>"
                f"<td>{_fnum(g(npf,i),0)}</td>"
                f"<td>{_fnum(g(eff,i))}</td>"
                f"<td>{_fnum(g(roe,i))}</td>"
                f"<td>{_fnum(g(roa,i),2)}</td>"
                f"<td>{_fnum(g(nim,i),2)}</td>"
                f"<td>{_fnum(g(npl,i),2)}</td>"
                f"<td>{_fnum(g(pe,i),1)}</td>"
                f"<td>{_fnum(g(pb,i),2)}</td></tr>"
            )
        else:
            yr_rows += (
                f"<tr><td>{y}</td>"
                f"<td>{_fnum(g(rev,i),0)}</td>"
                f"<td>{_fnum(g(npf,i),0)}</td>"
                f"<td>{_fnum(g(eff,i))}</td>"
                f"<td>{_fnum(g(roe,i))}</td>"
                f"<td>{_fnum(g(roa,i),2)}</td>"
                f"<td>{_fnum(g(pe,i),1)}</td>"
                f"<td>{_fnum(g(pb,i),2)}</td></tr>"
            )
    if sclass == "BANK":
        yr_head = (f"<tr><th>Năm</th><th>{rev_lbl}</th><th>LNST</th><th>{eff_lbl}</th>"
                  "<th>ROE</th><th>ROA</th><th>NIM</th><th>NPL</th><th>P/E</th><th>P/B</th></tr>")
    else:
        yr_head = f"<tr><th>Năm</th><th>{rev_lbl}</th><th>LNST</th><th>{eff_lbl}</th><th>ROE</th><th>ROA</th><th>P/E</th><th>P/B</th></tr>"

    q_rows = ""
    for i, q in enumerate(quarters):
        q_rows += (
            f"<tr><td>{q}</td>"
            f"<td>{_fnum(g(q_rev,i),0)}</td>"
            f"<td>{_fnum(g(q_pf,i),0)}</td>"
            f"<td>{_fnum(g(q_eff,i))}</td></tr>"
        )
    q_head = f"<tr><th>Quý</th><th>{rev_lbl}</th><th>LNST</th><th>{eff_lbl}</th></tr>"
    cir_note = (
        '<p class="fin-note">⚠ CIR = chi phí hoạt động / TOI — <b>THẤP là tốt</b> '
        '(ngược chiều Biên LN thông thường).</p>' if sclass == "BANK" else ""
    )

    tables_html = ""
    if yr_rows:
        tables_html += (
            f'<div class="fin-table-wrap"><h4>Kết quả tài chính · {len(years)} năm '
            '<span style="font-weight:400;color:var(--text-muted)">(tỷ đồng · %)</span></h4>'
            f'<table class="fin-tbl"><thead>{yr_head}</thead><tbody>{yr_rows}</tbody></table></div>'
        )
    if q_rows:
        tables_html += (
            f'<div class="fin-table-wrap"><h4>Kết quả theo quý · {len(quarters)} quý '
            '<span style="font-weight:400;color:var(--text-muted)">(tỷ đồng · %)</span></h4>'
            f'<table class="fin-tbl"><thead>{q_head}</thead><tbody>{q_rows}</tbody></table>'
            f'{cir_note}</div>'
        )

    # ── Charts (bar + line combo, BSR-like colors) ──────────────────────
    specs: list[dict] = []
    if years and any(v is not None for v in rev):
        specs.append({
            "title": f"{rev_lbl} & LNST · {len(years)} năm",
            "labels": years, "unitLeft": "tỷ đồng", "unitRight": "%", "bigLeft": True,
            "bars": [
                {"name": rev_lbl, "data": rev, "color": "#3b82f6"},
                {"name": "LNST",  "data": npf, "color": "#22c55e"},
            ],
            "lines": [{"name": eff_lbl, "data": eff, "color": "#f59e0b", "axis": "right"}],
        })
    if is_bank and years and any(v is not None for v in nim):
        specs.append({
            "title": f"NIM & NPL · {len(years)} năm",
            "labels": years, "unitLeft": "%",
            "bars": [], "lines": [
                {"name": "NIM", "data": nim, "color": "#f59e0b"},
                {"name": "NPL", "data": npl, "color": "#f43f5e"},
            ],
        })
    elif years and any(v is not None for v in pe):
        specs.append({
            "title": f"Định giá P/E & P/B · {len(years)} năm",
            "labels": years, "unitLeft": "lần",
            "bars": [], "lines": [
                {"name": "P/E", "data": pe, "color": "#a855f7"},
                {"name": "P/B", "data": pb, "color": "#06b6d4"},
            ],
        })
    if years and any(v is not None for v in roe):
        specs.append({
            "title": f"Hiệu quả sinh lời ROE & ROA · {len(years)} năm",
            "labels": years, "unitLeft": "%",
            "bars": [], "lines": [
                {"name": "ROE", "data": roe, "color": "#10b981"},
                {"name": "ROA", "data": roa, "color": "#38bdf8"},
            ],
        })
    if quarters and any(v is not None for v in q_rev):
        specs.append({
            "title": f"Xu hướng theo quý · {len(quarters)} quý",
            "labels": quarters, "unitLeft": "tỷ đồng", "unitRight": "%", "bigLeft": True,
            "bars": [
                {"name": rev_lbl, "data": q_rev, "color": "#3b82f6"},
                {"name": "LNST",  "data": q_pf,  "color": "#22c55e"},
            ],
            "lines": [{"name": eff_lbl, "data": q_eff, "color": "#f59e0b", "axis": "right"}],
        })

    charts_html = ""
    if specs:
        cells = "".join(f'<div class="vchart" data-spec="{_spec_attr(s)}"></div>' for s in specs)
        charts_html = f'<div class="charts-grid">{cells}</div>'

    if not tables_html and not charts_html:
        return ""

    return (
        '<section class="fin-block">'
        '<div class="fin-block-head"><span class="fb-icon">📊</span>'
        '<span class="fb-title">Tổng Hợp Kết Quả Tài Chính</span></div>'
        '<div class="fin-block-body">'
        f'<div class="fin-tables">{tables_html}</div>'
        f'{charts_html}'
        '</div></section>'
    )


def _build_technical_block(td: dict) -> str:
    """BSR-style technical section: score card + price/volume/MA chart, RSI, correlation."""
    if not td or not td.get("weeks"):
        return ""

    weeks = td["weeks"]
    sym = td.get("symbol", "")
    score = td.get("score", 0)
    smax = td.get("score_max", 6)
    scolor = td.get("signal_color", "#94a3b8")
    signal = td.get("signal", "—")

    def cls_for(v, good_high=True):
        if v is None:
            return "hm-flat"
        return ("hm-up" if (v >= 0) == good_high else "hm-down")

    # ── Score card + metric mini-cards ──────────────────────────────────
    rsi = td.get("rsi")
    rsi_lbl = td.get("rsi_label", "")
    rsi_cls = "hm-down" if rsi_lbl in ("QUÁ MUA", "TIÊU CỰC") else "hm-up" if rsi_lbl in ("TÍCH CỰC", "QUÁ BÁN") else "hm-flat"
    macd_trend = td.get("macd_trend", "")
    macd_cls = "hm-up" if macd_trend == "TÍCH CỰC" else "hm-down"
    perf = td.get("perf_1y")
    perf_vni = td.get("perf_vni")
    beta = td.get("beta")
    alpha = td.get("alpha")

    def metric(label, value, sub, vcls, accent):
        return (
            f'<div class="ts-metric" style="--hm-accent:{accent}">'
            f'<div class="ts-m-label">{label}</div>'
            f'<div class="ts-m-value {vcls}">{value}</div>'
            f'<div class="ts-m-sub">{sub}</div></div>'
        )

    metrics = ""
    if rsi is not None:
        metrics += metric("RSI 14", f"{rsi:.1f}", rsi_lbl, rsi_cls, "#a855f7")
    if macd_trend:
        metrics += metric("MACD", macd_trend, f"Hist {td.get('macd_hist','')}", macd_cls, "#f43f5e")
    if perf is not None:
        sub = f"vs VNINDEX {perf_vni:+.0f}%" if perf_vni is not None else ""
        metrics += metric("1 năm", f"{perf:+.0f}%", sub, cls_for(perf), "#14b8a6")
    if beta is not None:
        sub = f"Alpha {alpha:+.0f}%" if alpha is not None else ""
        metrics += metric("Beta vs VNI", f"{beta:.2f}", sub, "hm-flat", "#f59e0b")

    note = td.get("note", "")
    score_card = (
        '<div class="tech-score-card">'
        '<div class="ts-main">'
        '<div class="ts-label">Technical Score</div>'
        '<div class="ts-score-row">'
        f'<span class="ts-score" style="color:{scolor}">{score}<span class="ts-score-max">/{smax}</span></span>'
        f'<span class="ts-pill" style="color:{scolor}; border-color:{scolor}; '
        f'background:color-mix(in srgb,{scolor} 16%,transparent)">{signal}</span>'
        '</div>'
        f'<div class="ts-note">{note}</div>'
        '</div>'
        f'<div class="ts-metrics">{metrics}</div>'
        '</div>'
    )

    # ── Charts ──────────────────────────────────────────────────────────
    vol = td.get("volume", [])
    vol_up = td.get("vol_up", [])
    vol_colors = ["#14b8a6" if (i < len(vol_up) and vol_up[i]) else "#fb7185" for i in range(len(vol))]

    price_lines = [
        {"name": "Giá", "data": td.get("close", []), "color": "#ec4899", "markers": False, "width": 2.6},
    ]
    if any(v is not None for v in td.get("ma10", [])):
        price_lines.append({"name": "MA10", "data": td.get("ma10", []), "color": "#38bdf8", "markers": False, "width": 1.5})
    price_lines.append({"name": "MA20", "data": td.get("ma20", []), "color": "#a855f7", "markers": False, "dash": True, "width": 1.8})
    if any(v is not None for v in td.get("ma50", [])):
        price_lines.append({"name": "MA50", "data": td.get("ma50", []), "color": "#f59e0b", "markers": False, "width": 1.5})

    price_spec = {
        "title": f"Biểu đồ giá 52 tuần · Giá + Volume + MA10/20/50",
        "height": 360, "labels": weeks,
        "unitLeft": "nghìn đ", "unitRight": "triệu CP", "leftZero": False,
        "bars": [{"name": "Volume", "data": vol, "axis": "right", "color": "#14b8a6", "colors": vol_colors}],
        "lines": price_lines,
    }
    # MACD histogram colors (xanh dương / đỏ theo dấu)
    macdh = td.get("macd_hist_series", [])
    macdh_colors = ["#22c55e" if (v is not None and v >= 0) else "#f43f5e" for v in macdh]
    rsi_spec = {
        "title": "RSI (14) & MACD histogram", "labels": weeks,
        "leftMin": 0, "leftMax": 100, "unitRight": "MACD",
        "bands": [
            {"y": 70, "color": "#f43f5e", "label": "70 · quá mua"},
            {"y": 30, "color": "#22c55e", "label": "30 · quá bán"},
        ],
        "bars": [{"name": "MACD hist", "data": macdh, "axis": "right", "color": "#64748b", "colors": macdh_colors}],
        "lines": [{"name": "RSI", "data": td.get("rsi_series", []), "color": "#ec4899", "markers": False, "width": 2.4}],
    }
    corr_spec = {
        "title": "Tương quan vs VNINDEX & VN30", "labels": weeks,
        "unitLeft": "chuẩn hoá 100", "leftZero": False,
        "lines": [
            {"name": sym, "data": td.get("tkr_norm", []), "color": "#ec4899", "markers": False, "width": 2.6},
            {"name": "VNINDEX", "data": td.get("vni_norm", []), "color": "#06b6d4", "markers": False, "width": 2},
            {"name": "VN30", "data": td.get("vn30_norm", []), "color": "#a855f7", "markers": False, "width": 2, "dash": True},
        ],
    }

    price_html = f'<div class="vchart" data-spec="{_spec_attr(price_spec)}"></div>'
    grid_html = (
        '<div class="charts-grid">'
        f'<div class="vchart" data-spec="{_spec_attr(rsi_spec)}"></div>'
        f'<div class="vchart" data-spec="{_spec_attr(corr_spec)}"></div>'
        '</div>'
    )

    return (
        '<div class="tech-block">'
        f'{score_card}'
        f'<div class="tech-price">{price_html}</div>'
        f'{grid_html}'
        '</div>'
    )


_ALL_RATINGS = r'(Strong\s+Buy|Strong\s+Sell|Buy|Overweight|Hold|Underweight|Sell|Neutral)'
_RATING_RE = {
    "investment_plan":        _re.compile(r'\*\*Recommendation\*\*\s*:\s*' + _ALL_RATINGS, _re.IGNORECASE),
    "trader_investment_plan": _re.compile(r'\*\*Action\*\*\s*:\s*' + _ALL_RATINGS, _re.IGNORECASE),
    "final_trade_decision":   _re.compile(r'\*\*Rating\*\*\s*:\s*' + _ALL_RATINGS, _re.IGNORECASE),
}

_PILL_CLS = {
    "buy": "art-pill-buy",
    "overweight": "art-pill-overweight",
    "hold": "art-pill-hold",
    "underweight": "art-pill-underweight",
    "sell": "art-pill-sell",
    "strong buy": "art-pill-buy",
    "strong sell": "art-pill-sell",
    "neutral": "art-pill-hold",
}

def _rating_pill(value: str | None) -> str:
    """Return a styled <span> for a rating value, or 'chưa có dữ liệu'."""
    if not value:
        return '<span class="art-pill art-pill-missing">chưa có dữ liệu</span>'
    cls = _PILL_CLS.get(value.lower(), "art-pill-hold")
    return f'<span class="art-pill {cls}">{_html.escape(value)}</span>'


def _rating_direction(r: str | None) -> str | None:
    if not r:
        return None
    r = r.strip().upper()
    if r in ("BUY", "OVERWEIGHT", "STRONG BUY"):
        return "positive"
    if r in ("SELL", "UNDERWEIGHT", "STRONG SELL"):
        return "negative"
    if r in ("HOLD", "NEUTRAL"):
        return "neutral"
    return None


# E1: label map for prefixing intermediate-phase signal/recommendation/action fields
_E1_LABEL_MAP: dict[str, tuple[str, str]] = {
    "market_report":          ("Signal",         "Market Analyst"),
    "sentiment_report":       ("Signal",         "Sentiment Analyst"),
    "news_report":            ("Signal",         "News Analyst"),
    "fundamentals_report":    ("Signal",         "Fundamentals Analyst"),
    "investment_plan":        ("Recommendation", "Research Team"),
    "trader_investment_plan": ("Action",         "Trader"),
    # final_trade_decision (Phase V) keeps **Rating**: as authoritative label
}


def _prefix_section_rating(key: str, text: str) -> str:
    """E1: rewrite intermediate-phase signal/rec/action field labels to include agent name."""
    entry = _E1_LABEL_MAP.get(key)
    if not entry:
        return text
    field, agent = entry
    return _re.sub(
        rf'(?m)^\*\*{field}\*\*(\s*:\s*)',
        lambda m: f'**Đề xuất từ {agent}**{m.group(1)}',
        text,
    )


# E3: extract ≤15-word reason from structured output fields in each section
_REASON_RE: dict[str, _re.Pattern] = {
    "market_report":          _re.compile(r'\*\*Signal\*\*\s*:\s*\S+\s*[—\-]\s*(.+)', _re.IGNORECASE),
    "sentiment_report":       _re.compile(r'\*\*Signal\*\*\s*:\s*\S+\s*[—\-]\s*(.+)', _re.IGNORECASE),
    "news_report":            _re.compile(r'\*\*Signal\*\*\s*:\s*\S+\s*[—\-]\s*(.+)', _re.IGNORECASE),
    "fundamentals_report":    _re.compile(r'\*\*Signal\*\*\s*:\s*\S+\s*[—\-]\s*(.+)', _re.IGNORECASE),
    "investment_plan":        _re.compile(r'\*\*Rationale\*\*\s*:\s*(.+)', _re.IGNORECASE),
    "trader_investment_plan": _re.compile(r'\*\*Reasoning\*\*\s*:\s*(.+)', _re.IGNORECASE),
    "final_trade_decision":   _re.compile(r'\*\*Executive\s+Summary\*\*\s*:\s*(.+)', _re.IGNORECASE),
}


_RM_CONCLUSION_RE = _re.compile(
    r'Kết\s+luận(?:\s+cuối\s+cùng)?\s*:\*{0,2}\s*(?:UNDERWEIGHT|OVERWEIGHT|BUY|SELL|HOLD)\s+\S+\.\s*(.+)',
    _re.IGNORECASE
)


def _short_reason(text: str | None, max_chars: int = 130) -> str | None:
    """Truncate to first sentence (≤max_chars). Used for long Phase-II/III/V reasons."""
    if not text:
        return None
    text = text.strip()
    m = _re.search(r'(?<=[.!?])\s', text)
    if m and m.start() <= max_chars + 40:
        snippet = text[:m.start()].strip()
    else:
        snippet = text
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars].rstrip() + '…'
    return snippet or None


def _extract_reason(key: str, sections: dict) -> str:
    """Extract a ≤15-word reason from a section's structured output fields."""
    text = sections.get(key, "")
    if not text:
        return ""
    pat = _REASON_RE.get(key)
    if not pat:
        return ""
    m = pat.search(text)
    if not m:
        # RM freetext fallback: "Kết luận cuối cùng: UNDERWEIGHT VPB. Lý do..."
        if key == "investment_plan":
            m2 = _RM_CONCLUSION_RE.search(text)
            if m2:
                words = m2.group(1).strip().split()
                snippet = " ".join(words[:15])
                return snippet + ("…" if len(words) > 15 else "")
        return ""
    words = m.group(1).strip().split()
    snippet = " ".join(words[:15])
    return snippet + ("…" if len(words) > 15 else "")


_ANALYST_SIGNAL_RE = _re.compile(r'\*\*Signal\*\*\s*:\s*' + _ALL_RATINGS, _re.IGNORECASE)
_SENTIMENT_SIGNAL_RE = _re.compile(
    r'\*\*Overall\s+Sentiment\*\*[^:]*:\s*\*{0,2}(' +
    r'Bullish|Mildly Bullish|Neutral|Mixed|Mildly Bearish|Bearish' +
    r')\*{0,2}', _re.IGNORECASE
)
_SENTIMENT_TO_RATING = {
    "bullish": "Overweight", "mildly bullish": "Overweight",
    "neutral": "Hold", "mixed": "Hold",
    "mildly bearish": "Underweight", "bearish": "Sell",
}


def _extract_phase1_rating(text: str, is_sentiment: bool = False) -> str | None:
    """Extract rating from a Phase-I analyst section text.

    Used as fallback when agent_ratings is not available (e.g. render-only mode).
    Each analyst prepends **Signal**: X — ... via render_analyst_signal(), so this
    is reliable when structured output succeeded.
    """
    if not text:
        return None
    if is_sentiment:
        m = _SENTIMENT_SIGNAL_RE.search(text)
        if m:
            return _SENTIMENT_TO_RATING.get(m.group(1).lower(), m.group(1).title())
        return None
    m = _ANALYST_SIGNAL_RE.search(text)
    return m.group(1) if m else None


def _build_agent_rating_table(sections: dict[str, str], agent_ratings: dict | None) -> str:
    """E3: build the agent-rating summary table HTML.

    Phase-I ratings: prefer agent_ratings dict (set during live pipeline run);
    fall back to parsing **Signal**: X from the section text (available in
    render-only / recover mode where agent_ratings is not persisted).
    Phase-II/III/V ratings come from structured-output header lines.
    """
    ar = agent_ratings or {}

    # Phase-I: live structured extraction, fallback to section-text parse
    market_r  = ar.get("market")       or _extract_phase1_rating(sections.get("market_report", ""))
    news_r    = ar.get("news")         or _extract_phase1_rating(sections.get("news_report", ""))
    funds_r   = ar.get("fundamentals") or _extract_phase1_rating(sections.get("fundamentals_report", ""))

    # Phase-II/III/V: parse guaranteed structured output headers
    def _extract(key):
        text = sections.get(key, "")
        if not text:
            return None
        m = _RATING_RE.get(key, _re.compile(r"(?!x)x")).search(text)
        if m:
            return m.group(1)
        # Trader freetext fallback: DeepSeek Flash often returns Vietnamese prose
        # instead of structured output (e.g. "FINAL TRANSACTION PROPOSAL: **MUA**").
        if key == "trader_investment_plan":
            _VN_EN = {"MUA": "Buy", "BÁN": "Sell", "GIỮ NGUYÊN": "Hold", "NẮM GIỮ": "Hold", "GIỮ": "Hold"}
            # 1. Search for the FINAL TRANSACTION PROPOSAL line anywhere in text
            final_m = _re.search(
                r'FINAL\s+TRANSACTION\s+PROPOSAL\s*:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$',
                text, _re.MULTILINE | _re.IGNORECASE
            )
            if final_m:
                word = final_m.group(1).strip()
                return _VN_EN.get(word.upper(), word.title())
            # 2. Headline scan (first 300 chars) with Vietnamese → English normalization
            head = text[:300]
            head = _re.sub(r'\bMUA\b', 'BUY', head, flags=_re.IGNORECASE)
            head = _re.sub(r'\bBÁN\b', 'SELL', head, flags=_re.IGNORECASE)
            head = _re.sub(r'\b(?:NẮM\s*GIỮ|GIỮ\s*NGUYÊN|GIỮ)\b', 'HOLD', head, flags=_re.IGNORECASE)
            sig = detect_signal(head)[3]
            return sig if sig not in ("UNKNOWN", "PENDING") else None
        # RM freetext fallback: structured output failed, DeepSeek Pro returned Vietnamese prose.
        # Try without bold markers, then scan last 800 chars (conclusion area).
        if key == "investment_plan":
            m2 = _re.search(r'Recommendation\s*:\s*' + _ALL_RATINGS, text, _re.IGNORECASE)
            if m2:
                return m2.group(1)
            sig = detect_signal(text[-800:])[3]
            return sig if sig not in ("UNKNOWN", "PENDING") else None
        return None

    rm_r     = ar.get("rm")     or _extract("investment_plan")
    trader_r = ar.get("trader") or _extract("trader_investment_plan")
    pm_r     = ar.get("pm")     or _extract("final_trade_decision")

    # Chỉ render dòng cho agent CÓ MẶT trong run: section tồn tại & non-empty
    # (Trader vắng ở pipeline_mode="rating" → không có dòng "chưa có dữ liệu").
    # PM luôn hiển thị (là final signal của mọi run).
    _all_rows = [
        ("📈", "Market Analyst",        market_r,  False, "market_report",          ar.get("market_reason")),
        ("📰", "News Analyst",          news_r,    False, "news_report",            ar.get("news_reason")),
        ("🏦", "Fundamentals Analyst",  funds_r,   False, "fundamentals_report",    ar.get("fundamentals_reason")),
        ("🔬", "Research Manager",      rm_r,      False, "investment_plan",        ar.get("rm_reason")),
        ("⚡", "Trader",               trader_r,  False, "trader_investment_plan", ar.get("trader_reason")),
        ("🎯", "Portfolio Manager",     pm_r,      True,  "final_trade_decision",   ar.get("pm_reason")),
    ]
    rows = [
        r for r in _all_rows
        if r[3] or bool(sections.get(r[4], "").strip())  # is_pm hoặc section có mặt
    ]

    # PM override đa số: mẫu số Y = số agent (không tính PM) THỰC TẾ có khuyến nghị
    # trong run này (không hardcode 5). Badge khi PM ngược hướng với đa số của Y.
    others = [
        rating for (_i, _n, rating, is_pm, _k, _r) in rows
        if not is_pm and _rating_direction(rating) is not None
    ]
    pm_dir = _rating_direction(pm_r)
    override_badge = ""
    if pm_dir is not None and others:
        disagree = sum(1 for r in others if _rating_direction(r) != pm_dir)
        if disagree * 2 > len(others):  # đa số thực sự
            override_badge = (
                f'<span class="art-override-badge">⚠ PM override đa số ({disagree}/{len(others)})</span>'
            )

    tbody = ""
    for icon, name, rating, is_pm, sec_key, reason_override in rows:
        tr_cls = ' class="art-pm"' if is_pm else ""
        role_badge = (
            '<span class="art-role-final">Final Signal</span>'
            if is_pm else
            '<span class="art-role-interim">tạm</span>'
        )
        reason = _short_reason(reason_override) or _extract_reason(sec_key, sections)
        reason_html = (
            f'<span class="art-reason">{_html.escape(reason)}</span>'
            if reason else
            '<span class="art-reason art-reason-empty">—</span>'
        )
        tbody += (
            f"<tr{tr_cls}>"
            f"<td><span class='art-agent'>{icon} {_html.escape(name)}</span> {role_badge}</td>"
            f"<td>{_rating_pill(rating)}</td>"
            f"<td>{reason_html}</td>"
            "</tr>\n"
        )

    return (
        '<section class="art-wrap">'
        '<div class="art-head">'
        '<span class="art-icon">🗳</span>'
        '<span class="art-title">Tổng Hợp Khuyến Nghị Toàn Pipeline</span>'
        f'{override_badge}'
        '</div>'
        '<div class="art-body">'
        '<table class="art-tbl"><thead>'
        '<tr><th>Agent</th><th>Khuyến nghị</th><th>Tóm tắt lý do</th></tr>'
        '</thead><tbody>'
        f'{tbody}'
        '</tbody></table>'
        '</div>'
        '</section>'
    )


def build_html(ticker: str, analysis_date: str, sections: dict[str, str], generated_at: str,
               model_info: dict | None = None, cost_str: str | None = None,
               agent_ratings: dict | None = None) -> str:
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

    # G2: extract Conviction label from PM structured output
    _conv_m = _re.search(
        r'\*\*Conviction\*\*\s*:\s*(CAO|TRUNG\s+BÌNH|THẤP)',
        sections.get("final_trade_decision", ""),
        _re.IGNORECASE,
    )
    conviction_label = _re.sub(r"\s+", " ", _conv_m.group(1).upper()) if _conv_m else ""
    _CONV_COLOR = {"CAO": "#10b981", "TRUNG BÌNH": "#f59e0b", "THẤP": "#ef4444"}
    conviction_html = (
        f'<div class="sig-conviction" style="color:{_CONV_COLOR.get(conviction_label, signal_fg)}">'
        f'Conviction: {_html.escape(conviction_label)}'
        f'</div>'
        if conviction_label else ""
    )

    # E3: Agent rating summary table (Phase-I from agent_ratings, Phase-II/III/V from text)
    agent_rating_table_html = _build_agent_rating_table(sections, agent_ratings)

    # ── Pre-process fundamentals: lift chart data + executive summary to top ──
    work_sections = dict(sections)
    fin_chart_data: dict = {}
    exec_hero_html = ""
    if work_sections.get("fundamentals_report"):
        _f = work_sections["fundamentals_report"]
        _f, fin_chart_data = _extract_vn_chart_data(_f)
        _exec_md, _f = _extract_executive_summary(_f)
        work_sections["fundamentals_report"] = _f
        if _exec_md:
            # Sync "Khuyến nghị" line to PM's final signal so exec summary
            # doesn't contradict the Final Signal badge below it.
            _pm_signal = detect_signal(sections.get("final_trade_decision", ""))[3]
            if _pm_signal not in ("UNKNOWN", "PENDING"):
                _exec_md = _re.sub(
                    r'(\*{0,2}Khuyến\s+nghị\*{0,2}\s*:\s*)[^\n]+',
                    lambda m: m.group(1) + _pm_signal,
                    _exec_md,
                    count=1,
                )
            exec_hero_html = (
                '<section class="exec-hero">'
                '<div class="exec-hero-head"><span class="eh-icon">📋</span>'
                '<span class="eh-title">Tóm Tắt Đầu Tư</span></div>'
                f'<div class="exec-hero-body"><div class="md-content">{md_to_html(_exec_md)}</div></div>'
                '</section>'
            )
    fin_block_html = _build_financial_block(fin_chart_data)
    hero_metrics_html = _build_hero_metrics(fin_chart_data, ticker)

    # ── Pre-process market report: lift technical chart data ──────────────
    pre_charts: dict[str, str] = {}
    if work_sections.get("market_report"):
        _m, _tech = _extract_vn_tech_data(work_sections["market_report"])
        work_sections["market_report"] = _m
        tech_html = _build_technical_block(_tech)
        if tech_html:
            pre_charts["market_report"] = tech_html

    # ── Reconciliation validator (A7/A8) ──────────────────────────────────
    # Gate (block + regenerate) sống ở main.py; build_html chỉ render banner
    # cảnh báo (chỉ hiện khi vào dev/warn mode, vì production đã chặn trước render).
    _val_warnings = validate_report(work_sections, fin_chart_data)
    if _val_warnings:
        print("[validate_report] %d cảnh báo:\n  - %s" % (
            len(_val_warnings), "\n  - ".join(_val_warnings)), file=sys.stderr)
    validator_banner = ""
    if _val_warnings:
        _items = "".join(f"<li>{_inline_md(w)}</li>" for w in _val_warnings[:12])  # A9
        validator_banner = (
            '<div class="validator-banner"><strong>⚠ Cảnh báo nhất quán dữ liệu '
            f'({len(_val_warnings)})</strong><ul>{_items}</ul></div>'
        )

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

    # ── Horizontal sticky nav ─────────────────────────────────────
    nav_html = '<nav class="topnav">'
    for key, content in work_sections.items():
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
    nav_html += '</nav>'

    # ── Section cards ─────────────────────────────────────────────
    cards_html = ""
    for key, content in work_sections.items():
        if not content or key not in SECTION_META:
            continue
        meta = SECTION_META[key]

        # Pre-built charts (e.g. market technical block); fundamentals charts lifted to top
        charts_block = pre_charts.get(key, "")

        # News digest: highlight numbers + sentiment badges
        if key == "news_report":
            content = enhance_news_digest(content)

        content = _prefix_section_rating(key, content)  # E1: label intermediate ratings
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
    {charts_block}
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

    # ── Model info row ────────────────────────────────────────────
    if model_info:
        chips = []
        if model_info.get("deep_think_llm"):
            chips.append(f'<span class="model-chip"><span class="chip-label">Deep:</span>{model_info["deep_think_llm"]}</span>')
        if model_info.get("quick_think_llm") and model_info.get("quick_think_llm") != model_info.get("deep_think_llm"):
            chips.append(f'<span class="model-chip"><span class="chip-label">Quick:</span>{model_info["quick_think_llm"]}</span>')
        if model_info.get("refine_llm"):
            chips.append(f'<span class="model-chip"><span class="chip-label">Refine:</span>{model_info["refine_llm"]}</span>')
        if cost_str:
            chips.append(f'<span class="cost-chip">💰 {cost_str}</span>')
        model_info_html = f'<div class="header-models">{"".join(chips)}</div>'
    else:
        model_info_html = ""

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
  <header class="report-header" style="border-color: color-mix(in srgb, {signal_fg} 34%, var(--border)); background: linear-gradient(90deg, var(--bg-card) 0%, var(--bg-card) 30%, color-mix(in srgb, {signal_fg} 26%, var(--bg-card)) 100%);">
    <div class="header-left">
      <div class="header-meta">
        <span class="header-badge">TradingAgents AI</span>
        <span class="header-date">🕐 Generated {generated_at}</span>
      </div>
      {model_info_html}
      <h1 class="report-title">
        <span class="ticker-highlight">{ticker}</span> Investment Report
      </h1>
      <p class="report-subtitle">
        Multi-agent LLM analysis covering market technicals, fundamentals, news sentiment,
        bull/bear debate, risk management and portfolio decision.
      </p>
    </div>
    <div class="header-signal-box" style="color:{signal_fg};">
      <div class="sig-emoji">{signal_emoji}</div>
      <div class="sig-label-sm">Final Signal</div>
      <div class="sig-value">{signal_label}</div>
      {conviction_html}
      <div class="sig-date">{analysis_date}</div>
    </div>
  </header>

  <!-- Workflow -->
  {workflow_html}

  <!-- Sticky section nav -->
  {nav_html}

  <!-- Hero key metrics + executive summary + financial snapshot (top of report) -->
  {validator_banner}
  {hero_metrics_html}
  {exec_hero_html}
  {agent_rating_table_html}
  {fin_block_html}

  <!-- Main content -->
  <main>
    {cards_html}
  </main>

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
