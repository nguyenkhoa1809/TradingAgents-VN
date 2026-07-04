"""Append-only markdown decision log for TradingAgents.

Multi-machine mode (memory_log_dir set): each machine owns exactly one file
(``trading_memory_<hostname>.md``) and is the ONLY writer of brand-new
entries into that file — this is what makes syncing the folder via OneDrive
(or any other file-sync tool) safe: a file that only ever gets appended to
by a single device is exactly the sync pattern cloud storage handles well.
Reads (``get_past_context``, ``get_pending_entries``) transparently merge
every ``trading_memory_*.md`` file found in the directory, so a machine
sees lessons/pending trades logged by every other machine. Resolving a
pending entry (``update_with_outcome`` / ``batch_update_with_outcomes``)
still writes in place into whichever file that entry actually lives in —
this is the one case where a machine may write into another machine's
file, but it only happens for a single alternating user (never truly
concurrent) and uses the same atomic temp+replace as before, so a partial
sync only risks a stale re-resolve next run, never corruption.
"""

import re
import socket
from pathlib import Path
from typing import List, Optional

from tradingagents.agents.utils.rating import parse_rating


class TradingMemoryLog:
    """Append-only markdown log of trading decisions and reflections."""

    # HTML comment: cannot appear in LLM prose output, safe as a hard delimiter
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    # Precompiled patterns — avoids re-compilation on every load_entries() call
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)
    # Detects a resolved-return field in old-format tags (e.g. "+0.0%", "-3.5%")
    _PCT_RE = re.compile(r"^[+\-]?\d+\.\d+%$")
    _SAFE_HOST_RE = re.compile(r"[^A-Za-z0-9_-]+")

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._log_path = None
        self._multi_dir = None  # set => multi-machine mode

        log_dir = cfg.get("memory_log_dir")
        if log_dir:
            self._multi_dir = Path(log_dir).expanduser()
            self._multi_dir.mkdir(parents=True, exist_ok=True)
            hostname = cfg.get("memory_log_hostname") or socket.gethostname()
            safe_host = self._SAFE_HOST_RE.sub("_", hostname).strip("_") or "machine"
            # Own file — the ONLY file this process ever appends brand-new entries to.
            self._log_path = self._multi_dir / f"trading_memory_{safe_host}.md"
        else:
            path = cfg.get("memory_log_path")
            if path:
                self._log_path = Path(path).expanduser()
                self._log_path.parent.mkdir(parents=True, exist_ok=True)

        # Optional cap on resolved entries. None disables rotation.
        self._max_entries = cfg.get("memory_log_max_entries")

    def _all_log_paths(self) -> List[Path]:
        """Every file to read from — all machines' logs in multi-machine mode."""
        if self._multi_dir:
            return sorted(self._multi_dir.glob("trading_memory_*.md"))
        return [self._log_path] if self._log_path else []

    # --- Write path (Phase A) ---

    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
        run_type: str = "unknown",
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call.

        Always writes to THIS machine's own file (``self._log_path``) — never
        another machine's file — so appends are always single-writer, even
        in multi-machine mode.

        ``run_type`` must be set explicitly by callers — "production" for
        real investment decisions, "debug" / "test" for exploratory runs.
        The default "unknown" acts as a safety net: get_past_context()
        excludes every entry that is not "production", so an unlabelled run
        cannot silently pollute future PM decisions.
        """
        if not self._log_path:
            return
        # Idempotency guard: skip if a pending entry with the same run_type
        # already exists ANYWHERE (another machine may have logged it first
        # for the same ticker+date). Match on run_type suffix so old-format
        # entries (4-field, no run_type) don't block new production entries,
        # and debug runs don't block production runs.
        prefix = f"[{trade_date} | {ticker} |"
        suffix = f"| {run_type} | pending]"
        for path in self._all_log_paths():
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith(prefix) and line.endswith(suffix):
                    return
        rating = parse_rating(final_trade_decision)
        tag = f"[{trade_date} | {ticker} | {rating} | {run_type} | pending]"
        entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # --- Read path (Phase A) ---

    def load_entries(self) -> List[dict]:
        """Parse all entries from every log file, merged and date-sorted.

        In single-file mode this is just that one file's entries in their
        original (already chronological, append-only) order. In
        multi-machine mode, entries from all machines' files are combined
        and sorted by trade date so ``get_past_context``'s "most recent
        first" logic still works correctly across machines.
        """
        paths = self._all_log_paths()
        entries: List[dict] = []
        for path in paths:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
            for raw in raw_entries:
                parsed = self._parse_entry(raw)
                if parsed:
                    parsed["_source_path"] = path
                    entries.append(parsed)
        if len(paths) > 1:
            # Stable sort keeps original per-file order for same-date entries.
            entries.sort(key=lambda e: e["date"])
        return entries

    def get_pending_entries(self) -> List[dict]:
        """Return entries with outcome:pending (for Phase B)."""
        return [e for e in self.load_entries() if e.get("pending")]

    def get_past_context(self, ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
        """Return formatted past context string for agent prompt injection.

        Only entries with ``run_type == "production"`` are included.  Entries
        from debug/test/unknown runs are excluded so exploratory pipeline runs
        cannot influence future PM decisions.
        """
        entries = [
            e for e in self.load_entries()
            if not e.get("pending") and e.get("run_type") == "production"
        ]
        if not entries:
            return ""

        same, cross = [], []
        for e in reversed(entries):
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross:
            return ""

        parts = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)

    # --- Update path (Phase B) ---

    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
    ) -> None:
        """Replace pending tag and append REFLECTION section for one entry."""
        self.batch_update_with_outcomes([{
            "ticker": ticker,
            "trade_date": trade_date,
            "raw_return": raw_return,
            "alpha_return": alpha_return,
            "holding_days": holding_days,
            "reflection": reflection,
        }])

    def batch_update_with_outcomes(self, updates: List[dict]) -> None:
        """Apply multiple outcome updates, writing each in place where it lives.

        A pending entry may live in any machine's file (it was written by
        whichever machine ran that decision originally). Each file among
        ``_all_log_paths()`` is scanned once; only files that actually
        contain a matching pending entry are rewritten (atomic temp+replace),
        so a single-file setup behaves exactly as before and a multi-machine
        setup never touches files that don't need it.
        """
        if not updates:
            return
        update_map = {(u["trade_date"], u["ticker"]): u for u in updates}

        for path in self._all_log_paths():
            if not update_map or not path.exists():
                continue
            matched = self._apply_updates_to_file(path, update_map)
            if matched:
                # Consumed matches so later files don't re-apply them.
                for key in matched:
                    update_map.pop(key, None)

    def _apply_updates_to_file(self, path: Path, update_map: dict) -> List[tuple]:
        """Rewrite ``path`` in place for any pending entry matching update_map.

        Returns the list of (trade_date, ticker) keys that were matched and
        applied, so the caller can remove them from the pending pool.
        """
        text = path.read_text(encoding="utf-8")
        blocks = text.split(self._SEPARATOR)

        new_blocks = []
        matched_keys: List[tuple] = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                new_blocks.append(block)
                continue

            lines = stripped.splitlines()
            tag_line = lines[0].strip()
            applied = False

            for (trade_date, ticker), upd in update_map.items():
                pending_prefix = f"[{trade_date} | {ticker} |"
                if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    raw_pct = f"{upd['raw_return']:+.1%}"
                    alpha_pct = f"{upd['alpha_return']:+.1%}"
                    # Preserve run_type from new-format pending tags.
                    # New format: [date | ticker | rating | run_type | pending]
                    # Old format: [date | ticker | rating | pending]
                    if (
                        len(fields) >= 5
                        and fields[3] != "pending"
                        and not self._PCT_RE.match(fields[3])
                    ):
                        run_type = fields[3]
                        new_tag = (
                            f"[{trade_date} | {ticker} | {rating} | {run_type}"
                            f" | {raw_pct} | {alpha_pct} | {upd['holding_days']}d]"
                        )
                    else:
                        new_tag = (
                            f"[{trade_date} | {ticker} | {rating}"
                            f" | {raw_pct} | {alpha_pct} | {upd['holding_days']}d]"
                        )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                    )
                    matched_keys.append((trade_date, ticker))
                    applied = True
                    break

            if not applied:
                new_blocks.append(block)

        if not matched_keys:
            return []

        new_blocks = self._apply_rotation(new_blocks)
        new_text = self._SEPARATOR.join(new_blocks)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(path)
        return matched_keys

    # --- Helpers ---

    def _apply_rotation(self, blocks: List[str]) -> List[str]:
        """Drop oldest resolved blocks when their count exceeds max_entries.

        Pending blocks are always kept (they represent unprocessed work).
        Returns ``blocks`` unchanged when rotation is disabled or under cap.
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        # Tag each block with (kept, is_resolved) by parsing tag-line markers.
        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)
        if resolved_count <= self._max_entries:
            return blocks

        to_drop = resolved_count - self._max_entries
        kept: List[str] = []
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    def _parse_entry(self, raw: str) -> Optional[dict]:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None

        # Detect old vs new tag format.
        # Old pending:  [date | ticker | rating | pending]          (fields[3]=="pending")
        # Old resolved: [date | ticker | rating | raw% | alpha% | Nd]  (fields[3] is pct/n/a)
        # New pending:  [date | ticker | rating | run_type | pending]  (fields[4]=="pending")
        # New resolved: [date | ticker | rating | run_type | raw% | alpha% | Nd]
        f3 = fields[3]
        is_old_pending = f3 == "pending"
        is_old_resolved = not is_old_pending and (self._PCT_RE.match(f3) or f3 in ("n/a", "N/A"))
        is_new_format = not is_old_pending and not is_old_resolved

        if is_new_format:
            run_type = f3
            is_pending = len(fields) >= 5 and fields[4] == "pending"
            raw_idx, alpha_idx, holding_idx = 4, 5, 6
        else:
            run_type = "unknown"
            is_pending = is_old_pending
            raw_idx, alpha_idx, holding_idx = 3, 4, 5

        entry = {
            "date": fields[0],
            "ticker": fields[1],
            "rating": fields[2],
            "run_type": run_type,
            "pending": is_pending,
            "raw": fields[raw_idx] if not is_pending and len(fields) > raw_idx else None,
            "alpha": fields[alpha_idx] if len(fields) > alpha_idx else None,
            "holding": fields[holding_idx] if len(fields) > holding_idx else None,
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""
        return entry

    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        run_type = e.get("run_type", "unknown")
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {run_type} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"
