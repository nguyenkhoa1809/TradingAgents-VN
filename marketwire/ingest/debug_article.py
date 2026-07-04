"""Debug: thử summarize một bài cụ thể đang lỗi, xem raw response."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import os, json
from openai import OpenAI
from db import conn

client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")

# Lấy một bài đang lỗi (summary_vi IS NULL)
with conn() as c:
    row = c.execute(
        "SELECT id, title, raw_text FROM articles WHERE summary_vi IS NULL LIMIT 1"
    ).fetchone()

if not row:
    print("Không có bài nào lỗi.")
    sys.exit()

print(f"Article {row['id']}: {row['title'][:80]}")
print(f"Body length: {len(row['raw_text'] or '')} chars")
print()

prompt = f"""Tóm tắt bài sau, trả về JSON không có markdown:
{{"summary_vi": "2-3 câu tiếng Việt", "importance": 3}}

Title: {row['title']}
Body:
{(row['raw_text'] or '')[:3000]}"""

# Test không dùng response_format
resp = client.chat.completions.create(
    model="deepseek-v4-flash",
    max_tokens=400,
    messages=[{"role": "user", "content": prompt}],
)
content = resp.choices[0].message.content
print(f"=== No JSON mode ===")
print(f"Raw content ({len(content)} chars):")
print(repr(content[:500]))
print()

# Test với response_format
try:
    resp2 = client.chat.completions.create(
        model="deepseek-v4-flash",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content2 = resp2.choices[0].message.content
    print(f"=== With json_object mode ===")
    print(f"Raw content ({len(content2)} chars):")
    print(repr(content2[:500]))
    try:
        parsed = json.loads(content2)
        print(f"JSON parse: OK -> {list(parsed.keys())}")
    except Exception as e:
        print(f"JSON parse FAILED: {e}")
except Exception as e:
    print(f"response_format not supported: {e}")
