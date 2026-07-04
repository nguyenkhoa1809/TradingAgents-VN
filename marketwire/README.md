# MarketWire

Personal news aggregator cho VN equity PM — gom feed RSS từ chuyên gia macro/markets quốc tế + báo VN, LLM tóm tắt + cross-reference với holdings TGF/KDEF.

## Edge

- **Holdings-aware**: bài chạm mã đang nắm được highlight + ưu tiên hiển thị
- **Bilingual**: summary tiếng Việt + tiếng Anh
- **Provider-agnostic**: swap Claude ↔ DeepSeek qua config, không sửa code

## Chi phí

| Config | Cost/tháng (~100 bài/ngày) |
|---|---|
| DeepSeek V4 Flash (bulk only) | ~$1.6 |
| **Hybrid: Flash bulk + Pro rerank** ← *đang dùng* | ~$2.1 |
| Hybrid: Flash bulk + Sonnet rerank | ~$5-6 |
| Claude Haiku (all) | ~$15 |

Hosting: Cloudflare Pages (free). Domain: optional ~$15/năm.

## Cấu trúc

```
marketwire/
├── schema.sql
├── sources.yaml          # Feeds + universe + LLM config
├── migrations/
├── data/
│   ├── marketwire.db
│   └── holdings.csv      # Update mỗi sáng từ KIS export
├── ingest/
│   ├── db.py
│   ├── llm.py            # Provider abstraction (Claude / DeepSeek)
│   ├── migrate.py
│   ├── seed_sources.py
│   ├── holdings.py
│   ├── fetch.py
│   └── summarize.py
├── web/
│   ├── render.py
│   ├── templates/
│   └── dist/             # → Cloudflare Pages
└── scripts/
    └── run_daily.py
```

## Quickstart

```powershell
# 1. Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. API key — đặt trong file .env (xem mẫu bên dưới), KHÔNG paste vào đây
# Tạo file marketwire/.env:
#   DEEPSEEK_API_KEY=sk-...        (platform.deepseek.com)
#   ANTHROPIC_API_KEY=sk-ant-...   (console.anthropic.com — chỉ cần nếu dùng claude)

# 3. Init
python ingest/db.py
python ingest/migrate.py
python ingest/seed_sources.py

# 4. Chạy pipeline
python scripts/run_daily.py

# 5. Preview
cd web/dist && python -m http.server 8000
```

## Đổi LLM provider

Sửa `sources.yaml`, không cần sửa code:

```yaml
llm:
  default:                      # bulk summarize toàn bộ bài
    provider: deepseek
    model: deepseek-v4-flash    # hoặc deepseek-v4-pro / claude-haiku-4-5
  rerank:                       # chỉ chạy trên top 20 bài quan trọng nhất
    provider: deepseek
    model: deepseek-v4-pro      # hoặc claude-sonnet-4-6 nếu muốn chất lượng tối đa
    enabled: true               # false = tắt rerank, dùng Flash cho tất cả
```

API keys đặt trong `.env` (không commit):
```
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...   # chỉ cần nếu dùng provider: claude
```

## Holdings CSV format

`data/holdings.csv` — sửa `ingest/holdings.py` nếu KIS export format khác:

```csv
ticker,fund,weight,shares
VCB,TGF,8.5,150000
FPT,KDEF,8.0,40000
```

## Web views

- **Mới nhất** — feed toàn bộ
- **Quan trọng** — importance ≥ 4
- **🎯 Holdings** — bài chạm mã đang nắm (highlight border xanh)
- **Universe** — bài chạm bất kỳ mã nào trong universe
- **Per-expert** — feed riêng từng chuyên gia
- **Daily digest** — 7 ngày gần nhất

## Deploy lên Cloudflare Pages

```powershell
npm i -g wrangler
wrangler login
wrangler pages deploy web/dist --project-name=marketwire
```

Hoặc push `web/dist` lên GitHub → CF Pages auto-deploy.

## Schedule (Windows Task Scheduler)

2 task/ngày chạy `python scripts/run_daily.py`:
- 06:30 ICT — bắt overnight US
- 15:30 ICT — sau EU mở cửa

## TODO

- [ ] Hybrid rerank (DeepSeek bulk + Sonnet top 20)
- [ ] Scraper cho SBV/MoF (không có RSS)
- [ ] Telegram/email push khi có bài ★≥4 chạm holdings
- [ ] Macro dashboard widget (DXY, UST10Y, VN10Y, USDVND)
- [ ] Embedding dedup (cùng story từ nhiều nguồn)

## Notes

- Không commit `data/marketwire.db` và `web/dist/`.
- Rotate API key định kỳ; KHÔNG để key trong sources.yaml.
- DeepSeek dùng server TQ — nếu sau này pipeline xử lý nội dung nội bộ, cân nhắc switch về Claude.
