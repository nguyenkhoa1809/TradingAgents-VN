"""Telegram push notification khi có bài ★≥4 chạm holdings mới.

Env vars cần set:
  TELEGRAM_BOT_TOKEN — lấy từ @BotFather
  TELEGRAM_CHAT_ID   — personal chat ID hoặc group (dùng @userinfobot để lấy)

Chạy sau mỗi lần summarize. Chỉ push bài được fetch trong 1 giờ qua để tránh spam.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import requests

from db import conn

IMPORTANCE_THRESHOLD = 4
LOOKBACK_HOURS = 1


def _send(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"  [!] Telegram: {e}")


def push_new_hits():
    """Push bài ★≥4 chạm holdings được fetch trong LOOKBACK_HOURS giờ gần nhất."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    with conn() as c:
        rows = c.execute(
            """SELECT a.title, a.url, a.importance, a.thesis, a.hits_holdings,
                      s.name AS source_name
               FROM articles a JOIN sources s ON a.source_id = s.id
               WHERE a.processed = 1
                 AND a.importance >= ?
                 AND a.hits_holdings IS NOT NULL
                 AND a.fetched >= ?
               ORDER BY a.importance DESC, a.published DESC""",
            (IMPORTANCE_THRESHOLD, cutoff),
        ).fetchall()

    if not rows:
        return

    for r in rows:
        hits = json.loads(r["hits_holdings"] or "[]")
        tickers_str = " ".join(f"<code>{t}</code>" for t in hits)
        parts = [
            f"★{r['importance']} <b>{r['title']}</b>",
            r["source_name"],
        ]
        if r["thesis"]:
            parts.append(f"<i>{r['thesis']}</i>")
        if tickers_str:
            parts.append(tickers_str)
        parts.append(f'<a href="{r["url"]}">Đọc →</a>')
        _send("\n".join(parts))

    print(f"Pushed {len(rows)} Telegram notifications")


if __name__ == "__main__":
    push_new_hits()
