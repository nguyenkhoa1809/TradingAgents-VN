"""Fetch RSS feeds, parse, dedupe theo url_hash, insert raw vào articles.

LLM summarize/tag để pipeline sau xử lý (xem summarize.py).
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import hashlib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from db import conn

PARALLEL_WORKERS = 6
TIMEOUT = 20
USER_AGENT = "MarketWire/1.0 (personal aggregator)"


def url_hash(url: str) -> str:
    return hashlib.sha1(url.strip().lower().encode()).hexdigest()


def strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
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

        h = url_hash(link)
        with conn() as c:
            if c.execute("SELECT 1 FROM articles WHERE url_hash = ?", (h,)).fetchone():
                continue

        try:
            ytt = YouTubeTranscriptApi()
            segments = ytt.fetch(video_id, languages=["en", "en-US", "vi", "vi-VN"])
        except Exception:
            # fallback: thử auto-generated bất kỳ ngôn ngữ
            try:
                ytt = YouTubeTranscriptApi()
                tlist = ytt.list(video_id)
                gen = tlist.find_generated_transcript(["en", "vi"])
                segments = gen.fetch()
            except Exception:
                print(f"    [-] {name}: no transcript for {video_id}")
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
    return total


if __name__ == "__main__":
    run()
