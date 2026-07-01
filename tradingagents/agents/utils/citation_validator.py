"""Ungrounded-citation detector for TradingAgents agent outputs.

Scans agent output text for mentions of CTCK names / analyst patterns
that do NOT appear in the context/tool-output provided to that agent call.

Usage (in agent code):
    from tradingagents.agents.utils.citation_validator import validate_citations
    cleaned, flagged = validate_citations(report, context_text, "Fundamentals", ticker)
    # flagged → list of UNGROUNDED_CITATION warning strings
    # cleaned → report with flagged sentences stripped
"""

import re
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ── Known VN securities companies (CTCK) ─────────────────────────────────────
CTCK_NAMES: List[str] = [
    "Vietcap",
    "SSI",
    "VNDirect",
    "VCBS",
    "BSC",
    "HSC",
    "MBS",
    "KIS",
    "ACBS",
    "FPTS",
    "VPS",
    "Mirae Asset",
    "KB Securities",
    "Yuanta",
    "MASVN",
    "Rồng Việt",
    "BVSC",
    "Agriseco",
    "Pinetree",
]

# CTCKs that are also stock tickers — skip if ticker == name to avoid false positives
_CTCK_AS_TICKERS = {"SSI", "HSC", "VCI", "BSC", "MBS"}

# Some tickers have a different display name from their ticker symbol
_TICKER_TO_CTCK_NAME: dict = {
    "VCI": "vietcap",  # VCI = CTCP Chứng khoán Vietcap
}

_CTCK_ALT = "|".join(re.escape(c) for c in CTCK_NAMES)

# Precompile one regex per CTCK name (word-boundary aware)
_CTCK_PATTERNS: List[Tuple[str, re.Pattern]] = [
    (name, re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE | re.UNICODE))
    for name in CTCK_NAMES
]

# ── Vietnamese-aware name fragment ────────────────────────────────────────────
_VN_UPPER = (
    r"[A-ZÁÀẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÉÈẺẼẸÊẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÚÙỦŨỤƯỨỪỬỮỰÝỲỶỸỴ"
    r"ĐĂÂÊÔƠƯ]"
)
_VN_WORD = (
    r"[A-ZÁÀẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÉÈẺẼẸÊẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÚÙỦŨỤƯỨỪỬỮỰÝỲỶỸỴ"
    r"a-záàảãạăắằẳẵặâấầẩẫậđéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵ]+"
)
_NAME = rf"{_VN_UPPER}{_VN_WORD}(?:\s+{_VN_UPPER}{_VN_WORD}){{0,3}}"

# ── Analyst / citation patterns ───────────────────────────────────────────────
_ANALYST_PATTERNS: List[re.Pattern] = [
    # Require at least 2 name words to avoid matching UI labels ("Analyst Buy", "Analyst Team")
    re.compile(
        rf"[Aa]nalyst\s+{_VN_UPPER}{_VN_WORD}\s+{_VN_UPPER}{_VN_WORD}(?:\s+{_VN_UPPER}{_VN_WORD}){{0,2}}",
        re.UNICODE,
    ),
    re.compile(rf"chuyên\s+gia\s+{_NAME}", re.UNICODE),
    re.compile(rf"theo\s+(?:ông|bà|anh|chị)\s+{_NAME}", re.UNICODE),
    re.compile(
        rf"(?:ông|bà)\s+{_NAME}\s+(?:từ|của|tại)\s+(?:{_CTCK_ALT})",
        re.UNICODE,
    ),
    re.compile(rf"(?:{_CTCK_ALT})\s+Research", re.IGNORECASE),
    re.compile(rf"theo\s+báo\s+cáo\s+(?:của\s+)?(?:{_CTCK_ALT})", re.IGNORECASE),
    # Date-tagged citation: CTCK + (DD/MM/YYYY) within ~80 chars
    re.compile(
        rf"(?:{_CTCK_ALT}).{{0,80}}?\(\d{{2}}/\d{{2}}/\d{{4}}\)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"\(\d{{2}}/\d{{2}}/\d{{4}}\).{{0,80}}?(?:{_CTCK_ALT})",
        re.IGNORECASE | re.DOTALL,
    ),
]


# ── Sentence extraction helpers ───────────────────────────────────────────────

def _sentence_span_around(text: str, pos: int) -> Tuple[int, int]:
    """Return (start, end) of the sentence-like unit containing position pos.

    Sentence boundaries: newline OR '.', '!', '?' followed by space/newline.
    """
    # Scan backward for sentence start
    start = pos
    while start > 0:
        ch = text[start - 1]
        if ch == "\n":
            break
        if ch in ".!?" and start >= 2 and text[start - 2] not in ".!?":
            break
        start -= 1

    # Scan forward for sentence end (include the terminator)
    end = pos
    while end < len(text):
        ch = text[end]
        if ch == "\n":
            end += 1
            break
        if ch in ".!?":
            end += 1
            break
        end += 1

    return start, end


def _remove_span(text: str, start: int, end: int) -> str:
    """Remove [start:end] from text, cleaning up double whitespace/newlines."""
    removed = text[:start] + text[end:]
    # Collapse triple+ newlines to double newline
    removed = re.sub(r"\n{3,}", "\n\n", removed)
    return removed


