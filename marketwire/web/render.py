"""Render static HTML từ DB. Output vào web/dist/, deploy lên Cloudflare Pages.

Pages:
- index.html: feed mới nhất
- important.html: importance >= 4
- portfolio.html: chỉ bài có ticker trong universe
- briefing.html: morning briefing = wavy signals + sell-side + ★4+ news
- expert/<slug>.html: per-expert feed
- daily/YYYY-MM-DD.html: digest theo ngày
"""
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

from jinja2 import Environment, FileSystemLoader, select_autoescape
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "ingest"))
from db import conn

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
TPL = ROOT / "templates"

env = Environment(
    loader=FileSystemLoader(TPL),
    autoescape=select_autoescape(["html"]),
)


def slugify(s):
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


def load_articles(where: str = "1=1", limit: int = 500, order: str = "a.published DESC"):
    with conn() as c:
        rows = c.execute(f"""
            SELECT a.*, s.name AS source_name, s.expert_name, s.region, s.lang
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

    window = "a.published >= datetime('now', '-24 hours')"
    imp_then_time = "COALESCE(a.importance,0) DESC, a.published DESC"

    # Index: importance trước, time sau, 24h
    all_articles = load_articles(where=window, limit=200, order=imp_then_time)
    (DIST / "index.html").write_text(
        env.get_template("feed.html").render(
            title="MarketWire — Hôm nay",
            articles=all_articles,
            nav_active="index",
        ), encoding="utf-8"
    )

    # Important: ★4+ trong 24h
    important = [a for a in all_articles if (a["importance"] or 0) >= 4]
    (DIST / "important.html").write_text(
        env.get_template("feed.html").render(
            title="MarketWire — Quan trọng ★4+",
            articles=important,
            nav_active="important",
        ), encoding="utf-8"
    )

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
                articles=arts, nav_active="expert",
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
                articles=arts, the_date=d,
            ), encoding="utf-8"
        )

    render_briefing(today=date.today())
    print(f"Rendered {len(all_articles)} articles to {DIST}")


def render_briefing(today: date = None):
    if today is None:
        today = date.today()

    # 1. Wavy signals — tìm file của hôm nay hoặc hôm qua
    screener_dir = ROOT.parent.parent / "screener-output"
    signals = []
    for delta in (0, 1):
        candidate = screener_dir / f"wavy_signals_{(today - timedelta(days=delta)).isoformat()}.json"
        if candidate.exists():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            signals = data.get("signals", [])
            break

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

    (DIST / "briefing.html").write_text(
        env.get_template("briefing.html").render(
            title=f"Morning Briefing — {today.isoformat()}",
            scan_date=today.isoformat(),
            signals=signals,
            sellside=sellside,
            news=star4_news,
            nav_active="briefing",
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    render_all()
