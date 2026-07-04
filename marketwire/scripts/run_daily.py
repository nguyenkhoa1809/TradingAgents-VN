"""Chạy hàng ngày: seed sources -> fetch RSS -> LLM summarize -> render web.

Schedule trên Windows Task Scheduler 2 lần/ngày:
- 06:30 ICT (bắt overnight US)
- 15:30 ICT (sau EU mở cửa)
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
PY = sys.executable


def step(name, cmd, cwd):
    print(f"\n=== {name} ===")
    t = time.time()
    r = subprocess.run([PY, cmd], cwd=cwd)
    print(f"({time.time()-t:.1f}s, rc={r.returncode})")
    if r.returncode != 0:
        sys.exit(r.returncode)


def main():
    step("Seed sources", "seed_sources.py", ROOT / "ingest")
    step("Cleanup removed sources", "cleanup_sources.py", ROOT / "ingest")
    step("Run migrations", "migrate.py", ROOT / "ingest")
    step("Fetch RSS", "fetch.py", ROOT / "ingest")
    step("Sell-side notes", "sellside.py", ROOT / "ingest")
    step("LLM summarize", "summarize.py", ROOT / "ingest")
    step("Telegram notify", "notify.py", ROOT / "ingest")
    step("Render web", "render.py", ROOT / "web")
    print("\n✓ Done")


if __name__ == "__main__":
    main()
