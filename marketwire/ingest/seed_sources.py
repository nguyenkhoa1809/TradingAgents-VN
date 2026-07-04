"""Đọc sources.yaml, upsert vào bảng sources."""
import yaml
from pathlib import Path
from db import conn, init_db

CFG = Path(__file__).parent.parent / "sources.yaml"


def seed():
    init_db()
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    with conn() as c:
        for s in cfg["sources"]:
            c.execute(
                """INSERT INTO sources (name, kind, url, lang, region, expert_name, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     kind=excluded.kind, url=excluded.url,
                     lang=excluded.lang, region=excluded.region,
                     expert_name=excluded.expert_name,
                     active=excluded.active""",
                (s["name"], s["kind"], s["url"], s.get("lang", "en"),
                 s.get("region", "global"), s.get("expert_name"),
                 0 if s.get("active") is False else 1),
            )
    print(f"Seeded {len(cfg['sources'])} sources")


if __name__ == "__main__":
    seed()
