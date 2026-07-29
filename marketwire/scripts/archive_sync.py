"""archive_sync.py — Sync daily-briefing/ archive (nhiều file HTML theo ngày)
qua Cloudflare R2.

web/dist/ không được commit vào git và KHÔNG persist giữa các lần chạy
GitHub Actions (checkout mới mỗi lần) — nếu không sync, mỗi lần render chỉ
còn đúng 1 ngày (hôm nay), các ngày cũ bị mất vì dist/ luôn bắt đầu rỗng và
Cloudflare Pages deploy thay thế toàn bộ nội dung theo dist/ hiện có.

Khác với db_sync.py/wavy_sync.py (1 object cố định) — archive này gồm NHIỀU
file theo ngày, nên list theo prefix rồi đồng bộ từng file.

Usage:
    python archive_sync.py download   # kéo mọi file archive cũ từ R2 về dist/ (chạy TRƯỚC render)
    python archive_sync.py upload     # đẩy mọi file archive hiện có lên R2 (chạy SAU render)

Env vars: giống db_sync.py (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID,
R2_BUCKET_NAME).
"""
import os
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import boto3

PREFIX = "daily-briefing/"
LOCAL_DIR = Path(__file__).parent.parent / "web" / "dist" / "daily-briefing"


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


def download() -> None:
    bucket = _require_env("R2_BUCKET_NAME")
    client = _r2_client()
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    n = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            filename = obj["Key"][len(PREFIX):]
            # index.html là file tổng hợp, luôn được render.py sinh lại mới
            # từ danh sách ngày hiện có — không phải bản archive theo ngày.
            if not filename or filename == "index.html":
                continue
            client.download_file(bucket, obj["Key"], str(LOCAL_DIR / filename))
            n += 1
    print(f"[+] Downloaded {n} file(s) archive từ R2/{PREFIX}")


def upload() -> None:
    bucket = _require_env("R2_BUCKET_NAME")
    client = _r2_client()
    if not LOCAL_DIR.exists():
        print(f"[!] Không tìm thấy {LOCAL_DIR} — bỏ qua upload")
        return

    n = 0
    for html_file in LOCAL_DIR.glob("*.html"):
        if html_file.name == "index.html":
            continue
        client.upload_file(str(html_file), bucket, f"{PREFIX}{html_file.name}")
        n += 1
    print(f"[+] Uploaded {n} file(s) archive lên R2/{PREFIX}")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("upload", "download"):
        print("Usage: python archive_sync.py [upload|download]")
        sys.exit(1)
    {"upload": upload, "download": download}[sys.argv[1]]()


if __name__ == "__main__":
    main()
