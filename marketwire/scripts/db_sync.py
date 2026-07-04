"""db_sync.py — Sync marketwire.db với Cloudflare R2 (S3-compatible).

Usage:
    python db_sync.py download   # kéo DB mới nhất từ R2 về local
    python db_sync.py upload     # đẩy DB local lên R2
    python db_sync.py path       # in path DB local đã resolve (không cần credentials)

Dùng trong GitHub Actions (ubuntu-latest, không có disk persistence giữa các
run) để DB "sống" trên R2 thay vì trong runner. Máy local (Windows) chạy
`download` riêng (xem sync_local.bat) để lấy DB mới nhất trước khi dùng
TradingAgents, tránh dùng DB cũ từ lần chạy local trước.

Env vars bắt buộc: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID,
R2_BUCKET_NAME.
Tùy chọn: MARKETWIRE_DB_PATH — override path DB local; mặc định khớp path
ingest/db.py dùng (marketwire/data/marketwire.db).

Lưu ý: download() chỉ đảm bảo có 1 file SQLite hợp lệ theo schema.sql gốc
(lần đầu R2 chưa có DB). Nó KHÔNG chạy migrations — run_daily.py đã có bước
migrate.py chạy đầu tiên (critical=True) để đưa schema lên mới nhất, nên
không cần lặp lại logic đó ở đây.
"""
import os
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

from dotenv import load_dotenv
# Chạy local (sync_local.bat) đọc credentials từ marketwire/.env. Trong
# GitHub Actions, .env không tồn tại (gitignored, không lên repo) nên
# load_dotenv() no-op và secrets đã inject sẵn vào os.environ được giữ
# nguyên (override=False mặc định — không ghi đè env đã set).
load_dotenv(Path(__file__).parent.parent / ".env")

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

OBJECT_KEY = "marketwire.db"
MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100MB — theo spec

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "marketwire.db"


def _db_path() -> Path:
    override = os.environ.get("MARKETWIRE_DB_PATH")
    return Path(override).expanduser() if override else _DEFAULT_DB_PATH


def _require_env(name: str) -> str:
    """Trả về giá trị env var, hoặc báo lỗi RÕ RÀNG + exit(1) nếu thiếu —
    không để boto3 tự crash với lỗi auth khó hiểu khi credentials rỗng."""
    value = os.environ.get(name)
    if not value:
        print(f"[!] Thiếu biến môi trường bắt buộc: {name}")
        print(f"    Kiểm tra file marketwire/.env (xem mẫu ở marketwire/.env.example).")
        sys.exit(1)
    return value


def _r2_client():
    account_id = _require_env("R2_ACCOUNT_ID")
    access_key = _require_env("R2_ACCESS_KEY_ID")
    secret_key = _require_env("R2_SECRET_ACCESS_KEY")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",  # R2 convention (không phải AWS region thật)
    )


def _fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _init_fresh_db(db_path: Path) -> None:
    """Tạo DB mới từ schema.sql — dùng khi R2 chưa có object nào (lần đầu)."""
    import sqlite3
    schema_path = Path(__file__).parent.parent / "schema.sql"
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(schema_path.read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()


def download() -> None:
    bucket = _require_env("R2_BUCKET_NAME")
    client = _r2_client()
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    t = time.time()
    try:
        client.head_object(Bucket=bucket, Key=OBJECT_KEY)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            print(f"[!] Object '{OBJECT_KEY}' chưa tồn tại trên R2 (lần đầu) — init DB mới từ schema.sql")
            _init_fresh_db(db_path)
            print(f"    Init xong tại {db_path} ({time.time()-t:.1f}s)")
            return
        raise

    config = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD)
    client.download_file(bucket, OBJECT_KEY, str(db_path), Config=config)
    size = db_path.stat().st_size
    print(f"[+] Downloaded {OBJECT_KEY} ({_fmt_size(size)}) -> {db_path} ({time.time()-t:.1f}s)")


def upload() -> None:
    bucket = _require_env("R2_BUCKET_NAME")
    client = _r2_client()
    db_path = _db_path()

    if not db_path.exists():
        print(f"[!] Không tìm thấy DB local tại {db_path} — bỏ qua upload")
        sys.exit(1)

    size = db_path.stat().st_size
    t = time.time()
    config = TransferConfig(multipart_threshold=MULTIPART_THRESHOLD)
    client.upload_file(str(db_path), bucket, OBJECT_KEY, Config=config)
    print(f"[+] Uploaded {db_path} ({_fmt_size(size)}) -> R2/{OBJECT_KEY} ({time.time()-t:.1f}s)")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("download", "upload", "path"):
        print("Usage: python db_sync.py [download|upload|path]")
        sys.exit(1)
    if sys.argv[1] == "path":
        # In path đã resolve, không cần credentials R2 — dùng để sync_local.bat
        # hiển thị rõ file nào vừa được cập nhật mà không phải nhúng Python
        # inline trong batch (rủi ro quoting với path có ký tự '&').
        print(_db_path())
        return
    {"download": download, "upload": upload}[sys.argv[1]]()


if __name__ == "__main__":
    main()
