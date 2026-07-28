"""wavy_sync.py — Sync wavy_signals JSON qua Cloudflare R2 (S3-compatible).

wavy_scheduler.py (wavy-system/Daily/, SIBLING của TradingAgents/ — không nằm
trong git repo này) ghi wavy_signals_YYYY-MM-DD.json vào
Intelligence_&_Quant_Screening/screener-output/. GitHub Actions runner chỉ
checkout repo TradingAgents nên KHÔNG BAO GIỜ thấy được thư mục đó — phải đẩy
qua R2 (cùng bucket với marketwire.db, xem db_sync.py) để render.py trên CI
đọc lại được.

Usage:
    python wavy_sync.py upload    # máy local: đẩy file hôm nay/hôm qua lên R2
    python wavy_sync.py download  # CI: kéo về marketwire/data/wavy_signals_latest.json

Env vars: giống db_sync.py (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID,
R2_BUCKET_NAME).
"""
import os
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import boto3
from botocore.exceptions import ClientError

OBJECT_KEY = "wavy_signals_latest.json"
# scripts/ -> marketwire/ -> TradingAgents/ -> Intelligence_&_Quant_Screening/
SCREENER_OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "screener-output"
LOCAL_PATH = Path(__file__).parent.parent / "data" / "wavy_signals_latest.json"


def _require_env(name: str) -> str:
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
        region_name="auto",
    )


def _find_local_file():
    """Tìm wavy_signals_{date}.json của hôm nay, fallback hôm qua."""
    for delta in (0, 1):
        candidate = SCREENER_OUTPUT_DIR / f"wavy_signals_{(date.today() - timedelta(days=delta)).isoformat()}.json"
        if candidate.exists():
            return candidate
    return None


def upload() -> None:
    bucket = _require_env("R2_BUCKET_NAME")
    src = _find_local_file()
    if not src:
        print(f"[!] Không tìm thấy wavy_signals_*.json hôm nay/hôm qua trong {SCREENER_OUTPUT_DIR} — bỏ qua upload")
        return
    client = _r2_client()
    t = time.time()
    client.upload_file(str(src), bucket, OBJECT_KEY)
    print(f"[+] Uploaded {src.name} -> R2/{OBJECT_KEY} ({time.time()-t:.1f}s)")


def download() -> None:
    bucket = _require_env("R2_BUCKET_NAME")
    client = _r2_client()
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    t = time.time()
    try:
        client.download_file(bucket, OBJECT_KEY, str(LOCAL_PATH))
        print(f"[+] Downloaded {OBJECT_KEY} -> {LOCAL_PATH} ({time.time()-t:.1f}s)")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            print(f"[!] Chưa có wavy signals nào trên R2 — bỏ qua, briefing sẽ hiện trống")
            return
        raise


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("upload", "download"):
        print("Usage: python wavy_sync.py [upload|download]")
        sys.exit(1)
    {"upload": upload, "download": download}[sys.argv[1]]()


if __name__ == "__main__":
    main()