# ── Public API ────────────────────────────────────────────────────────────────

def validate_citations(
    output_text: str,
    context_text: str,
    agent_name: str = "",
    ticker: str = "",
) -> Tuple[str, List[str]]:
    """Detect and strip ungrounded citations from agent output.

    Args:
        output_text:  The agent's generated text (markdown).
        context_text: Concatenation of all tool outputs / context provided to
                      this agent call in the current turn.
        agent_name:   Label for log/flag messages (e.g. "Fundamentals").
        ticker:       Subject ticker symbol — prevents false positives when a
                      CTCK name matches the ticker (e.g. report about SSI).

    Returns:
        (cleaned_text, flagged_claims)
        cleaned_text has sentences containing ungrounded citations removed.
        flagged_claims is a list of UNGROUNDED_CITATION strings for the caller
        to log / surface.
    """
    if not output_text:
        return output_text, []

    context_lower = (context_text or "").lower()
    ticker_upper = (ticker or "").upper()
    also_skip = _TICKER_TO_CTCK_NAME.get(ticker_upper, "")

    # Accumulate (start, end) spans to remove — process in one pass, then remove
    # right-to-left so positions don't shift.
    spans_to_remove: List[Tuple[int, int, str, str]] = []  # (start, end, entity, sentence)
    text = output_text

    def _already_covered(pos: int) -> bool:
        return any(s <= pos < e for s, e, *_ in spans_to_remove)

    # ── Check CTCK names ──────────────────────────────────────────────────────
    for name, pat in _CTCK_PATTERNS:
        if name.upper() == ticker_upper:
            continue  # report subject — skip
        if also_skip and name.lower() == also_skip:
            continue  # company's own CTCK name (e.g. VCI → Vietcap)
        for m in pat.finditer(text):
            if _already_covered(m.start()):
                continue
            if name.lower() in context_lower:
                continue  # grounded
            sent_start, sent_end = _sentence_span_around(text, m.start())
            sentence_preview = text[sent_start:sent_end].strip()[:200].replace("\n", " ")
            spans_to_remove.append(
                (sent_start, sent_end, name, sentence_preview)
            )

    # ── Check analyst patterns ────────────────────────────────────────────────
    for pat in _ANALYST_PATTERNS:
        for m in pat.finditer(text):
            if _already_covered(m.start()):
                continue
            matched = m.group(0)
            if matched.lower() in context_lower:
                continue  # grounded
            sent_start, sent_end = _sentence_span_around(text, m.start())
            sentence_preview = text[sent_start:sent_end].strip()[:200].replace("\n", " ")
            spans_to_remove.append(
                (sent_start, sent_end, matched, sentence_preview)
            )

    if not spans_to_remove:
        return output_text, []

    # Build flagged list and remove spans right-to-left
    flagged: List[str] = []
    for start, end, entity, preview in sorted(spans_to_remove, key=lambda x: x[0], reverse=True):
        label = f"[{agent_name}] " if agent_name else ""
        flagged.append(
            f"{label}UNGROUNDED_CITATION: '{entity}' not found in context. "
            f"Stripped sentence: \"{preview}\""
        )
        logger.warning(flagged[-1])
        text = _remove_span(text, start, end)

    # Reverse so order matches document order
    flagged.reverse()
    return text, flagged


_AUDIT_UI_BLOCKLIST = frozenset({
    "team", "phase", "analysis", "technical", "social", "news", "market",
    "fundamental", "fundamentals", "sentiment", "risk", "portfolio",
    "buy", "sell", "hold", "underweight", "overweight", "bds",
})


def _looks_like_ui_label(matched_text: str) -> bool:
    """True if matched text is a pipeline UI label, not a real person/org citation."""
    words = matched_text.lower().split()
    return any(w in _AUDIT_UI_BLOCKLIST for w in words)


def scan_text_for_citations(text: str, ticker: str = "") -> List[str]:
    """Scan plain text for any CTCK/analyst mentions (no context check).

    Used for HTML report audit where original context is unavailable.
    Returns list of human-readable match strings.
    """
    ticker_upper = (ticker or "").upper()
    also_skip = _TICKER_TO_CTCK_NAME.get(ticker_upper, "")
    found: List[str] = []
    seen_positions = set()

    for name, pat in _CTCK_PATTERNS:
        if name.upper() == ticker_upper:
            continue
        if also_skip and name.lower() == also_skip:
            continue
        for m in pat.finditer(text):
            if m.start() in seen_positions:
                continue
            seen_positions.add(m.start())
            start, end = _sentence_span_around(text, m.start())
            snippet = text[start:end].strip()[:200].replace("\n", " ")
            found.append(f"CTCK '{name}' @ char {m.start()}: \"{snippet}\"")

    for pat in _ANALYST_PATTERNS:
        for m in pat.finditer(text):
            matched = m.group(0)
            if _looks_like_ui_label(matched):
                continue
            if m.start() in seen_positions:
                continue
            seen_positions.add(m.start())
            start, end = _sentence_span_around(text, m.start())
            snippet = text[start:end].strip()[:200].replace("\n", " ")
            found.append(f"PATTERN '{matched[:60]}' @ char {m.start()}: \"{snippet}\"")

    return found
