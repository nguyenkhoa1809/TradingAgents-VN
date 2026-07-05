"""Chạy hàng ngày: seed sources -> fetch RSS -> LLM summarize -> render web.

Schedule trên Windows Task Scheduler 2 lần/ngày:
- 06:30 ICT (bắt overnight US)
- 15:30 ICT (sau EU mở cửa)

Robustness: chỉ migrate.py là CRITICAL — fail thì dừng toàn bộ pipeline ngay,
vì schema có thể đang ở trạng thái dở dang, không an toàn để bất kỳ bước nào
sau đó (kể cả render) chạm vào DB. Mọi bước khác fail thì log lỗi rõ ràng và
CHẠY TIẾP — mất 1 nguồn RSS hay LLM summarize timeout không được phép chặn
render.py, vì DB đã có sẵn dữ liệu từ các lần chạy trước; render một phần vẫn
tốt hơn trang web đứng im vì lỗi ở một bước không liên quan.
"""
import subprocess
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
PY = sys.executable
STALE_AFTER_DAYS = 2


def step(name: str, cmd: str, cwd: Path, critical: bool = True):
    """Chạy 1 bước con, trả về (name, ok, elapsed_seconds, returncode).

    critical=True  : fail -> in lỗi rõ + sys.exit ngay (dừng toàn bộ pipeline).
    critical=False : fail -> in lỗi rõ, trả ok=False, KHÔNG sys.exit — các
                     bước sau (đặc biệt render.py) vẫn tiếp tục chạy.
    """
    print(f"\n=== {name} ===")
    t = time.time()
    try:
        r = subprocess.run([PY, cmd], cwd=cwd)
        rc = r.returncode
    except Exception as e:
        print(f"  [!] Không khởi chạy được subprocess: {e}")
        rc = -1
    elapsed = time.time() - t
    ok = rc == 0
    print(f"({elapsed:.1f}s, {'OK' if ok else f'FAIL rc={rc}'})")

    if not ok:
        tag = "CRITICAL" if critical else "non-critical"
        print(f"  [!] '{name}' fail ({tag}).")
        if critical:
            print(f"\n✗ Dừng pipeline ngay — '{name}' là bước bắt buộc, "
                  f"không an toàn để chạy tiếp (kể cả render.py).")
            sys.exit(rc if rc != -1 else 1)
        print(f"  → Tiếp tục các bước còn lại.")

    return (name, ok, elapsed, rc)


def check_staleness(days: int = STALE_AFTER_DAYS) -> None:
    """Cảnh báo nếu bài mới nhất trong DB cũ hơn `days` ngày.

    Không chặn pipeline — chỉ log rõ để phát hiện sớm trường hợp fetch.py
    trả rc=0 (không lỗi) nhưng thực chất không lấy được bài mới nào (vd RSS
    đổi định dạng, DNS chết) — đúng kiểu lỗi âm thầm đã gây ra gap coverage
    của PNJ.
    """
    print("\n=== Stale check ===")
    sys.path.insert(0, str(ROOT / "ingest"))
    from db import conn
    with conn() as c:
        row = c.execute("SELECT MAX(published) AS latest FROM articles").fetchone()
    latest = row["latest"] if row else None
    if not latest:
        print("  [!] CẢNH BÁO: DB không có bài viết nào.")
        return
    latest_dt = datetime.fromisoformat(latest).replace(tzinfo=None)
    age = datetime.now() - latest_dt
    if age > timedelta(days=days):
        print(f"  [!] CẢNH BÁO: bài mới nhất trong DB từ {latest_dt.date()} "
              f"({age.days} ngày trước, ngưỡng {days} ngày) — pipeline có thể "
              f"đã ngừng cập nhật dữ liệu thật dù các bước trên báo OK.")
    else:
        print(f"  ✓ DB fresh — bài mới nhất từ {latest_dt.date()} ({age.days} ngày trước).")


def print_summary(results: list) -> None:
    print("\n" + "=" * 55)
    print("  TÓM TẮT CÁC BƯỚC")
    print("=" * 55)
    print(f"  {'Bước':<26}{'Trạng thái':<16}{'Thời gian':>10}")
    print(f"  {'-'*26}{'-'*16}{'-'*10}")
    for name, ok, elapsed, rc in results:
        status = "✓ OK" if ok else f"✗ FAIL (rc={rc})"
        print(f"  {name:<26}{status:<16}{elapsed:>8.1f}s")
    print(f"  {'-'*26}{'-'*16}{'-'*10}")
    n_fail = sum(1 for _, ok, _, _ in results if not ok)
    if n_fail:
        print(f"  ⚠ {n_fail}/{len(results)} bước fail — xem log chi tiết ở trên")
    else:
        print(f"  ✓ Tất cả {len(results)} bước thành công")
    print("=" * 55)


def main():
    results = []
    ing = ROOT / "ingest"

    # migrate CHẠY ĐẦU TIÊN, tuyệt đối — mọi bước khác đều có thể cần schema
    # mới nhất (vd cột hits_holdings do summarize.py/notify.py ghi/đọc chỉ
    # tồn tại sau migrations/001_holdings.sql).
    results.append(step("Run migrations", "migrate.py", ing, critical=True))
    results.append(step("Seed sources", "seed_sources.py", ing, critical=False))
    results.append(step("Cleanup removed sources", "cleanup_sources.py", ing, critical=False))
    results.append(step("Fetch RSS/YouTube", "fetch.py", ing, critical=False))
    results.append(step("Sell-side notes", "sellside.py", ing, critical=False))
    results.append(step("LLM summarize", "summarize.py", ing, critical=False))
    results.append(step("Telegram notify", "notify.py", ing, critical=False))
    # render LUÔN chạy cuối, bất kể mọi thứ trước đó — dùng data hiện có
    # trong DB dù fetch/summarize có fail một phần.
    results.append(step("Render web", "render.py", ROOT / "web", critical=False))

    check_staleness()
    print_summary(results)


if __name__ == "__main__":
    main()
