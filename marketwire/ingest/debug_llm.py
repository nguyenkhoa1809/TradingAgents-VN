"""Debug: xem raw response từ DeepSeek để hiểu format."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")

for model in ["deepseek-v4-flash", "deepseek-v4-pro"]:
    print(f"\n{'='*60}")
    print(f"Model: {model}")
    resp = client.chat.completions.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": 'Return JSON: {"hello": "world", "num": 42}'}],
    )
    msg = resp.choices[0].message
    print(f"content: {repr(msg.content)}")
    print(f"reasoning_content: {repr(getattr(msg, 'reasoning_content', 'N/A'))}")
    print(f"finish_reason: {resp.choices[0].finish_reason}")
