"""Bước 1: Đọc sell-side notes từ processed/YYYY-MM-DD/sell-side-notes.json
và insert vào MarketWire DB dưới dạng articles.

Không dùng LLM — key_points đã là summary, insert trực tiếp.
Nội dung broker (WhatsApp/Outlook) KHÔNG gửi qua DeepSeek.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import json
import hashlib
from datetime import datetime, timezone, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import conn

PROCESSED_DIR = Path(__file__).parent.parent.parent / "processed"
SOURCE_NAME = "Sell-side Notes"


def url_hash(uid: str) -> str:
    return hashlib.sha1(uid.encode()).hexdigest()


def ensure_sellside_source(c) -> int:
    row = c.execute("SELECT id FROM sources WHERE name=?", (SOURCE_NAME,)).fetchone()
    if row:
        return row["id"]
    c.execute(
        """INSERT INTO sources (name, kind, url, lang, region)
           VALUES (?, 'internal', '', 'vi', 'vn')""",
        (SOURCE_NAME,),
    )
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def load_notes(target_date: date) -> list[dict]:
    path = PROCESSED_DIR / target_date.isoformat() / "sell-side-notes.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8-sig"))


def insert_notes(notes: list[dict], target_date: date) -> int:
    if not notes:
        return 0
    new_count = 0
    with conn() as c:
        src_id = ensure_sellside_source(c)
        for note in notes:
            uid = note.get("id", "")
            if not uid:
                continue
            h = url_hash(uid)
            if c.execute("SELECT 1 FROM articles WHERE url_hash=?", (h,)).fetchone():
                continue

            ticker = note.get("ticker", "")
            analyst = note.get("analyst", "")
            source_label = note.get("source", SOURCE_NAME)
            rec = note.get("recommendation", "")
            key_points = note.get("key_points", [])
            catalyst = note.get("catalyst", "")

            # Build summary từ key_points — không dùng LLM
            points_text = " | ".join(p.strip() for p in key_points if p.strip())[:1000]
            summary = f"[{rec}] {points_text}" if rec else points_text
            thesis = catalyst[:300] if catalyst else ""

            pub_iso = datetime.combine(target_date, datetime.min.time(),
                                       tzinfo=timezone.utc).isoformat()

            # Fake URL để navigation → dùng id như anchor
            url = f"internal://sellside/{uid}"

            c.execute(
                """INSERT INTO articles
                   (source_id, url, url_hash, title, author, published, fetched,
                    raw_text, summary_vi, summary_en, thesis,
                    importance, topics, tickers, processed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    src_id, url, h,
                    f"{ticker} — {source_label}" if ticker else source_label,
                    analyst,
                    pub_iso,
                    datetime.now(timezone.utc).isoformat(),
                    points_text,          # raw_text
                    summary,              # summary_vi
                    summary,              # summary_en (same — already EN from brokers)
                    thesis,
                    3,                    # importance default = 3, rerank sẽ điều chỉnh
                    json.dumps(["sell-side", "broker-note"]),
                    json.dumps([ticker] if ticker else []),
                ),
            )
            new_count += 1
    return new_count


def run(target_date: date = None):
    if target_date is None:
        target_date = date.today()
    notes = load_notes(target_date)
    count = insert_notes(notes, target_date)
    print(f"  [sell-side] {target_date}: {count} notes mới từ {len(notes)} trong file")
    return count


if __name__ == "__main__":
    run()
