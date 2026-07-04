"""Wrapper SQLite gọn — connection, schema init, helper queries."""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent.parent / "data" / "marketwire.db"
SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
