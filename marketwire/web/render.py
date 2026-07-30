"""Render static HTML từ DB. Output vào web/dist/, deploy lên Cloudflare Pages.

Pages:
- index.html: feed mới nhất
- important.html: importance >= 4
- portfolio.html: chỉ bài có ticker trong universe
- briefing.html: morning briefing = wavy signals + sell-side + ★4+ news
- expert/<slug>.html: per-expert feed
- daily/YYYY-MM-DD.html: digest theo ngày
- blog/index.html, blog/<slug>.html: bài viết Markdown trong blog/posts/
"""
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

import frontmatter
import markdown as md_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "ingest"))
from db import conn

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
TPL = ROOT / "templates"
BLOG_POSTS_DIR = ROOT.parent / "blog" / "posts"
BLOG_DIST = DIST / "blog"

env = Environment(
    loader=FileSystemLoader(TPL),
    autoescape=select_autoescape(["html"]),
)


def slugify(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


def load_articles(where: str = "1=1", limit: int = 500, order: str = "a.published DESC"):
    with conn() as c:
        rows = c.execute(f"""
            SELECT a.*, s.name AS source_name, s.expert_name, s.region, s.lang, s.kind
            FROM articles a JOIN sources s ON a.source_id = s.id
            WHERE a.processed = 1 AND {where}
            ORDER BY {order}
            LIMIT {limit}
        """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["topics"] = json.loads(d["topics"] or "[]")
        d["tickers"] = json.loads(d["tickers"] or "[]")
        d["hits_holdings"] = json.loads(d["hits_holdings"] or "null") or []
        d["published_dt"] = datetime.fromisoformat(d["published"])
        out.append(d)
    return out


def render_all():
    DIST.mkdir(exist_ok=True)
    (DIST / "expert").mkdir(exist_ok=True)
    (DIST / "daily").mkdir(exist_ok=True)
    BLOG_DIST.mkdir(exist_ok=True)

    window = "a.published >= datetime('now', '-24 hours')"
    imp_then_time = "COALESCE(a.importance,0) DESC, a.published DESC"

    # Index: importance trước, time sau, 24h. Video (youtube) không tính vào
    # đây — tách riêng qua Expert Talk để khỏi lẫn với tin cào từ báo.
    all_articles = load_articles(where=window, limit=200, order=imp_then_time)
    news_articles = [a for a in all_articles if a["kind"] != "youtube"]
    (DIST / "index.html").write_text(
        env.get_template("feed.html").render(
            title="MarketWire — Hôm nay",
            articles=news_articles,
            nav_active="index",
        ), encoding="utf-8"
    )

    # Expert Talk: video/commentary từ chuyên gia (YouTube) — không giới hạn
    # 24h như index, vì đây là điểm đến riêng để xem lại, không phải feed cuộn.
    render_expert_talk()

    # Portfolio (universe)
    portfolio = [a for a in all_articles if a["tickers"]]
    (DIST / "portfolio.html").write_text(
        env.get_template("feed.html").render(
            title="MarketWire — Universe",
            articles=portfolio,
            nav_active="portfolio",
        ), encoding="utf-8"
    )

    # Holdings: bài chạm mã đang nắm, sorted importance→time
    holdings_hits = [a for a in all_articles if a["hits_holdings"]]
    (DIST / "holdings.html").write_text(
        env.get_template("feed.html").render(
            title="MarketWire — 🎯 Holdings",
            articles=holdings_hits,
            nav_active="holdings",
        ), encoding="utf-8"
    )

    # Per-expert
    by_expert = defaultdict(list)
    for a in all_articles:
        if a["expert_name"]:
            by_expert[a["expert_name"]].append(a)
    for name, arts in by_expert.items():
        (DIST / "expert" / f"{slugify(name)}.html").write_text(
            env.get_template("feed.html").render(
                title=f"MarketWire — {name}",
                articles=arts, nav_active="expert", nav_prefix="../",
            ), encoding="utf-8"
        )

    # Daily digest (7 ngày gần nhất)
    by_day = defaultdict(list)
    for a in all_articles:
        by_day[a["published_dt"].date()].append(a)
    for d, arts in list(sorted(by_day.items(), reverse=True))[:7]:
        arts.sort(key=lambda x: -(x["importance"] or 0))
        (DIST / "daily" / f"{d.isoformat()}.html").write_text(
            env.get_template("daily.html").render(
                title=f"Digest {d.isoformat()}",
                articles=arts, the_date=d, nav_prefix="../",
            ), encoding="utf-8"
        )

    render_briefing(today=date.today())
    render_blog()
    print(f"Rendered {len(all_articles)} articles to {DIST}")


def render_expert_talk() -> None:
    """Video/commentary từ chuyên gia (nguồn kind='youtube') — feed riêng,
    không giới hạn 24h, để không lẫn với tin cào từ báo (index.html)."""
    articles = load_articles(where="s.kind = 'youtube'", limit=200, order="a.published DESC")
    (DIST / "expert-talk.html").write_text(
        env.get_template("feed.html").render(
            title="MarketWire — 🎙️ Expert Talk",
            articles=articles,
            nav_active="expert-talk",
        ), encoding="utf-8"
    )


def _load_blog_posts() -> list:
    """Đọc + parse mọi .md trong blog/posts/, sort theo date giảm dần."""
    if not BLOG_POSTS_DIR.exists():
        return []
    posts = []
    for md_path in sorted(BLOG_POSTS_DIR.glob("*.md")):
        post = frontmatter.load(md_path)
        html_content = md_lib.markdown(
            post.content, extensions=["tables", "fenced_code", "nl2br"]
        )
        date_val = post.get("date")
        date_str = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
        posts.append({
            "slug": md_path.stem,  # tên file (không .md) — trùng luôn tên output .html
            "title": post.get("title", md_path.stem),
            "date": date_str,
            "tags": post.get("tags", []),
            "lang": post.get("lang", "vi"),
            "excerpt": post.get("excerpt", ""),
            "content_html": html_content,
        })
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def render_blog() -> None:
    """Render blog/index.html (listing) + blog/<slug>.html (từng bài)."""
    posts = _load_blog_posts()
    BLOG_DIST.mkdir(parents=True, exist_ok=True)

    (BLOG_DIST / "index.html").write_text(
        env.get_template("blog_list.html").render(
            title="MarketWire — Blog", posts=posts,
            nav_active="blog", nav_prefix="../",
        ), encoding="utf-8"
    )
    for post in posts:
        (BLOG_DIST / f"{post['slug']}.html").write_text(
            env.get_template("blog_post.html").render(
                title=f"MarketWire — {post['title']}", post=post,
                nav_active="blog", nav_prefix="../",
            ), encoding="utf-8"
        )
    print(f"[blog] Rendered {len(posts)} post(s) to {BLOG_DIST}")


def render_briefing(today: date = None):
    if today is None:
        today = date.today()

    # 1. Wavy signals — file gốc do wavy_scheduler.py sinh ra nằm ngoài git
    # repo (screener-output/ là sibling của TradingAgents/), GitHub Actions
    # runner không bao giờ thấy được thư mục đó. Sync qua R2 thay vì đọc local
    # (xem scripts/wavy_sync.py — chạy `upload` trên máy local sau khi
    # wavy_scheduler.py xong, `download` được thêm vào bước CI trước render).
    wavy_path = ROOT.parent / "data" / "wavy_signals_latest.json"
    signals = []
    wavy_scan_date = None
    if wavy_path.exists():
        data = json.loads(wavy_path.read_text(encoding="utf-8"))
        scan_date = data.get("scan_date", "")
        # Briefing chạy buổi sáng, TRƯỚC lần scan wavy trong ngày (~15h ICT) —
        # nên file của "hôm nay" chưa tồn tại lúc render, check hôm nay là vô
        # nghĩa. Nhận scan của 1-3 ngày trước (đủ phủ nghỉ cuối tuần: sáng thứ
        # Hai vẫn thấy tín hiệu thứ Sáu). wavy_scan_date hiện rõ ngày của scan
        # đang xem để không nhầm là của hôm nay.
        valid_dates = {(today - timedelta(days=d)).isoformat() for d in (1, 2, 3)}
        if scan_date in valid_dates:
            signals = data.get("signals", [])
            wavy_scan_date = scan_date

    # 2. Sell-side notes từ DB
    window_24h = "a.published >= datetime('now', '-24 hours')"
    sellside = load_articles(
        where=f"{window_24h} AND s.name = 'Sell-side Notes'",
        limit=50,
        order="a.published DESC",
    )

    # 3. ★4+ macro/market news từ DB (trừ sell-side)
    star4_news = load_articles(
        where=f"{window_24h} AND COALESCE(a.importance,0) >= 4 AND s.name != 'Sell-side Notes'",
        limit=30,
        order="COALESCE(a.importance,0) DESC, a.published DESC",
    )

    ctx = dict(
        title=f"Morning Briefing — {today.isoformat()}",
        scan_date=today.isoformat(),
        signals=signals,
        wavy_scan_date=wavy_scan_date,
        sellside=sellside,
        news=star4_news,
        nav_active="briefing",
    )
    tpl = env.get_template("briefing.html")

    # Trang "Briefing" trong nav luôn là bản mới nhất, bị ghi đè mỗi lần render.
    (DIST / "briefing.html").write_text(tpl.render(**ctx), encoding="utf-8")

    # Archive theo ngày — không bao giờ bị ghi đè, để xem lại briefing ngày cũ.
    archive_dir = DIST / "daily-briefing"
    archive_dir.mkdir(exist_ok=True)
    (archive_dir / f"{today.isoformat()}.html").write_text(
        tpl.render(**{**ctx, "nav_prefix": "../", "nav_active": "briefing-archive"}),
        encoding="utf-8",
    )
    render_briefing_archive_index(archive_dir)


def render_briefing_archive_index(archive_dir: Path) -> None:
    dates = sorted(
        (p.stem for p in archive_dir.glob("*.html") if p.stem != "index"),
        reverse=True,
    )
    (archive_dir / "index.html").write_text(
        env.get_template("briefing_archive.html").render(
            title="MarketWire — Lịch sử Briefing",
            dates=dates,
            nav_active="briefing-archive",
            nav_prefix="../",
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    render_all()
