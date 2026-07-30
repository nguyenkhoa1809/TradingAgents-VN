-- Migration: thêm cột detail_vi — bản tóm tắt đầy đủ có cấu trúc cho video
-- YouTube (Macro / Thị trường / Cổ phiếu / Khuyến nghị). Bài RSS thường để NULL.
-- Idempotent: migrate.py bắt "duplicate column" nếu chạy lại.

ALTER TABLE articles ADD COLUMN detail_vi TEXT;
