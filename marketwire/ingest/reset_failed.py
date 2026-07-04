import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
from db import conn
with conn() as c:
    r = c.execute("UPDATE articles SET processed=0 WHERE summary_vi IS NULL")
    print(f"Reset {r.rowcount} articles to reprocess")
