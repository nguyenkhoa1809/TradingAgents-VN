"""Chạy migrations theo thứ tự, skip migration đã apply.

Track qua bảng _migrations.
"""
import sqlite3
from pathlib import Path
from db import conn

MIG_DIR = Path(__file__).parent.parent / "migrations"


def run():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        applied = {r[0] for r in c.execute("SELECT name FROM _migrations").fetchall()}

        for sql_file in sorted(MIG_DIR.glob("*.sql")):
            if sql_file.name in applied:
                continue
            print(f"Applying {sql_file.name}...")
            try:
                c.executescript(sql_file.read_text(encoding="utf-8"))
                c.execute("INSERT INTO _migrations(name) VALUES (?)", (sql_file.name,))
            except sqlite3.OperationalError as e:
                # Cột đã tồn tại từ chạy thủ công — đánh dấu là applied
                if "duplicate column" in str(e):
                    print(f"  (cột đã có, skip)")
                    c.execute("INSERT INTO _migrations(name) VALUES (?)", (sql_file.name,))
                else:
                    raise
        print("Migrations done")


if __name__ == "__main__":
    run()
