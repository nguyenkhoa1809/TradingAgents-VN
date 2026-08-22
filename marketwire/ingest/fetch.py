"""Fetch RSS feeds, parse, dedupe theo url_hash, insert raw vào articles.

LLM summarize/tag để pipeline sau xử lý (xem summarize.py).
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import hashlib
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import html
import re
import unicodedata

import feedparser
import requests
from bs4 import BeautifulSoup
from db import conn

PARALLEL_WORKERS = 6
TIMEOUT = 20
USER_AGENT = "MarketWire/1.0 (personal aggregator)"
ICT_TZ = timezone(timedelta(hours=7))  # giờ VN — dùng để xác định "hôm nay" cho video YouTube
# processed=3: tin trùng (cùng sự kiện, khác nguồn) — không tính là "chưa xử
# lý" (0) để summarize.py bỏ qua, không tính là "đã tóm tắt" (1) để render.py/
# notify.py không hiện — 5 báo cùng đăng 1 tin PR chỉ tóm tắt 1 bản.
SKIPPED_DUPLICATE = 3
_DEDUP_STOPWORDS = set(
    "va cua cho tu voi tai trong khi nhung mot cac da se duoc la co the nay "
    "do vao ra len xuong nam thang ngay tin bai theo den tren duoi hon nhat "
    "sau truoc them noi cung cong ty".split()
)


def url_hash(url: str) -> str:
    return hashlib.sha1(url.strip().lower().encode()).hexdigest()


# id/class chứa 1 trong các từ này gần như luôn là chrome của trang (menu,
# sidebar, banner quảng cáo, khối bình luận) chứ không phải nội dung bài —
# phát hiện được vì Tuổi Trẻ/Vietstock/Thanh Niên đặt menu trong <div> thường,
# không dùng thẻ <nav>/<header> ngữ nghĩa nên soup(["nav","header",...]) cũ
# bỏ sót, khiến ~30-36% ký tự đầu mỗi bài là menu điều hướng lặp lại y hệt
# giữa các bài khác nhau hoàn toàn về nội dung.
_CHROME_HINTS = ("menu", "nav", "sidebar", "banner", "cookie", "share-",
                 "social", "comment", "quangcao", "advertis", "widget",
                 "breadcrumb", "footer", "header")


def strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # <head> (title/meta) không phải nội dung — get_text() mặc định vẫn lấy
    # nếu không decompose, và <title> trang thường lặp lại tiêu đề + tên báo.
    for tag in soup(["script", "style", "nav", "footer", "header", "head", "noscript"]):
        tag.decompose()
    for tag in soup.find_all(True):
        ident = tag.get("id", "") + " " + " ".join(tag.get("class", []) or [])
        if any(h in ident.lower() for h in _CHROME_HINTS):
            tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def fetch_full_text(url: str) -> str:
    """Một số feed chỉ có summary — fetch full page để có context cho LLM."""
    try:
        r = requests.get(url, timeout=TIMEOUT,
                         headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return strip_html(r.text)[:20000]  # cap để tránh ngốn token
    except Exception:
        return ""


def fetch_source(source_row) -> int:
    """Fetch một RSS/Substack source, return số bài mới insert."""
    sid, name, url = source_row["id"], source_row["name"], source_row["url"]
    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"  [!] {name}: {e}")
        return 0

    # Phase 1: check existence (read-only, short-lived conn)
    candidates = []
    with conn() as c:
        for entry in feed.entries[:50]:
            link = entry.get("link", "")
            if not link:
                continue
            h = url_hash(link)
            if c.execute("SELECT 1 FROM articles WHERE url_hash = ?", (h,)).fetchone():
                continue
            candidates.append((entry, link, h))

    if not candidates:
        print(f"  [+] {name}: 0 bài mới")
        return 0

    # Phase 2: HTTP fetch ngoài DB lock
    now_iso = datetime.now(timezone.utc).isoformat()
    to_insert = []
    for entry, link, h in candidates:
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_iso = (
            datetime(*pub[:6], tzinfo=timezone.utc).isoformat()
            if pub else now_iso
        )
        body = ""
        if entry.get("content"):
            body = strip_html(entry["content"][0].get("value", ""))
        if len(body) < 500:
            body_full = fetch_full_text(link)
            if len(body_full) > len(body):
                body = body_full
        if not body:
            body = strip_html(entry.get("summary", ""))
        to_insert.append((sid, link, h,
                          entry.get("title", "(no title)"),
                          entry.get("author"),
                          pub_iso, now_iso, body))

    # Phase 3: batch insert (short-lived write conn)
    with conn() as c:
        for row in to_insert:
            c.execute(
                """INSERT OR IGNORE INTO articles
                   (source_id, url, url_hash, title, author, published, fetched, raw_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )

    print(f"  [+] {name}: {len(to_insert)} bài mới")
    return len(to_insert)


