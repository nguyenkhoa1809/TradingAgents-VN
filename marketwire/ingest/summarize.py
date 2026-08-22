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
# Tin báo viết theo inverted pyramid — lede + vài đoạn đầu đã đủ cho 2-3 câu
# tóm tắt + chấm importance. 12000 ký tự cũ chiếm ~85% token mỗi request mà
# phần đuôi bài gần như không đổi kết quả (Tuổi Trẻ TB 11.774 ch, sát trần).
MAX_INPUT_CHARS = 4000
# Backlog gần đây chạm trần 200 mỗi ngày (tăng từ ~140-155 hồi tháng 7) — hạ
# xuống để chặn chi phí DeepSeek leo theo backlog thay vì theo tin thật sự mới.
ARTICLE_LIMIT = 120
# Dưới ngưỡng này thì body rỗng/chỉ còn tiêu đề (Investing.com trả feed không
# có content, fetch_full_text cũng fail) — gọi LLM chỉ đốt ~450 token overhead
# để nhận về tóm tắt bịa từ mỗi cái title. Bỏ qua, đánh dấu processed=2.
MIN_BODY_CHARS = 200
SKIPPED_NO_BODY = 2  # sentinel: khác 0 (chờ xử lý) và 1 (đã tóm tắt, được render)
# Video: tóm tắt đầy đủ hơn (có detail_vi) — đọc gần trọn transcript (lưu tối đa
# 25000 ký tự ở fetch.py) và cho output rộng hơn để chứa bản chi tiết 4 mục.
VIDEO_MAX_INPUT_CHARS = 25000
VIDEO_MAX_TOKENS = 2500

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


VIDEO_PROMPT = """Bạn là analyst hỗ trợ portfolio manager VN equity. Transcript sau là speech-to-text tự động từ 1 video YouTube tài chính (livestream/review thị trường) — sẽ có phần chào hỏi, chat lặp lại, quảng cáo Discord/khóa học ở đầu/cuối video. BỎ QUA hoàn toàn các đoạn đó, chỉ trích xuất nội dung tài chính thật sự. Trả về JSON đúng schema, KHÔNG kèm markdown.

Schema:
{{
  "summary_vi": "2-3 câu tiếng Việt tóm gọn nhất (teaser): kết luận/điểm chính của video",
  "summary_en": "2-3 sentences English, same content",
  "detail_vi": "Tóm tắt ĐẦY ĐỦ tiếng Việt, chia đúng 4 mục sau, mỗi mục 1 dòng bắt đầu bằng nhãn (bỏ mục nếu video không đề cập):\\nMacro: các số liệu/sự kiện vĩ mô được nhắc (Fed, lãi suất, jobs, dầu, vàng, tỷ giá...) kèm con số cụ thể\\nThị trường: quan điểm về diễn biến/xu hướng thị trường (index, dòng tiền, sentiment)\\nCổ phiếu: các mã/nhóm ngành cụ thể được bàn và nhận định\\nKhuyến nghị: lời khuyên hành động nếu có",
  "thesis": "1 câu: khuyến nghị đầu tư hoặc quan điểm thị trường quan trọng nhất nếu có, hoặc empty string",
  "importance": 1-5,
  "topics": ["rates","fx","credit","em","vn-banks","commodities","tech","macro-us","macro-cn","equity-strategy", ...],
  "tickers": ["VCB","FPT",...]
}}

Importance: 5=market-moving cho VN PM, 4=macro shift, 3=relevant context, 2=noise, 1=skip.
Tickers: chỉ liệt kê mã có trong universe sau: {universe}

Video: {title}
Kênh: {source}
Transcript (có thể lộn xộn do speech-to-text):
{body}
"""


def summarize_one(title, source, body):
    prompt = PROMPT.format(
        universe=", ".join(sorted(UNIVERSE)),
        title=title, source=source,
        body=body[:MAX_INPUT_CHARS],
    )
    return LLM.complete_json(prompt, max_tokens=1400)


def summarize_video(title, source, body):
    prompt = VIDEO_PROMPT.format(
        universe=", ".join(sorted(UNIVERSE)),
        title=title, source=source,
        body=body[:VIDEO_MAX_INPUT_CHARS],
    )
    return LLM.complete_json(prompt, max_tokens=VIDEO_MAX_TOKENS)


def skip_no_body(row) -> bool:
    """Loại bài không có nội dung TRƯỚC khi tốn 1 request LLM.

    processed=2 để bài rơi khỏi cả hàng đợi summarize (processed=0) lẫn web
    (render.py/notify.py lọc processed=1) — không cần sửa 2 file đó.
    """
    if len(row["raw_text"] or "") >= MIN_BODY_CHARS:
        return False
    with conn() as c:
        c.execute("UPDATE articles SET processed = ? WHERE id = ?",
                  (SKIPPED_NO_BODY, row["id"]))
    return True


def process_article(row):
    try:
        if skip_no_body(row):
            return None
        summarize_fn = summarize_video if row["kind"] == "youtube" else summarize_one
        result = summarize_fn(row["title"], row["source_name"], row["raw_text"] or "")
        tickers_in_universe = [t for t in result.get("tickers", []) if t in UNIVERSE]
        hits = [t for t in tickers_in_universe if t in HOLDINGS]
        with conn() as c:
            c.execute(
                """UPDATE articles SET
                     summary_vi = ?, summary_en = ?, thesis = ?,
                     importance = ?, topics = ?, tickers = ?,
                     hits_holdings = ?, detail_vi = ?,
                     processed = 1
                   WHERE id = ?""",
                (result["summary_vi"], result["summary_en"],
                 result.get("thesis", ""),
                 int(result["importance"]),
                 json.dumps(result.get("topics", [])),
                 json.dumps(tickers_in_universe),
                 json.dumps(hits) if hits else None,
                 result.get("detail_vi") or None,  # chỉ video có; bài RSS để NULL
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
        pending = c.execute("SELECT COUNT(*) AS n FROM articles WHERE processed = 0").fetchone()["n"]
        rows = c.execute(
            """SELECT a.id, a.title, a.raw_text, s.name AS source_name, s.kind
               FROM articles a JOIN sources s ON a.source_id = s.id
               WHERE a.processed = 0
               ORDER BY a.published DESC
               LIMIT ?""",
            (ARTICLE_LIMIT,)
        ).fetchall()

    if pending > ARTICLE_LIMIT:
        print(f"  [!] Backlog {pending} bài > cap {ARTICLE_LIMIT} — {pending - ARTICLE_LIMIT} bài cũ hơn bị bỏ qua lần này.")
    print(f"Processing {len(rows)} articles với {LLM_CFG['provider']}/{LLM_CFG.get('model','default')}...")
    ok = skipped = 0
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = [ex.submit(process_article, r) for r in rows]
        for f in as_completed(futures):
            r = f.result()
            if r is None:
                skipped += 1
            elif r:
                ok += 1
    if skipped:
        print(f"  [·] Bỏ qua {skipped} bài không có nội dung (<{MIN_BODY_CHARS} ch) — không gọi LLM.")
    print(f"Done: {ok}/{len(rows) - skipped} (đã gọi LLM), skipped {skipped}")
    rerank()


if __name__ == "__main__":
    run()
