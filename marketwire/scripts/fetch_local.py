"""Chạy fetch TỪ MÁY LOCAL để lấy video YouTube (bấm đúp fetch_local.bat).

Vì sao phải chạy ở máy: YouTube chặn IP datacenter của GitHub Actions với
transcript API — mọi video đều fail "no transcript" trên CI, chỉ lấy được từ
máy có IP thường. Các nguồn RSS/tin khác CI đã chạy 8h sáng rồi; ở đây chạy lại
cũng vô hại (dedupe theo url_hash) và cần thiết vì summarize dùng chung bước.

Trình tự: kéo DB từ R2 -> migrations -> fetch (gồm YouTube) -> summarize
(DeepSeek) -> đẩy DB lại R2. Web cập nhật ở lần CI deploy kế tiếp (8h sáng hôm
sau) — script này KHÔNG render/deploy.

LƯU Ý: đừng chạy trùng lúc CI đang chạy (~8h sáng ICT). Cả hai cùng
download/upload một file DB trên R2, chạy chồng nhau sẽ đè mất data của nhau
mà không báo lỗi gì. Chạy chiều/tối là an toàn nhất.
"""
import subprocess
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
PY = sys.executable


def step(name: str, args: list, cwd: Path, critical: bool = True):
    """Chạy 1 bước con. critical=True -> fail thì dừng hẳn (không được phép đẩy
    DB lên R2 khi các bước trước hỏng); critical=False -> log rồi chạy tiếp."""
    print(f"\n=== {name} ===")
    t = time.time()
    try:
        rc = subprocess.run([PY] + args, cwd=cwd).returncode
    except Exception as e:
        print(f"  [!] Không khởi chạy được subprocess: {e}")
        rc = -1
    elapsed = time.time() - t
    ok = rc == 0
    print(f"({elapsed:.1f}s, {'OK' if ok else f'FAIL rc={rc}'})")

    if not ok:
        print(f"  [!] '{name}' fail ({'CRITICAL' if critical else 'non-critical'}).")
        if critical:
            print(f"\n✗ Dừng — '{name}' là bước bắt buộc.")
            sys.exit(rc if rc != -1 else 1)
        print("  → Tiếp tục các bước còn lại.")
    return (name, ok, elapsed, rc)


def main() -> None:
    ing = ROOT / "ingest"
    results = [
        step("Kéo DB từ R2", ["scripts/db_sync.py", "download"], ROOT, critical=True),
        step("Migrations", ["migrate.py"], ing, critical=True),
        step("Fetch tin + video YouTube", ["fetch.py"], ing, critical=False),
        step("Tóm tắt (DeepSeek)", ["summarize.py"], ing, critical=False),
        step("Đẩy DB lại R2", ["scripts/db_sync.py", "upload"], ROOT, critical=True),
    ]

    print("\n" + "=" * 55)
    print("  TÓM TẮT")
    print("=" * 55)
    for name, ok, elapsed, rc in results:
        print(f"  {name:<28}{'✓ OK' if ok else f'✗ FAIL (rc={rc})':<14}{elapsed:>7.1f}s")
    print("=" * 55)
    n_fail = sum(1 for _, ok, _, _ in results if not ok)
    if n_fail:
        print(f"  ⚠ {n_fail}/{len(results)} bước fail — xem log ở trên")
    else:
        print("  ✓ Xong. Video sẽ hiện trên web ở lần deploy kế tiếp (8h sáng hôm sau).")


if __name__ == "__main__":
    main()