def _filter_scrape_links(raw_links: list[dict], base_url: str, link_contains: str = "") -> list[str]:
    """Filter crawl4ai internal links xuống candidate article links.

    Giữ query params (Oracle WebCenter dùng ?dDocName=... để phân biệt bài).
    """
    base_parsed = urlparse(base_url)
    seen = set()
    links = []
    for lk in raw_links:
        href = (lk.get("href") or "").strip()
        if not href:
            continue
        parsed = urlparse(href)
        if link_contains and link_contains not in parsed.path:
            continue
        if not link_contains and len(parsed.path) <= len(base_parsed.path):
            continue
        canonical = parsed._replace(fragment="").geturl()
        if canonical not in seen:
            seen.add(canonical)
            links.append(canonical)
    return links[:50]


async def _fetch_scrape_async(source_row) -> int:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.content_filter_strategy import PruningContentFilter

    sid, name, url = source_row["id"], source_row["name"], source_row["url"]
    link_contains = source_row["scrape_link_contains"] if "scrape_link_contains" in source_row.keys() else ""

    bc = BrowserConfig(headless=True, browser_type="chromium", verbose=False,
                       extra_args=["--no-sandbox", "--disable-dev-shm-usage"])
    list_rc = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=TIMEOUT * 1000)
    art_mg = DefaultMarkdownGenerator(content_filter=PruningContentFilter())
    art_rc = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, markdown_generator=art_mg,
                              page_timeout=TIMEOUT * 1000)

    async with AsyncWebCrawler(config=bc) as crawler:
        # Listing page — JS-rendered OK
        list_r = await crawler.arun(url, config=list_rc)
        if not list_r.success:
            err = getattr(list_r, "error_message", "") or ""
            print(f"  [!] {name}: {err[:200] or 'listing page failed'}")
            return 0

        links = _filter_scrape_links(list_r.links.get("internal", []), url, link_contains)

        # Phase 1: DB read (check existing)
        new_links = []
        for link in links:
            h = url_hash(link)
            with conn() as c:
                if not c.execute("SELECT 1 FROM articles WHERE url_hash=?", (h,)).fetchone():
                    new_links.append((link, h))

        if not new_links:
            print(f"  [+] {name} (scrape): 0 bài mới")
            return 0

        # Phase 2: crawl article pages
        now_iso = datetime.now(timezone.utc).isoformat()
        to_insert = []
        for link, h in new_links:
            art_r = await crawler.arun(link, config=art_rc)
            if not art_r.success:
                continue
            title = (art_r.metadata or {}).get("title") or link
            body = art_r.markdown.fit_markdown or art_r.markdown.raw_markdown or ""
            if not body:
                continue
            to_insert.append((sid, link, h, title[:500], now_iso, now_iso, body[:20000]))

    # Phase 3: batch insert
    with conn() as c:
        for row in to_insert:
            c.execute(
                """INSERT OR IGNORE INTO articles
                   (source_id, url, url_hash, title, published, fetched, raw_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                row,
            )

    print(f"  [+] {name} (scrape): {len(to_insert)} bài mới")
    return len(to_insert)


def fetch_scrape_source(source_row) -> int:
    """Scrape listing page qua Crawl4AI (xử lý JS), extract article links, insert mới vào DB."""
    import asyncio
    try:
        return asyncio.run(_fetch_scrape_async(source_row))
    except Exception as e:
        print(f"  [!] {source_row['name']}: {e}")
        return 0


def fetch_youtube_source(source_row) -> int:
    """Fetch YouTube channel RSS, lấy transcript cho mỗi video mới.

    url trong sources.yaml phải là YouTube RSS feed:
    https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxx
    (channel_id lấy từ view-source trang kênh, tìm "channelId")
    """
    from urllib.parse import parse_qs
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    except ImportError:
        print("  [!] youtube-transcript-api chưa cài: pip install youtube-transcript-api")
        return 0

    sid, name, url = source_row["id"], source_row["name"], source_row["url"]

    try:
        feed = feedparser.parse(url, agent=USER_AGENT)
    except Exception as e:
        print(f"  [!] {name}: {e}")
        return 0

    new_count = 0
    for entry in feed.entries[:15]:
        link = entry.get("link", "")
        if not link:
            continue

        parsed_url = urlparse(link)
        video_id = parse_qs(parsed_url.query).get("v", [None])[0]
        if not video_id:
            continue

        # Bỏ qua video đăng HÔM NAY (giờ VN): mới upload thường chưa có
        # transcript (auto-caption cần thời gian xử lý) — quét sẽ luôn fail.
        # Để lần chạy ngày mai xử lý khi transcript đã sẵn; video vẫn nằm trong
        # 15 entry mới nhất của feed nên không bị bỏ sót.
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub:
            pub_ict = datetime(*pub[:6], tzinfo=timezone.utc).astimezone(ICT_TZ).date()
            if pub_ict >= datetime.now(ICT_TZ).date():
                print(f"    [·] {name}: bỏ qua video hôm nay ({video_id})")
                continue

        h = url_hash(link)
        with conn() as c:
            if c.execute("SELECT 1 FROM articles WHERE url_hash = ?", (h,)).fetchone():
                continue

        try:
            ytt = YouTubeTranscriptApi()
            segments = ytt.fetch(video_id, languages=["vi", "vi-VN", "en", "en-US"])
        except Exception as e1:
            # fallback: thử auto-generated bất kỳ ngôn ngữ
            try:
                ytt = YouTubeTranscriptApi()
                tlist = ytt.list(video_id)
                gen = tlist.find_generated_transcript(["vi", "en"])
                segments = gen.fetch()
            except Exception as e2:
                # In rõ loại lỗi thật — youtube-transcript-api chặn IP datacenter
                # (RequestBlocked/IpBlocked) trả lỗi khác hẳn "video thật sự không
                # có transcript" (NoTranscriptFound/TranscriptsDisabled); catch-all
                # cũ in "no transcript" chung chung che mất sự khác biệt này.
                print(f"    [-] {name}: {video_id} — {type(e1).__name__}: {e1} | fallback {type(e2).__name__}: {e2}")
                continue

        # youtube-transcript-api >=1.0 returns FetchedTranscriptSnippet dataclasses
        # (attribute access), not dicts — the old ["text"] subscript raised
        # TypeError and silently killed every video's transcript fetch.
        transcript = " ".join(s.text for s in segments)
        if not transcript:
            continue

        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        pub_iso = (
            datetime(*pub[:6], tzinfo=timezone.utc).isoformat()
            if pub else datetime.now(timezone.utc).isoformat()
        )

        with conn() as c:
            c.execute(
                """INSERT INTO articles
                   (source_id, url, url_hash, title, author, published, fetched, raw_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, link, h,
                 entry.get("title", "(no title)"),
                 entry.get("author"),
                 pub_iso,
                 datetime.now(timezone.utc).isoformat(),
                 transcript[:25000]),
            )
        new_count += 1

    print(f"  [+] {name} (youtube): {new_count} video mới")
    return new_count


