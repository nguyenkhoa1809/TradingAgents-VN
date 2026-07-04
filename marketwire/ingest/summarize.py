"""LLM pass: tóm tắt (vi+en), trích thesis, chấm importance, tag topics/tickers.

Provider chọn qua sources.yaml -> llm.default. Đổi provider không cần sửa code này.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from db import conn
import holdings as holdings_mod
from llm import get_provider

CFG = yaml.safe_load((Path(__file__).parent.parent / "sources.yaml").read_text(encoding="utf-8"))
UNIVERSE = set(CFG.get("vn_universe", []))
HOLDINGS = holdings_mod.current_tickers()

LLM_CFG = CFG["llm"]["default"]
LLM = get_provider(LLM_CFG["provider"], LLM_CFG.get("model"))

PARALLEL_WORKERS = 4
MAX_INPUT_CHARS = 12000

PROMPT = """Bạn là analyst hỗ trợ portfolio manager VN equity. Phân tích bài viết sau và trả về JSON đúng schema, KHÔNG kèm markdown.

Schema:
{{
  "summary_vi": "2-3 câu tiếng Việt, plain language, nêu kết luận/data point chính",
  "summary_en": "2-3 sentences English, same content",
  "thesis": "1 câu: luận điểm/data point quan trọng nhất nếu có, hoặc empty string",
  "importance": 1-5,
  "topics": ["rates","fx","credit","em","vn-banks","commodities","tech","macro-us","macro-cn","equity-strategy", ...],
  "tickers": ["VCB","FPT",...]
}}

Importance: 5=market-moving cho VN PM, 4=macro shift, 3=relevant context, 2=noise, 1=skip.
Tickers: chỉ liệt kê mã có trong universe sau: {universe}

Bài viết:
Title: {title}
Source: {source}
Body:
{body}
"""


def summarize_one(title, source, body):
    prompt = PROMPT.format(
        universe=", ".join(sorted(UNIVERSE)),
        title=title, source=source,
        body=body[:MAX_INPUT_CHARS],
    )
    return LLM.complete_json(prompt, max_tokens=1400)


def process_article(row):
    try:
        result = summarize_one(row["title"], row["source_name"], row["raw_text"] or "")
        tickers_in_universe = [t for t in result.get("tickers", []) if t in UNIVERSE]
        hits = [t for t in tickers_in_universe if t in HOLDINGS]
        with conn() as c:
            c.execute(
                """UPDATE articles SET
                     summary_vi = ?, summary_en = ?, thesis = ?,
                     importance = ?, topics = ?, tickers = ?,
                     hits_holdings = ?,
                     processed = 1
                   WHERE id = ?""",
                (result["summary_vi"], result["summary_en"],
                 result.get("thesis", ""),
                 int(result["importance"]),
                 json.dumps(result.get("topics", [])),
                 json.dumps(tickers_in_universe),
                 json.dumps(hits) if hits else None,
                 row["id"]),
            )
        return True
    except Exception as e:
        print(f"  [!] art_id={row['id']}: {e}")
        return False


RERANK_PROMPT = """Bạn là senior VN equity analyst. Đánh giá lại mức độ quan trọng của bài viết với góc nhìn VN equity PM.

Importance scale:
5 = market-moving trực tiếp cho VN (Fed decision, VN macro shock, sector policy change)
4 = macro shift ảnh hưởng 1-2 tuần (EM flows, rates outlook revision, key data beat/miss)
3 = relevant context hữu ích
2 = noise
1 = skip

Trả về JSON (không markdown):
{{"importance": 1-5, "thesis": "1 câu concise nhất về impact với VN equity PM, hoặc empty string"}}

Title: {title}
Source: {source}
Summary: {summary}
Body (excerpt):
{body}"""


def rerank():
    """Rerank top N articles qua provider chất lượng cao hơn (nếu enabled trong sources.yaml)."""
    rerank_cfg = CFG["llm"].get("rerank", {})
    if not rerank_cfg.get("enabled", False):
        return

    provider = get_provider(rerank_cfg["provider"], rerank_cfg.get("model"))
    top_n = rerank_cfg.get("top_n", 20)

    with conn() as c:
        rows = c.execute(
            """SELECT a.id, a.title, a.raw_text, a.summary_vi, a.importance,
                      s.name AS source_name
               FROM articles a JOIN sources s ON a.source_id = s.id
               WHERE a.processed = 1
                 AND a.published >= datetime('now', '-24 hours')
               ORDER BY a.importance DESC
               LIMIT ?""",
            (top_n,)
        ).fetchall()

    if not rows:
        return

    print(f"Reranking {len(rows)} articles với {rerank_cfg['provider']}/{rerank_cfg.get('model')}...")
    for row in rows:
        try:
            prompt = RERANK_PROMPT.format(
                title=row["title"],
                source=row["source_name"],
                summary=row["summary_vi"] or "",
                body=(row["raw_text"] or "")[:3000],
            )
            result = provider.complete_json(prompt, max_tokens=600)
            new_imp = int(result.get("importance", 0))
            if new_imp and new_imp != row["importance"]:
                with conn() as c:
                    c.execute(
                        "UPDATE articles SET importance=?, thesis=? WHERE id=?",
                        (new_imp, result.get("thesis", ""), row["id"])
                    )
        except Exception as e:
            print(f"  [!] rerank art_id={row['id']}: {e}")
    print("Rerank done")


def run():
    with conn() as c:
        rows = c.execute(
            """SELECT a.id, a.title, a.raw_text, s.name AS source_name
               FROM articles a JOIN sources s ON a.source_id = s.id
               WHERE a.processed = 0
               ORDER BY a.published DESC
               LIMIT 200"""
        ).fetchall()

    print(f"Processing {len(rows)} articles với {LLM_CFG['provider']}/{LLM_CFG.get('model','default')}...")
    ok = 0
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = [ex.submit(process_article, r) for r in rows]
        for f in as_completed(futures):
            if f.result():
                ok += 1
    print(f"Done: {ok}/{len(rows)}")
    rerank()


if __name__ == "__main__":
    run()
