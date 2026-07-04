"""Deactivate sources không còn trong sources.yaml và xóa articles của chúng."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import yaml
from pathlib import Path
from db import conn

CFG = Path(__file__).parent.parent / "sources.yaml"
active_names = {s["name"] for s in yaml.safe_load(CFG.read_text(encoding="utf-8"))["sources"]}

# Internal sources added programmatically — never in sources.yaml, never delete
INTERNAL_SOURCES = {"Sell-side Notes"}

with conn() as c:
    all_sources = c.execute("SELECT id, name FROM sources").fetchall()
    for src in all_sources:
        if src["name"] in INTERNAL_SOURCES:
            continue
        if src["name"] not in active_names:
            deleted = c.execute("DELETE FROM articles WHERE source_id=?", (src["id"],)).rowcount
            c.execute("DELETE FROM sources WHERE id=?", (src["id"],))
            print(f"Removed source '{src['name']}': {deleted} articles deleted")
    print("Done")
