-- FinHub schema. SQLite cho simple, đổi sang Postgres khi cần.

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,        -- "Matt Levine", "VnEconomy"
    kind        TEXT NOT NULL,               -- rss | substack | youtube | scrape
    url         TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT 'en',  -- en | vi
    region      TEXT NOT NULL DEFAULT 'global', -- global | us | vn | em
    expert_name TEXT,                        -- NULL nếu không phải personal feed
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    url         TEXT NOT NULL UNIQUE,        -- dedupe key chính
    url_hash    TEXT NOT NULL UNIQUE,        -- sha1 của canonical url
    title       TEXT NOT NULL,
    author      TEXT,
    published   TEXT NOT NULL,               -- ISO datetime
    fetched     TEXT NOT NULL,
    raw_text    TEXT,                        -- nội dung gốc đã strip HTML

    -- LLM output
    summary_vi  TEXT,                        -- 2–3 câu tiếng Việt
    summary_en  TEXT,                        -- 2–3 câu tiếng Anh
    thesis      TEXT,                        -- luận điểm chính / data point
    importance  INTEGER,                     -- 1–5, Sonnet chấm
    topics      TEXT,                        -- JSON array: ["rates","em","vn-banks"]
    tickers     TEXT,                        -- JSON array các mã VN trong universe

    processed   INTEGER NOT NULL DEFAULT 0   -- đã qua LLM chưa
);

CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published DESC);
CREATE INDEX IF NOT EXISTS idx_articles_importance ON articles(importance DESC, published DESC);
CREATE INDEX IF NOT EXISTS idx_articles_processed ON articles(processed);

CREATE TABLE IF NOT EXISTS saved (
    article_id  INTEGER PRIMARY KEY REFERENCES articles(id),
    saved_at    TEXT NOT NULL
);
