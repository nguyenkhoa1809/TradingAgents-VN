"""Debug rerank với deepseek-v4-pro."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import os
from openai import OpenAI
from db import conn

client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")

with conn() as c:
    row = c.execute(
        """SELECT a.id, a.title, a.raw_text, a.summary_vi, s.name AS source_name
           FROM articles a JOIN sources s ON a.source_id = s.id
           WHERE a.processed = 1 LIMIT 1"""
    ).fetchone()

prompt = f"""Đánh giá tầm quan trọng, trả về JSON không markdown:
{{"importance": 1-5, "thesis": "1 câu"}}

Title: {row['title']}
Summary: {row['summary_vi'] or ''}
Body: {(row['raw_text'] or '')[:1000]}"""

for max_tok in [200, 500, 1000]:
    resp = client.chat.completions.create(
        model="deepseek-v4-pro",
        max_tokens=max_tok,
        messages=[{"role": "user", "content": prompt}],
    )
    msg = resp.choices[0].message
    print(f"max_tokens={max_tok}: content={repr(msg.content[:200] if msg.content else 'EMPTY')}, finish={resp.choices[0].finish_reason}")