def _strip_dia(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")


def _title_tokens(title: str) -> set:
    t = _strip_dia(html.unescape(title or "").lower())
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return {w for w in t.split() if len(w) > 2 and w not in _DEDUP_STOPWORDS}


def _entity_tokens(title: str) -> set:
    """Token 'tên riêng' trong title GỐC (chưa lowercase) — viết hoa giữa câu,
    toàn chữ hoa (ticker/viết tắt: SHB, ECB, PNJ...), hoặc chữ+số dính nhau
    (PC1, VSIP2). Dùng để chặn false positive kiểu 2 công ty khác nhau nhưng
    cùng khuôn mẫu tin ("X bị phạt 300 triệu", "Y có chủ tịch mới", "Z lãi
    hơn N tỷ trong 6 tháng") — Jaccard theo từ thường sẽ nhận nhầm là trùng
    vì phần khuôn mẫu chiếm đa số từ, dù thực thể chính khác hẳn.
    """
    words = re.findall(r"[A-Za-zÀ-ỹ0-9]+", html.unescape(title or ""))
    out = set()
    for i, w in enumerate(words):
        has_alpha = any(ch.isalpha() for ch in w)
        has_digit = any(ch.isdigit() for ch in w)
        is_allcaps = w.isupper() and len(w) >= 2
        is_midcap = i > 0 and w[0].isupper() and len(w) >= 3
        if (has_digit and has_alpha) or is_allcaps or is_midcap:
            out.add(_strip_dia(w.lower()))
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_same_story(a: dict, b: dict, threshold: float) -> bool:
    if _jaccard(a["tok"], b["tok"]) < threshold:
        return False
    # Bắt buộc trùng ít nhất 1 tên riêng nếu title có tên riêng — 2 title đều
    # không tên riêng nào (thường là tin thị trường chung, không gắn 1 công
    # ty cụ thể) thì vẫn cho qua chỉ dựa vào word-Jaccard.
    ea, eb = a["ent"], b["ent"]
    return bool(ea & eb) or (not ea and not eb)


def dedup_cross_source(hours: int = 48, threshold: float = 0.55) -> int:
    """Gộp tin trùng sự kiện đăng bởi nhiều báo khác nhau (PR, tin thời sự lớn
    3-5 nguồn cùng đưa) — đo được ~3.5% volume trên dữ liệu thật (53/1530 bài
    RSS/7 ngày), verify tay toàn bộ 42 cụm, không có gộp nhầm 2 sự kiện khác
    nhau. threshold 0.55 + entity-gate cố ý bảo thủ: từng thử 0.45 không có
    entity-gate và bắt nhầm "ECB giữ nguyên lãi suất" với "Fed giữ nguyên lãi
    suất", "Eximbank có chủ tịch mới" với "PC1 có Chủ tịch mới" — 2 công ty
    khác nhau nhưng cùng khuôn mẫu câu. Thà bỏ sót vài tin trùng còn hơn giấu
    mất 1 tin thị trường thật sự khác biệt.

    Chỉ hạ processed=0 -> SKIPPED_DUPLICATE, KHÔNG bao giờ đụng bài đã tóm tắt
    (processed=1): nếu 1 nguồn đã lên feed rồi, các nguồn đến sau trùng tin bị
    gộp vào nguồn đó; nếu tất cả còn mới, giữ lại bài dài nhất (raw_text đầy
    đủ nhất) làm bản canonical cho summarize.py xử lý.
    """
    with conn() as c:
        rows = c.execute(
            """SELECT id, source_id, title, raw_text, processed
               FROM articles
               WHERE published >= datetime('now', ?) AND processed IN (0, 1)""",
            (f"-{hours} hours",),
        ).fetchall()

    items = [dict(r) for r in rows]
    for it in items:
        it["tok"] = _title_tokens(it["title"])
        it["ent"] = _entity_tokens(it["title"])

    n_marked = 0
    seen = set()
    for i, a in enumerate(items):
        if a["id"] in seen or not a["tok"]:
            continue
        group = [a]
        for b in items[i + 1:]:
            if b["id"] in seen or b["source_id"] == a["source_id"]:
                continue
            if _is_same_story(a, b, threshold):
                group.append(b)
        if len(group) < 2:
            continue
        for it in group:
            seen.add(it["id"])
        # ưu tiên bài đã tóm tắt (processed=1) làm canonical — khỏi tốn LLM
        # thêm lần nào; nếu chưa bài nào tóm tắt, chọn bài đầy đủ nội dung nhất.
        already = [it for it in group if it["processed"] == 1]
        canonical = already[0] if already else max(group, key=lambda it: len(it["raw_text"] or ""))
        dupes = [it for it in group if it["id"] != canonical["id"] and it["processed"] == 0]
        if not dupes:
            continue
        with conn() as c:
            for it in dupes:
                c.execute("UPDATE articles SET processed = ? WHERE id = ?",
                          (SKIPPED_DUPLICATE, it["id"]))
        n_marked += len(dupes)

    if n_marked:
        print(f"  [·] Gộp {n_marked} bài trùng tin (khác nguồn, cùng sự kiện) — bỏ qua LLM.")
    return n_marked


def run():
    with conn() as c:
        sources = c.execute(
            "SELECT * FROM sources WHERE active = 1 AND kind IN ('rss','substack','scrape','youtube')"
        ).fetchall()

    print(f"Fetching {len(sources)} sources...")
    rss_sources = [s for s in sources if s["kind"] in ("rss", "substack")]
    scrape_sources = [s for s in sources if s["kind"] == "scrape"]
    youtube_sources = [s for s in sources if s["kind"] == "youtube"]

    total = 0
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as ex:
        futures = {ex.submit(fetch_source, s): s for s in rss_sources}
        futures.update({ex.submit(fetch_scrape_source, s): s for s in scrape_sources})
        futures.update({ex.submit(fetch_youtube_source, s): s for s in youtube_sources})
        for f in as_completed(futures):
            total += f.result()

    print(f"\nTổng: {total} bài mới")
    dedup_cross_source()
    return total


if __name__ == "__main__":
    run()
