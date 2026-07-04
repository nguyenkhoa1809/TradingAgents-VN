-- Migration: thêm cột hits_holdings (JSON array các mã chạm holdings hiện tại)
-- Idempotent: chạy nhiều lần OK vì SQLite sẽ báo lỗi nếu cột đã tồn tại.

ALTER TABLE articles ADD COLUMN hits_holdings TEXT;
CREATE INDEX IF NOT EXISTS idx_articles_holdings ON articles(hits_holdings) WHERE hits_holdings IS NOT NULL;
