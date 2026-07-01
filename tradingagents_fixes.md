# TradingAgents-VN — Report Pipeline Fixes

**Context:** Multi-agent LLM pipeline sinh báo cáo phân tích cổ phiếu HOSE/HNX, output HTML per ticker (5 phase: Analyst → Research → Trader → Risk → Portfolio Manager). Bản review dưới đây dựa trên report mẫu `PVT_2026-06-29_deepseek-pro_v1.html`.

Hai loại lỗi, fix bằng hai cách khác nhau:
- **Phần A — Code/pipeline:** lỗi dữ liệu & render. Đây là phần Claude Code làm chính.
- **Phần B — Prompt/agent design:** lỗi chất lượng lập luận. Không sửa bằng code, chỉ sửa bằng prompt/orchestration.

Làm Phần A trước. Phần B làm sau khi dữ liệu đã sạch.

---

## TRẠNG THÁI

- **Round 1 (A1–A7, B1–B4): ✅ ĐÃ HOÀN THÀNH** — verify ở bản `v2` (PVT_2026-06-29_deepseek-pro_v2.html). KHÔNG làm lại các mục này; code tương ứng đã đúng.
- **Round 2 (A8–A10, B5–B7): 🔲 CẦN LÀM** — lỗi validator báo nhầm, font loạn, vài lỗi logic số.
- **Round 3 (C1–C5): 🔲 CẦN LÀM** — lớp lỗi MỚI: hallucination factual (gán nhầm thực thể) lan nhiễm xuyên suốt report. Cần grounded fact-check gate. Xem cuối file.
- **Round 4 (D1–D6): 🔲 CẦN LÀM** — nâng cấp KHUNG PHÂN TÍCH & DEBATE cho MỌI mã (không phải sửa riêng report nào). Áp vào prompt dùng chung của Analyst/Research/PM. Xem cuối file.
- **Round 5 (E1–E4): 🔲 CẦN LÀM** — KIẾN TRÚC TRÌNH BÀY: rating mâu thuẫn xuyên suốt report (7 lần đảo chiều trong 1 report) + thuật ngữ nội bộ (D1-D6...) lọt vào output. Thêm bảng tổng hợp rating các agent + quyết định PM. Xem cuối file.
- **Round 6 (F1–F3): ⚠️ ĐÃ ĐIỀU TRA — xem Round 6B để biết spec chính xác.** Kết quả: nguồn variance là Phase I (Analyst agents) thiếu temperature/seed control, KHÔNG phải Phase II+V. Đồng thời phát hiện "convergence giả": rating trùng nhau nhưng EV bên dưới dao động mạnh.
- **Round 6B (G1–G3): 🔲 CẦN LÀM — spec chính xác thay thế Round 6.** G1 = temperature/seed cho Phase I + V. G2 = sensitivity TRONG 1 LẦN CHẠY (không cần N runs — phù hợp ngân sách, xem ghi chú trong mục G2). G3 = fix lỗi parse Trader N/A âm thầm.
- **Round 7 (I1–I3): ⚠️ ĐÃ ĐIỀU TRA — anchoring bị loại, vấn đề thật là FABRICATED CITATION.** Xác nhận qua log: Fundamentals agent ở PVD v3 tự tính 40.3 (đúng quy trình, từ raw data) rồi BỊA "Analyst Phuoc Duong, Vietcap, 09/04/2026" — citation này không có trong context (News agent chưa chạy, marketwire rỗng). Số 40.3 trùng Vietcap thật chỉ là ngẫu nhiên; cái tên nguồn là confabulation hoàn toàn. Nguy hiểm hơn Round 3 vì có cấu trúc giống thật (tên người/tổ chức/ngày cụ thể) và không thể phát hiện nếu không có ảnh chụp đối chiếu. Spec: cấm agent tự trích nguồn ngoài không có trong context (I1) + validator tự động quét tên riêng/tổ chức không grounded (I2) + test case riêng cho anchoring thật khi CÓ broker note (I3).

> Lưu ý cho Claude Code: chỉ đụng vào code liên quan Round 2. Nếu phát hiện regression ở Round 1 thì báo, đừng tự refactor lại phần đã đúng.

---

## ROOT CAUSE (đọc trước khi sửa)

Mỗi agent đang **tự fetch/sinh lại số liệu tài chính riêng** thay vì dùng chung một nguồn. Bằng chứng: agent Fundamentals và agent Portfolio Manager in ra **hai bộ P&L khác nhau** cho cùng một mã, cùng một ngày (xem A1). Phần lớn lỗi A bắt nguồn từ đây. Fix kiến trúc này (A1) sẽ tự động dập ~70% các lỗi còn lại.

---

## PHẦN A — CODE / PIPELINE  ·  ✅ ROUND 1 DONE (v2)

### A1. Single source of truth cho số liệu tài chính ⚠️ ROOT CAUSE
**Vấn đề:** Bảng tài chính trong mục Fundamentals và mục Portfolio Manager mâu thuẫn nhau:
- Doanh thu 2025: 16,013 (Fundamentals) vs 15,840 (PM)
- LNST 2024: 1,093 (Fundamentals) vs 1,458 (PM) — lệch 33%

**Fix:**
- Fetch tài chính **một lần** (vnstock), normalize thành 1 dict/JSON `financials` (income / balance / cashflow + các ratio đã tính sẵn).
- Inject `financials` vào context của **mọi** agent. Agent chỉ được TRÍCH DẪN từ payload, **cấm tự sinh số**. Thêm chỉ thị rõ trong system prompt mỗi agent: "Mọi con số tài chính phải lấy từ `financials` đã cung cấp. Không được tự tính lại hay ước lượng."

**Acceptance:** Mọi bảng/con số tài chính trong report (mọi phase) phải trùng khớp tuyệt đối với `financials`.

### A2. Tính các ratio một chỗ, không để LLM tự tính
**Vấn đề:** Các ratio do LLM tự tính ra bị sai/mâu thuẫn:
- Net margin 2025: 6.5% (bảng đầu, = 1038/16013, đúng) vs 8.3% (Fundamentals, không reconcile được)
- ROE 2025: 12.7% (DuPont) vs 9.7% (PM); ROE 2023: 15.2% vs 14%
- ROIC "18% → 12%" và "P/B 1.2x", "ngành 1.5x" — xuất hiện từ hư không, không nguồn

**Fix:** Tính tất cả ratio (margin, ROE, ROA, ROIC, P/E, P/B, D/E, quick ratio, FCF/rev...) bằng Python trong layer `financials`, đưa kết quả vào payload. LLM chỉ diễn giải, không tính.

**Acceptance:** `net_margin == LNST / DT` cho mọi năm; ROE/P/B/D/E giống nhau ở mọi mục của report.

### A3. FCF — chốt một định nghĩa
**Vấn đề:** Report tự mâu thuẫn về FCF:
- Fundamentals: "FCF âm 3 năm liên tiếp (-2,487 / -1,398 / -579)" → dùng làm trụ cột Bear
- Bảng cashflow của PM: FCF = 600 / 800 / 661 / 250 (DƯƠNG cả 4 kỳ)
- Đây là sai số do A1 (capex/CFI hai agent khác nhau)

**Fix:** Định nghĩa FCF cố định trong code (`FCF = CFO - CapEx`, CapEx lấy từ CFI mục mua sắm TSCĐ), tính một lần trong `financials`. Xóa mọi chỗ LLM tự nói FCF.

**Acceptance:** Chỉ tồn tại MỘT chuỗi FCF trong toàn report, khớp công thức.

### A4. Render layer — không để markdown thô lọt ra HTML
**Vấn đề:** Mục "Phân tích Định lượng" (trong PM) in nguyên cú pháp markdown table `| Chỉ số | 2023 |` và `|---|---|`, và để nguyên text `[TÍCH CỰC]/[TIÊU CỰC]/[TRUNG LẬP]` thay vì badge màu.

**Fix:**
- Parse markdown table → HTML `<table>` (dùng `markdown` lib hoặc regex pipe-parser) trước khi nhúng vào template.
- Map `[TÍCH CỰC]`→badge xanh, `[TIÊU CỰC]`→đỏ, `[TRUNG LẬP]`→xám.
- Đảm bảo MỌI output LLM đi qua render layer này, không có đường tắt nhúng raw text.

**Acceptance:** Không còn ký tự `|`, `---`, `[TÍCH CỰC]` dạng raw trong HTML cuối.

### A5. Format số & đơn vị thống nhất
**Vấn đề:**
- Lẫn locale: "16,013" (phẩy) vs "15.840" (chấm) trong cùng report
- Giá ba kiểu: "20.1 nghìn đ" / "20,050 VND" / "20,100 VND"
- "+319.0%" — giá trị tuyệt đối 319 tỷ bị format nhầm thành % (đúng ra +48% YoY)

**Fix:** Một hàm format duy nhất (đề xuất chuẩn VN: nghìn = `.`, thập phân = `,`, hoặc thống nhất EN — miễn là một kiểu). Tách rõ field "giá trị tuyệt đối" và field "% biến động", validate `%` nằm trong khoảng hợp lý (vd |x| < 1000%).

**Acceptance:** Một locale duy nhất toàn report; giá một biểu diễn duy nhất; không có % vượt ngưỡng vô lý.

### A6. Alpha tính đúng
**Vấn đề:** Header ghi "Alpha -33%" = chỉ lấy hiệu suất tương đối (15% − 48%), bỏ qua beta 0.73.

**Fix:** `alpha = r_stock - beta * r_index`. Với số mẫu: 15% − 0.73×48% ≈ −20%. Tính trong code, hoặc đổi nhãn thành "Relative return" nếu cố ý không điều chỉnh beta.

**Acceptance:** Nhãn "Alpha" = công thức có beta; hoặc đổi tên đúng.

### A7. Validator reconciliation BẮT BUỘC trước khi render
**Vấn đề:** Không có bước kiểm tra nhất quán → report ra ngoài với mâu thuẫn nội bộ.

**Fix:** Thêm hàm `validate_report(financials, agent_outputs)` chạy trước render, assert:
- FCF chỉ có 1 chuỗi, khớp công thức
- `net_margin[y] == round(LNST[y]/DT[y], 3)` mọi năm
- P/B dùng ở mục định giá == P/B áp dụng ra fair value
- ROE/D/E/P/B giống nhau giữa các phase
- Không có % > ngưỡng
- Nếu fail → **BLOCK**: log lỗi cụ thể (chỉ rõ assert nào fail, giá trị thực vs kỳ vọng), KHÔNG render HTML, raise exception để dừng pipeline. Cho thử regenerate tối đa N lần (vd N=2); hết N lần vẫn fail thì raise và dừng hẳn.
- (Tùy chọn) thêm flag `--dev` để chuyển sang chế độ cảnh báo + render kèm warning banner, dùng khi debug. Mặc định (production) là block.

**Acceptance:** Report fail validator thì KHÔNG có file HTML nào được tạo ra; pipeline dừng với thông báo lỗi rõ ràng.

---

## PHẦN B — PROMPT / AGENT DESIGN (làm sau Phần A)

> ✅ ROUND 1 DONE (v2). Những lỗi này KHÔNG sửa bằng code. Sửa bằng cách viết lại prompt của Research agent và Portfolio Manager agent.

### B1. Bỏ logic "ai thắng debate", thay bằng khung EV
**Vấn đề:** Research & PM kết luận theo kiểu "Bear thắng tranh luận" → không phải cơ sở định cỡ vị thế. Tệ hơn: mục kịch bản gán xác suất 60% tích cực / 30% trung tính / 10% tiêu cực với payoff +25%/0%/−25% → EV = +12.5% (nghiêng Overweight), nhưng report ra Underweight. Tự mâu thuẫn.

**Fix prompt (PM agent):** Buộc PM:
1. Lấy đúng xác suất kịch bản đã sinh ở phase trước.
2. Tính EV có trọng số = Σ(prob × payoff).
3. Ra rating TỪ EV + mức tin cậy, không từ "ai thắng".
4. Nếu rating ngược dấu EV → phải giải thích lý do rõ ràng (vd rủi ro đuôi, thanh khoản) hoặc flag review.

### B2. Bắc cầu định giá ↔ khuyến nghị
**Vấn đề:** Mục định giá nói "rẻ, upside +24–26%, biên an toàn hấp dẫn"; mục cuối nói "bán 30–50%, Underweight" — không câu nào nối hai kết luận.

**Fix prompt:** PM agent phải có một đoạn bắt buộc reconcile: "Định giá cho upside X%, nhưng tôi khuyến nghị Y vì..." Nếu không nối được logic thì rating sai.

### B3. Burden of proof đối xứng
**Vấn đề:** "Bear thắng vì Bull không chứng minh được Q2 hồi phục" — nhưng Bear cũng đang dự báo tương lai (margin chưa đáy, BDI yếu), không bên nào chứng minh được. Q1/2026 LNST 319 tỷ (cao nhất 5 quý, +48% YoY) là bằng chứng thực bị gạt đi.

**Fix prompt (Research judge):** Yêu cầu đánh giá cả hai bên theo cùng tiêu chuẩn bằng chứng; nêu rõ data point nào confirmed vs forecast cho CẢ HAI bên trước khi kết luận.

### B4. Bỏ lặp luận điểm 4 lần
**Vấn đề:** Executive Summary / Investment Thesis / Research / Kết luận đều nhắc lại y nguyên 4 trụ cột Bear.

**Fix:** Mỗi phase chỉ thêm thông tin MỚI. Phase sau tham chiếu phase trước, không lặp lại.

---

## THỨ TỰ ĐỀ XUẤT
1. **A1 + A2 + A3** (single source of truth + ratio + FCF) — dập gốc rễ.
2. **A7** (validator) — để bắt regression ngay.
3. **A4 + A5 + A6** (render + format + alpha) — dọn bề mặt.
4. **B1 → B4** (prompt) — sau khi dữ liệu đã sạch và validator đã chạy.

## TEST CASE
Dùng chính `PVT_2026-06-29` làm regression test: regenerate và kiểm tra tất cả Acceptance ở trên đều pass.

---
---

# ROUND 2 🔲 CẦN LÀM

> Dựa trên review bản `PVT_2026-06-29_deepseek-pro_v2.html`. Round 1 đã sạch các mâu thuẫn nặng (FCF, net margin, alpha, EV, bắc cầu định giá). Round 2 là các lỗi còn sót: validator báo nhầm, font loạn, và vài lỗi logic số trong phần lập luận. Giữ nguyên acceptance-criteria style như Round 1.

## PHẦN A — CODE / PIPELINE (Round 2)

### A8. Validator locale parser — FIX TRƯỚC KHI BẬT BLOCK ⚠️
**Vấn đề:** Validator báo false positive. "12,94%" (= 12.94%, dấu phẩy thập phân VN — tỷ lệ sở hữu khối ngoại) bị parse thành **1294%** rồi flag "% vô lý". Tức check `% > threshold` (A5/A7) chưa normalize locale VN trước khi so sánh.

**Fix:**
- Trước khi range-check `%`: normalize dấu phẩy thập phân VN → chấm. Quy tắc: chuỗi khớp `\d{1,2},\d{1,2}%` là **thập phân**, KHÔNG phải nghìn.
- Chỉ SAU khi sạch false positive mới bật **block** (đã chốt Round 1) cho mâu thuẫn THẬT: FCF, net margin, P/B chéo phase. Lý do thứ tự: nếu bật block khi parser còn sai, report đúng sẽ bị chặn oan.

**Acceptance:** "12,94%" không còn bị flag; validator block đúng các mâu thuẫn thật, không block số đúng định dạng VN.

### A9. Banner validator phải đi qua render layer (nối A4)
**Vấn đề:** Banner đang in raw markdown `**12,94%**` (asterisk chưa render) → bản thân banner chưa qua render layer.

**Fix:** Đẩy nội dung banner qua cùng render layer A4 (render `**bold**`, escape an toàn).

**Acceptance:** Banner không còn ký tự `**`, `|`, hay `[TÍCH CỰC]` dạng raw.

### A10. Type scale — dọn font-size loạn
**Vấn đề:** HTML hiện có **~28 giá trị font-size** khác nhau; riêng chữ thân bài đã 10 cỡ (10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 14 / 15px). Không có thang typography → "chỗ to chỗ nhỏ".

**Fix:** Định nghĩa ~6–7 CSS var trên một thang cố định: `--fs-h1, --fs-h2, --fs-h3, --fs-body, --fs-small, --fs-caption`. Refactor toàn template về dùng var. Cấm font-size rời rạc inline.

**Acceptance:** ≤ 8 giá trị font-size duy nhất trong toàn bộ HTML output.

## PHẦN B — PROMPT / AGENT DESIGN (Round 2)

### B5. Chống annualize ngây thơ + lỗi logic số (Research + PM agent)
**Vấn đề:** v2 neo định giá vào "319 × 4 = 1,276 tỷ" (annualize 1 quý), và viết câu vô lý *"LNST 319 tỷ — cao hơn mọi quý của năm 2025 (trừ Q2 ở 295 tỷ)"* — 319 > 295 nên không có ngoại lệ nào để "trừ". Ngoài ra 319 KHÔNG cao hơn Q3-2024 (365 tỷ), nên claim "cao nhất" chỉ đúng trong phạm vi 2025.

**Fix prompt:**
- Ưu tiên **TTM** thay vì quý × 4. Nếu buộc annualize, phải kèm cảnh báo mùa vụ rõ ràng.
- So sánh "cao nhất / cao hơn" phải trên cửa sổ ≥ 8 quý (gồm cả 2024). Bỏ kiểu diễn đạt "cao hơn mọi quý (trừ X)".

**Acceptance:** Không còn câu so sánh tự mâu thuẫn; mọi anchor lợi nhuận forward ghi rõ nguồn (TTM hay annualized + caveat).

### B6. Multiple mục tiêu phải gắn ROE forward (valuation logic)
**Vấn đề:** P/B target 1.25x lấy bình quân lịch sử 5 năm (thời ROE ~15%), áp lên doanh nghiệp giờ ROE 12.7% và margin co — mâu thuẫn ngầm với chính luận điểm của report. Cú premium +0.05 (avg 1.2 → target 1.25) không giải thích.

**Fix prompt:** Hoặc (a) hạ P/B target theo ROE hiện tại, hoặc (b) ghi rõ "1.25x là multiple CÓ ĐIỀU KIỆN vào kịch bản ROE hồi về 16–18%". Bắt buộc justify mọi premium/discount so với bình quân lịch sử.

**Acceptance:** Target multiple có justification gắn ROE; nêu rõ giả định nền.

### B7. Linh tinh
- Harmonize hoặc ghi chú xác suất kịch bản giữa các mục (kỹ thuật 60/25/15 vs PM EV 40/40/20 — khác framing, cần một dòng giải thích vì sao khác).
- Sửa thuật ngữ "lợi nhuận sau thu nhập" → "lợi nhuận sau thuế".

## THỨ TỰ ĐỀ XUẤT (Round 2)
1. **A8** (locale parser) trước — vì nó chặn việc bật block an toàn.
2. **A9 + A10** (banner render + type scale) — dọn bề mặt.
3. **B5 → B7** (prompt) — sau cùng.

## TEST CASE (Round 2)
Regen `PVT_2026-06-29`, kiểm tall acceptance Round 2 pass — đặc biệt: banner KHÔNG còn false positive "12,94%", và ≤ 8 font-size trong HTML.

---
---

# ROUND 3 🔲 — GROUNDED FACT-CHECK GATE

> **Lớp lỗi mới, KHÁC HẲN Round 1-2.** Round 1-2 xử lý *mâu thuẫn nội bộ* (số không khớp giữa các mục) — validator A7 bắt được vì có cái đối chiếu bên trong report. Round 3 xử lý *hallucination factual*: claim SAI nhưng NHẤT QUÁN xuyên suốt → A7 cho PASS vì không có gì mâu thuẫn để bắt. Muốn bắt phải có **ground truth ngoài**.

## CHẨN ĐOÁN — vì sao sai "xuyên suốt"

Ca mẫu: report `POW_2026-06-29_v1` gán nhầm nhà máy **Sông Hậu 1 (1.200MW)** là tài sản của POW (PV Power) và biến nó thành trụ cột phục hồi biên lợi nhuận. Thực tế Sông Hậu 1 do **PVN** làm chủ đầu tư, KHÔNG thuộc POW. Catalyst thật của POW là **Nhơn Trạch 3 & 4 (LNG)**.

Cơ chế lỗi = **context poisoning (lan nhiễm context)**, KHÔNG phải 5 lần hallucinate độc lập:
1. Agent Fundamentals (chạy sớm) bịa claim từ trí nhớ tham số của model.
2. Output đó thành context của các agent sau (DuPont, EV, Research, PM).
3. Các agent sau **thừa kế** claim như dữ kiện đã xác lập, không suy luận lại → claim sai thành tiền đề chịu lực cho cả tòa nhà.

**Hệ quả thiết kế #1: fact-check phải chặn SỚM**, ngay sau phase đầu tiên sinh claim thực thể — KHÔNG phải ở cuối. Chặn ở cuối thì chất độc đã định hình xong toàn bộ lập luận → phải vứt cả report làm lại.

**Hệ quả thiết kế #2: KHÔNG để model tự verify bằng trí nhớ.** Nếu fact-checker dùng chính model hỏi "Sông Hậu 1 có phải của POW không?" → nó tái xác nhận cái ảo giác (cùng prior sai). Fact-check chỉ có giá trị khi BỊ ÉP truy xuất nguồn ngoài và verify đối chiếu *bằng chứng truy xuất được*.

## NGUYÊN TẮC NỀN
- **Không factsheet tĩnh.** Danh mục & thông tin DN thay đổi → file gõ tay đúng hôm nay sai tháng sau. Dùng **cache có TTL, tái sinh từ nguồn gốc** (vnstock profile, BCTC/BCTN mới nhất, feed marketwire), không phải bảng khắc đá.
- **"Fact-check agent" và "grounding" hội tụ làm một**: một *verifier neo vào nguồn, chạy như cổng chặn (gate)*.

---

## C1. Claim Extraction — tách claim thực-thể kiểm-tra-được
**Việc:** Sau MỖI phase sinh nội dung định tính (đặc biệt Fundamentals — phase đầu), chạy một bước trích xuất các **claim thực thể** có thể verify:
- Tên nhà máy / dự án / công ty con + thuộc sở hữu của ai
- Mốc vận hành (COD), công suất, trạng thái dự án
- Sự kiện đã/đang xảy ra (M&A, phát hành, hợp đồng)

**KHÔNG trích** phán đoán phân tích ("biên LN sẽ hồi", "định giá hấp dẫn") — đó không phải fact, verify vô nghĩa và tốn token.

**Output:** list claim có cấu trúc, vd `{entity: "Sông Hậu 1", relation: "thuộc sở hữu", target: "POW", context_snippet: "..."}`.

**Acceptance:** mọi claim thực thể trong output phase được tách ra; claim phán đoán bị bỏ qua đúng.

## C2. Grounded Verifier — verify ép truy xuất nguồn
**Việc:** Với mỗi claim từ C1, verifier **bắt buộc** gọi nguồn ngoài (ưu tiên theo thứ tự: cache vnstock/marketwire → web_search), KHÔNG được trả lời từ trí nhớ model.
- Mỗi claim nhận verdict: `SUPPORTED` / `CONTRADICTED` / `UNVERIFIED`, **kèm nguồn (url/snippet)**.
- Cấm verdict không có nguồn đính kèm.

**Acceptance:** "Sông Hậu 1 thuộc POW" → `CONTRADICTED` kèm nguồn PVN là chủ đầu tư. Verdict nào thiếu nguồn → coi như fail chính verifier.

## C3. Gate Logic — chặn sớm, không cho lan nhiễm
**Việc:** Verifier chạy như **cổng giữa các phase**, không phải ở cuối:
- `CONTRADICTED` → **BLOCK**: dừng/regenerate phase đó với feedback ("claim X bị bác bởi nguồn Y, sửa lại"). KHÔNG cho claim truyền sang phase sau.
- `UNVERIFIED` → không block, nhưng **hạ cấp**: claim phải được đánh dấu "chưa kiểm chứng" và KHÔNG được dùng làm trụ cột luận điểm/định giá ở phase sau.
- `SUPPORTED` → cho đi tiếp, gắn tag nguồn (phục vụ C4).

**Vị trí trong chuỗi:** đặt gate ngay sau Phase I (Analyst/Fundamentals) trước khi vào Phase II (Research debate). Đây là điểm rẻ nhất để chặn.

**Acceptance:** claim `CONTRADICTED` không bao giờ xuất hiện ở các phase sau; report POW regen không còn Sông Hậu 1 trong DuPont/EV/Exec.

## C4. Citation-required cho claim định tính (nối B-class)
**Việc:** Mọi claim thực thể còn lại trong report cuối phải mang tag nguồn `[nguồn: ...]`. Claim `UNVERIFIED` hiển thị nhãn rõ ("giả thuyết — chưa kiểm chứng"), tách khỏi danh sách catalyst ✅ chắc chắn.

**Acceptance:** không còn claim thực thể "trần" (không nguồn, không nhãn) trong HTML cuối.

## C5. Ground-truth source layer (nút thắt thật)
**Việc:** Nút thắt KHÔNG nằm ở agent (wiring dễ) mà ở **nguồn ground truth**. Xây/khoá nguồn theo thứ tự:
1. **marketwire feed** (đã có) — tin cross-ref theo mã, nguồn chính cho sự kiện/catalyst.
2. **vnstock company profile** — mảng KD, công ty con, cache TTL.
3. **web_search** — fallback cho claim tài sản/sở hữu (đã chứng minh ăn tốt với tài sản điện VN).

**Acceptance:** verifier có ít nhất 1 nguồn truy xuất được cho mỗi loại claim; không phụ thuộc trí nhớ model.

## SƠ ĐỒ ĐẶT GATE
```
Phase I  Fundamentals/Analyst
   │  (sinh claim)
   ▼
[C1 Claim Extraction] → [C2 Grounded Verifier] ──► nguồn ngoài (marketwire / vnstock / web)
   │                                                   │
   ▼                          verdict + source ◄───────┘
[C3 GATE]
   ├─ CONTRADICTED → BLOCK + regenerate Phase I
   ├─ UNVERIFIED   → tag "chưa kiểm chứng", cấm làm trụ cột
   └─ SUPPORTED    → pass + tag nguồn
   │
   ▼
Phase II Research debate → … → Phase V PM   (chỉ nhận claim đã qua gate)
   │
   ▼
[C4] Render: mọi claim thực thể có [nguồn] hoặc nhãn "chưa kiểm chứng"
```

## THỨ TỰ ĐỀ XUẤT (Round 3)
1. **C5** (khoá nguồn) trước — không có nguồn thì verifier vô nghĩa.
2. **C1 + C2 + C3** (extraction → verify → gate) — lõi cơ chế.
3. **C4** (citation render) — sau cùng.

## TEST CASE (Round 3)
Regen `POW_2026-06-29`. Acceptance: (a) "Sông Hậu 1" bị gate bắt là `CONTRADICTED` và KHÔNG xuất hiện ở bất kỳ phase nào của report mới; (b) catalyst thật (Nhơn Trạch 3&4 LNG / Quỳnh Lập) nếu nêu thì có tag nguồn; (c) mọi claim thực thể còn lại đều có `[nguồn]` hoặc nhãn "chưa kiểm chứng".

## LƯU Ý CHI PHÍ
- Scope verifier CHỈ vào claim thực thể kiểm-tra-được (C1) để khỏi đốt token verify phán đoán phân tích.
- Verify sớm (sau Phase I) rẻ hơn nhiều so với để lỗi lan rồi vứt cả report.


---
---

# ROUND 4 🔲 — KHUNG PHÂN TÍCH & DEBATE (cho MỌI mã)

> **PHẠM VI QUAN TRỌNG:** Round 4 KHÔNG sửa riêng report nào. Đây là nâng cấp **prompt dùng chung** của các agent (Analyst/Fundamentals, Research debate, Portfolio Manager) và **khung tranh luận/định giá** — áp dụng cho TẤT CẢ mã được yêu cầu. POW chỉ là ca minh hoạ. **Cấm hardcode** đặc thù POW (giá khí, Q1, NT3&4) vào code/prompt — mọi quy tắc phải phát biểu tổng quát để chạy đúng với ngân hàng, BĐS, thép, bán lẻ, điện, v.v.
>
> Bối cảnh: bản `POW_2026-06-30_v1` đã trung thực và có kỷ luật (factual đúng, FCF nhất quán, có EV + bắc cầu + CFO/LNST insight), NHƯNG chưa *thuyết phục* được người cầm tiền. Khoảng cách "trung thực → thuyết phục" nằm ở chiều sâu phân tích. Round 4 mã hoá *checklist mà một analyst kỳ cựu luôn chạy* — không thể prompt cho LLM "giỏi", nhưng có thể ép nó chạy đủ checklist.

> Phân loại: D1, D2, D4, D5, D6 là **prompt/framework** (sửa system prompt agent dùng chung). D3 có cấu phần **code** (validator + valuation render).

---

## D1. Mổ xẻ chất lượng lợi nhuận — bắt buộc khi có quý/năm outlier
**Nguyên tắc tổng quát:** Khi một kỳ (quý/năm) là cực trị trong cửa sổ N (cao nhất/thấp nhất), agent KHÔNG được nêu nó như tin tốt/xấu trần — phải **giải phẫu**:
- Tách lợi nhuận lõi (hoạt động kinh doanh chính) khỏi: thu nhập tài chính, lãi/lỗ bất thường, hoàn nhập dự phòng, đánh giá lại tài sản, mùa vụ.
- **Nối với chuyển hoá tiền mặt:** so LNST kỳ đó với CFO; nếu CFO/LNST < 1.0x (hoặc xấu đi so với lịch sử), gắn cờ "lợi nhuận nặng tính dồn tích — chưa thành tiền" và KHÔNG cho dùng kỳ outlier làm trụ cột thesis nếu chưa giải thích được khoảng vênh.
- Quy tắc chống "annualize ngây thơ" (đã nêu B5): cấm lấy 1 quý × 4; nếu kỳ outlier lớn bất thường so với lợi nhuận cả năm gần nhất, phải nêu rõ tỷ lệ và đặt câu hỏi tính bền vững.

**Vì sao tổng quát:** ngân hàng → tách thu nhập lãi thuần vs thu nhập bất thường/hoàn nhập; BĐS → tách bàn giao vs đánh giá lại; thép → tách spread vs hoàn nhập tồn kho. Cùng một logic.

**Acceptance:** mọi report có kỳ outlier đều có đoạn giải phẫu nguồn gốc + đối chiếu CFO/LNST; không còn "quý kỷ lục" nêu trần không giải thích.

## D2. Sensitivity của biến động chính — biến cảnh báo định tính thành số
**Nguyên tắc tổng quát:** Mỗi mã có 1–2 **biến xoay chuyển thesis** (swing variable). Agent phải (a) tự nhận diện biến đó, (b) dựng **bảng sensitivity** fair value / margin theo biến đó.
- Cách nhận diện: biến mà Bear và Bull bất đồng nhất, hoặc biến có biên độ lịch sử lớn nhất tác động lên lợi nhuận.
- Ví dụ ánh xạ (KHÔNG hardcode, chỉ để agent hiểu pattern): điện khí → giá khí/dầu; ngân hàng → NIM & chi phí tín dụng; BĐS → hấp thụ & lãi suất; thép → spread giá; bán lẻ → SSSG & biên gộp.
- Output: bảng "biến ±10/20/30% → margin → fair value → upside/downside".

**Vì sao cần:** thesis kiểu "biên đã hồi" vô nghĩa nếu không lượng hoá được điều gì xảy ra khi biến chính đảo chiều. Đây là cách "căng" lập luận Bear thành con số thay vì phẩy tay.

**Acceptance:** mọi report nêu rõ swing variable + có ít nhất 1 bảng sensitivity gắn với fair value.

## D3. Định giá trung thực — tách "fair value hôm nay" khỏi "upside có điều kiện"
**Nguyên tắc tổng quát:** Cấm gộp phương pháp định giá *dựa earnings hiện tại/TTM* với phương pháp *dựa dự báo phục hồi* thành một dải mơ hồ che đi bất đồng.
- Phải trình bày tách 2 lớp: **(1) Giá trị trên số hiện tại** (multiple áp lên TTM/hiện tại — "nếu không có gì thay đổi"); **(2) Upside có điều kiện** (multiple áp lên kịch bản forward — ghi RÕ điều kiện: ROE/LNST forward giả định bao nhiêu, xác suất).
- Nếu (1) cho upside ~0% và toàn bộ upside nằm ở (2): report PHẢI nói thẳng câu này lên đầu — "cổ phiếu đang ~fair value trên số hôm nay; đây là kèo phục hồi/optionality", không được bán nó như "cổ phiếu rẻ".
- **(Code) Back-propagation + validator:** khi một agent tuyến sau hiệu chỉnh số của agent tuyến trước (vd PM sửa TTM LNST/margin mà Bull thổi phồng), số đã sửa phải **đồng bộ ngược** lên mọi mục (exec summary, valuation, header). Validator A7 phải bắt trường hợp số headline ≠ số đã hiệu chỉnh ở mục khác → BLOCK.
- **(Code) Sửa logic số trong justification multiple** (nối B5/B6): vd "target 12.0x cao hơn hiện tại 13.19x" là sai số học → validator check hướng so sánh của target vs current.

**Acceptance:** valuation tách 2 lớp rõ; nếu upside chủ yếu có điều kiện thì nêu ngay đầu; không còn số headline mâu thuẫn với số đã hiệu chỉnh nội bộ; không còn lỗi hướng so sánh multiple.

## D4. "Why-now" — phân biệt rẻ-có-catalyst vs rẻ-tiền-chết
**Nguyên tắc tổng quát:** Mọi rating Overweight/Buy phải trả lời "tại sao bây giờ":
- Nêu **catalyst gần** cụ thể (KQKD kỳ tới, sự kiện DN, tái định giá hợp đồng, mốc dự án, chính sách) HOẶC
- Thừa nhận thẳng "không có catalyst gần — đây là vị thế kiên nhẫn/optionality" và điều chỉnh sizing/khung thời gian cho phù hợp.
- **Kiểm tra nhất quán:** nếu lộ trình giải ngân tự nó "chờ KQKD kỳ tới / chờ tín hiệu X" thì agent phải thừa nhận điều đó hàm ý KHÔNG gấp — và không được đồng thời hô "vào ngay". Sizing đợt 1 phải phản ánh mức độ cấp thiết của catalyst.

**Acceptance:** mọi Overweight/Buy có mục "why-now" rõ; không còn mâu thuẫn giữa "mua ngay" và lộ trình "chờ xác nhận".

## D5. Hiện dữ liệu, đừng khẳng định xu hướng
**Nguyên tắc tổng quát:** Cấm khẳng định xu hướng suông ("N quý tăng liên tục", "phục hồi đều đặn") mà không hiện chuỗi số.
- Claim về tính đơn điệu/liên tục phải kèm chuỗi dữ liệu thực để người đọc tự đánh giá độ mượt.
- Phân biệt rõ "hồi phục từ đáy sụp" vs "tăng trưởng trơn tru" — đi lên từ một đáy khủng hoảng KHÁC với uptrend bền vững; agent không được đóng gói cái trước thành cái sau.

**Acceptance:** mọi claim xu hướng đều có chuỗi số đi kèm; ngôn ngữ phân biệt recovery-off-trough vs structural-uptrend.

## D6. Chuẩn burden-of-proof đối xứng trong debate (củng cố B3)
**Nguyên tắc tổng quát:** Trong "cân đo" Bull/Bear, KHÔNG được mặc định bên nào thắng chỉ vì "có nhiều data point đã xác nhận hơn". Phải:
- Cân theo **trọng số tác động**, không đếm số lượng data point (1 rủi ro FCF cấu trúc có thể nặng hơn 6 điểm tích cực).
- Khi cả hai bên cùng dùng DỰ BÁO cho luận điểm cốt lõi, nêu rõ điều đó và KHÔNG nghiêng về bên nào chỉ vì bên kia "cũng dự báo".
- Rủi ro Bear "đã xác nhận nhưng chưa giải quyết" (vd FCF âm) phải được phản ánh vào **sizing và xác suất kịch bản Bear**, không chỉ nhắc rồi bỏ qua.

**Acceptance:** mục cân đo nêu rõ trọng số tác động (không phải đếm điểm); rủi ro đã-xác-nhận-chưa-giải-quyết được map vào xác suất/sizing.

## THỨ TỰ ĐỀ XUẤT (Round 4)
1. **D3** (gồm cấu phần code: back-prop + validator) — vì credibility số là nền.
2. **D1 + D2** (quality-of-earnings + sensitivity) — lõi chiều sâu phân tích.
3. **D4 + D5 + D6** (why-now, hiện dữ liệu, burden-of-proof) — tinh chỉnh debate/kết luận.

## TEST CASE (Round 4) — kiểm tra trên ≥2 mã KHÁC NGÀNH
Quan trọng: vì đây là khung chung, test trên ít nhất 2 mã thuộc 2 ngành khác nhau (vd 1 điện + 1 ngân hàng/BĐS), KHÔNG chỉ POW. Acceptance:
- Mỗi report tự nhận diện đúng swing variable của ngành đó + có bảng sensitivity.
- Kỳ outlier (nếu có) được giải phẫu + đối chiếu CFO/LNST.
- Valuation tách 2 lớp (hiện tại vs có điều kiện).
- Có mục why-now; không mâu thuẫn mua-ngay vs chờ.
- Không hardcode đặc thù mã/ngành nào trong code.

---
---

# ROUND 5 🔲 — KIẾN TRÚC TRÌNH BÀY RATING (cho MỌI report)

> Phát hiện mới từ review `POW_2026-06-30_deepseek-pro_v2.html`: report có **7 lần xuất hiện rating khác nhau** (Header HOLD → Tóm Tắt Đầu Tư BUY → Market/News STRONG BUY → Trader HOLD → Fundamentals BUY → Research BUY → PM HOLD cuối). Đây KHÔNG phải lỗi nội dung phân tích — đây là lỗi **kiến trúc trình bày**: mỗi agent tự gắn label "khuyến nghị" độc lập, không phân biệt "ý kiến tạm trong debate" với "quyết định cuối". Người đọc lướt dễ hiểu sai hướng vì 5/7 label nghiêng BUY trong khi quyết định thật là HOLD.
>
> Việc 2 (E-series dưới) độc lập: thuật ngữ nội bộ (D1-D6, L1/L2, B1...) dùng để đặc tả yêu cầu sửa pipeline giữa người dùng và Claude Code — KHÔNG được lọt vào nội dung report hiển thị cho người dùng cuối.

## E1. Phân biệt rõ "đề xuất tạm" vs "quyết định cuối"
**Việc:** Mọi rating do Analyst/Trader/Research agent đưa ra ở phase trung gian phải gắn nhãn rõ là **đề xuất/ý kiến tạm**, KHÔNG hiển thị trần như một kết luận độc lập.
- Đổi cách hiển thị: "Đề xuất từ [tên agent/phase]: BUY" thay vì để chữ "BUY" đứng một mình to đậm.
- CHỈ rating của Portfolio Manager (Phase V, cuối) mới được gọi là **"Final Signal" / "Khuyến nghị cuối cùng"**.

**Acceptance:** không còn label rating nào ở phase trung gian hiển thị như kết luận độc lập (không kèm tên agent/phase + chữ "đề xuất"/"tạm").

## E2. Header "Final Signal" phải khớp tuyệt đối với PM (validator)
**Việc:** Thêm assert vào validator (nối A7): `header.final_signal == PM.rating`. Đây là dòng đầu tiên người đọc thấy — nó PHẢI phản ánh đúng quyết định thật, không phải rating của agent nào khác.

**Acceptance:** mọi report regen, header luôn khớp PM. Lệch → BLOCK (cùng cơ chế A7).

## E3. Bảng tổng hợp rating của các agent + quyết định PM (đặt ngay đầu report)
**Việc:** Thêm một bảng nhỏ ngay dưới header "Final Signal", TRƯỚC mọi phase chi tiết. Bảng gồm các cột:

| Agent / Phase | Rating đề xuất | Tóm tắt lý do (≤15 từ) |
|---|---|---|
| Market Analyst | ... | ... |
| News/Sentiment | ... | ... |
| Fundamentals | ... | ... |
| Research Team (Bull/Bear) | ... | ... |
| Trader | ... | ... |
| **PM — Quyết định cuối** | **...** | **...** |

Yêu cầu kỹ thuật:
- **Build từ structured output thật của từng agent bằng code**, KHÔNG để PM (hay agent nào) tự tường thuật lại ý kiến của agent khác bằng văn xuôi tự do — tránh lệch/diễn giải sai (rủi ro kiểu context-poisoning ở Round 3). Mỗi agent khi sinh output phải trả về một field rating có cấu trúc (enum: BUY/HOLD/SELL/STRONG BUY/...) riêng biệt khỏi phần văn xuôi phân tích, để code ghép bảng trực tiếp từ field đó.
- **Cột "lý do override" bắt buộc khi PM đi ngược đa số agent.** Nếu PM rating khác với rating của ≥3/5 agent còn lại → tự động thêm dòng/badge cảnh báo "PM override đa số" ngay trong bảng, kèm 1 câu lý do ngắn (không phải để độc giả phải tự lục Investment Thesis dài bên dưới mới hiểu).
- Bảng này thay thế hoàn toàn vai trò của "khối tóm tắt hành trình rating" — không cần làm thêm phần riêng.

**Acceptance:** bảng hiển thị đúng ở vị trí đầu report; dữ liệu khớp với rating thật mà mỗi agent đã field-output (không lệch so với nội dung phase chi tiết bên dưới); có cảnh báo rõ khi PM override đa số.

## E4. Bỏ thuật ngữ đặc tả nội bộ (D/B/A/C/L-series) ra khỏi output hiển thị
**Việc:** Rà toàn bộ prompt agent — bất kỳ chỗ nào agent được hướng dẫn áp dụng D1-D6/B1-B7/A-series/C-series và lỡ in thẳng mã hiệu đó vào output (vd "Kiểm tra Định giá 2 Lớp (D3)", "L1 upside", "L2 upside", "D4 WHY NOW", "D6 Impact-Weighted Risk") — sửa hướng dẫn để agent thực thi đúng yêu cầu nhưng diễn đạt bằng ngôn ngữ tài chính thông thường.

Gợi ý đổi tên hiển thị:
- "Định giá 2 Lớp (D3)" → "Định giá: Giá trị hiện tại vs. Tiềm năng có điều kiện"
- "L1 upside" → "Upside trên số liệu hiện tại"
- "L2 upside" → "Upside theo kịch bản phục hồi"
- "WHY NOW (D4)" → "Tại sao là lúc này?"
- "Impact-Weighted Risk (D6)" → "Đánh giá rủi ro theo mức độ tác động"

Mã D/B/A/C/L-series CHỈ dùng trong `tradingagents_fixes.md` và code comment/commit message — không bao giờ xuất hiện trong HTML output.

**Acceptance:** search toàn bộ HTML output, không còn pattern `\b[A-D][0-9]\b` hoặc `\bL[12]\b` xuất hiện như thuật ngữ hiển thị cho người dùng.

## THỨ TỰ ĐỀ XUẤT (Round 5)
1. **E1 + E2** — phân loại label tạm/cuối + validator header trước.
2. **E3** — bảng tổng hợp (phụ thuộc E1: cần field rating có cấu trúc từ mỗi agent).
3. **E4** — dọn thuật ngữ, làm sau cùng, độc lập với E1-E3.

## TEST CASE (Round 5)
Regen `POW_2026-06-30`. Acceptance: (a) chỉ còn 1 label "Final Signal/Khuyến nghị cuối cùng" duy nhất, khớp PM; (b) có bảng tổng hợp đầu report đúng vị trí, đúng dữ liệu field-based; (c) có cảnh báo "PM override đa số" nếu áp dụng cho case POW (PM Hold vs 4 agent khác nghiêng Buy); (d) không còn D/B/A/C/L-series trong HTML.

---
---

# ROUND 6 🔲 — VARIANCE & ĐỘ TIN CẬY CỦA "LỊCH SỬ QUYẾT ĐỊNH"

> **Phát hiện:** 3 lần chạy report POW cùng ngày 30/06/2026 ra 3 rating khác nhau (Hold/Buy/Underweight ở các điểm PM-decision khác nhau trong log). PM ở report mới nhất trích dẫn "bài học từ quyết định Underweight 26/06 và Overweight 29/06" như FACT LỊCH SỬ ĐÃ XÁC LẬP để tự cho mình quyền hạ thêm rating — nhưng nếu các ngày đó cũng có variance giữa nhiều run như 30/06, thì "bài học" đó chỉ là MỘT SAMPLE trong một phân phối, không phải sự thật đã chốt. PM đang học từ nhiễu, không phải từ kinh nghiệm thật — giống overfitting trên N=1.
>
> **BẮT BUỘC chạy Bước Điều Tra (xem prompt riêng) trước khi áp bất kỳ mục nào dưới đây.** Hai nhánh fix tuỳ kết quả điều tra.

## NHÁNH 1 — Nếu variance đến từ INPUT khác nhau giữa các run (data freshness)

### F1a. Snapshot input theo run, không theo ngày
**Vấn đề:** Nếu News/Market data được fetch lại mỗi lần chạy (real-time), "report ngày 30/06" thực ra là nhiều bài toán khác nhau bị gắn nhãn chung một ngày.

**Fix:** Lưu kèm mỗi report một `input_snapshot_id` (hash của input thực tế dùng), không chỉ ngày. "Lịch sử quyết định" khi tra cứu phải so khớp theo input tương tự, không chỉ theo ngày — hoặc tối thiểu phải hiển thị rõ "report này dùng input fetch tại thời điểm X trong ngày", để người đọc biết đây có thể không phải input giống các report khác cùng ngày.

**Acceptance:** mỗi report có field `input_snapshot_id`; nếu 2 report cùng ngày có input khác nhau, hệ thống không cho phép PM coi chúng là "cùng một tình huống" khi trích dẫn lịch sử.

## NHÁNH 2 — Nếu variance đến từ MODEL không deterministic (dù input giống nhau)

### F1b. Giảm variance ở Research/PM bằng temperature thấp hơn / seed cố định
**Fix:** Với Phase II (Research debate) và Phase V (PM) — nơi quyết định cuối được chốt — hạ `temperature` xuống mức thấp (vd 0.1-0.3) thay vì mặc định cao, và set `seed` cố định nếu API hỗ trợ. Mục tiêu KHÔNG phải triệt tiêu hoàn toàn variance (debate cần một chút đa dạng góc nhìn), mà giảm variance ở bước RA QUYẾT ĐỊNH CUỐI để cùng input không ra rating khác nhau quá xa.

**Acceptance:** chạy lại 5 lần với input cố định, rating cuối lệch nhau không quá 1 bậc (vd Hold↔Buy chấp nhận được do biên EV mỏng, nhưng Buy↔Underweight trong cùng input là dấu hiệu cần giảm thêm temperature).

## KHÔNG PHỤ THUỘC NHÁNH NÀO — áp dụng cả hai trường hợp

### F2. "Lịch sử quyết định" phải lưu dưới dạng PHÂN PHỐI, không phải fact đơn lẻ
**Vấn đề:** PM hiện trích dẫn "quyết định ngày X" như một điểm dữ liệu chắc chắn để tự điều chỉnh xác suất/rating ngày hôm sau.

**Fix:** Khi lưu lịch sử quyết định cho một mã/ngày, nếu có nhiều run, lưu dưới dạng tổng hợp: ví dụ "ngày 29/06: 3 run → {Overweight: 1, Hold: 1, Buy: 1} — độ phân tán CAO, độ tin cậy THẤP" thay vì chọn 1 run làm đại diện. Nếu chỉ có 1 run (chưa đo variance) → gắn nhãn "chưa kiểm tra độ ổn định — coi là tham khảo yếu".

**Fix prompt cho PM:** cấm PM viện dẫn "quyết định ngày X" như fact chắc chắn để làm cơ sở hạ/nâng rating thêm một bậc, NẾU độ phân tán của ngày đó là CAO hoặc chưa được đo. Chỉ được dùng "bài học lịch sử" làm yếu tố điều chỉnh khi lịch sử đó có độ tin cậy đủ (vd ≥3 run, độ phân tán thấp) — và phải nêu rõ độ tin cậy này trong lý do.

**Acceptance:** PM không còn câu kiểu "bài học từ quyết định Underweight 26/06" mà không kèm chú thích độ tin cậy của chính ngày đó.

### F3. Tách "alpha thực hiện" (fact khách quan) khỏi "rating đã ra" (sample LLM)
**Vấn đề:** Cái đáng học từ lịch sử không phải là rating PM từng chốt (phụ thuộc run), mà là GIÁ ĐÃ DI CHUYỂN THẾ NÀO sau đó — đây là fact khách quan từ thị trường, không phụ thuộc việc chạy lại bao nhiêu lần.

**Fix:** Khi PM tham chiếu "performance" của quyết định cũ, phải dùng dữ liệu giá thực tế (đã có sẵn, không phụ thuộc LLM run) làm input chính — vd "giá POW đã di chuyển +X% trong N ngày sau mốc đó" — KHÔNG dùng "rating tôi từng nói" làm bằng chứng. Rating cũ chỉ nên xuất hiện trong ngoặc để đối chiếu hướng đi (đúng/sai), không phải làm cơ sở định lượng cho điều chỉnh xác suất.

**Acceptance:** mọi tham chiếu "bài học lịch sử" trong PM output đều neo vào di chuyển giá thực tế, không chỉ vào rating LLM đã từng đưa ra.

## THỨ TỰ ĐỀ XUẤT (Round 6)
1. **Bước Điều Tra** (xem prompt riêng, không sửa code) — xác định Nhánh 1 hay Nhánh 2 hay cả hai.
2. **F1a hoặc F1b** tuỳ kết quả điều tra.
3. **F2 + F3** — áp dụng bất kể nhánh nào, vì đây là vấn đề cách dùng lịch sử, độc lập với nguyên nhân variance.

## TEST CASE (Round 6)
Chạy lại POW 5 lần với input cố định (snapshot từ 1 run thật). Acceptance: (a) rating không dao động quá 1 bậc giữa 5 run; (b) nếu PM tham chiếu lịch sử ngày trước, phải kèm độ tin cậy (số run đã đo, độ phân tán); (c) tham chiếu "alpha" dùng giá thị trường thật, không dùng rating LLM cũ làm bằng chứng định lượng.

---
---

# ROUND 6B 🔲 — KẾT QUẢ ĐIỀU TRA & SPEC CHÍNH XÁC (thay thế giả thuyết nhánh ở Round 6)

> **Kết quả điều tra thật (Claude Code đã chạy):** 5/5 lần rerun Phase II+V với Phase I CỐ ĐỊNH (từ run 30/06 v3) → PM ra Hold cả 5 lần. Phase I CHẠY LẠI (main.py riêng từng lần) → market/news/fundamentals/sentiment report MD5 đều khác nhau dù raw market data cùng ngày, vì **Phase I (Analyst agents) không có temperature/seed control nào (mặc định ~1.0)**.
>
> **Kết luận đã xác nhận:** nguồn variance là **Phase I LLM prose**, KHÔNG phải raw data fetch (data khác Nhánh 1 gốc đã đoán), và KHÔNG phải Phase II+V non-determinism (Nhánh 2 gốc đã đoán). Đây là layer thứ ba, hẹp hơn cả hai giả thuyết: **Phase I thiếu determinism control, framing của nó lan xuống và lái cả debate.**
>
> **Phát hiện phụ quan trọng — "convergence giả":** trong 5 run Step 3, PM rating đều = Hold, NHƯNG **EV dao động (0.25 / 5.70% / 0.25 / 0.25 / N/A)**. Vì EV là phép tính có công thức cố định từ bộ xác suất Bull/Base/Bear, EV khác nhau nghĩa là PM vẫn tự sinh lại bộ xác suất khác nhau mỗi lần (xem câu hỏi trước về cơ sở thực tiễn của 30/45/25) — chỉ là kết quả tình cờ rơi vào cùng dải rating "Hold". Nếu chỉ nhìn cột rating, dễ tưởng Phase II+V hoàn toàn ổn định — thực ra KHÔNG, nó chỉ ổn định ở tầng quyết định cuối nhờ biên rating đủ rộng, KHÔNG ổn định ở tầng xác suất/EV bên dưới.

## G0. Phân biệt "run quyết định thật" vs "run debug/test" trong lịch sử — ĐIỀU TRA TRƯỚC G1-G3
> **Phát hiện mới (từ câu hỏi của Khoa):** Các lần chạy POW trong quá trình debug Round 6/6B (test G1/G2/G3, rerun Phase II+V nhiều lần) là run THỬ NGHIỆM, không phải quyết định đầu tư thật. Nếu hệ thống lưu "lịch sử quyết định" theo ngày/mã mà không phân biệt được nguồn gốc run, các run debug này có thể bị PM sau này trích dẫn như lịch sử thật — làm hỏng cả ý định của F2 (Round 6: lịch sử phải đáng tin để tham chiếu).

**Việc điều tra (làm trước khi chạm G1-G3):**
- Tìm trong codebase: lịch sử quyết định (dùng cho "bài học từ ngày X") được lưu ở đâu — file/DB nào, có field nào đánh dấu loại run (production / manual-test / debug) không?
- Xác nhận: các lần Khoa và Claude Code đã chạy hôm nay để test G1/G2/G3 — chúng có bị ghi vào cùng store mà PM sau này sẽ đọc làm "lịch sử" không?
- Nếu CÓ bị trộn lẫn → đây là việc cần làm ngay, có thể quan trọng hơn G1-G3:

**Fix (nếu xác nhận bị trộn lẫn):**
- Thêm field `run_type` (enum: `production` / `manual_test` / `debug`) khi lưu mỗi run.
- PM khi tham chiếu "lịch sử quyết định" CHỈ được đọc run có `run_type = production`. Run test/debug không được tính vào lịch sử dùng để điều chỉnh rating tương lai.
- **Dọn dữ liệu đã nhiễm (nếu có):** rà lại lịch sử đã lưu của POW — gắn lại `run_type` cho các run đã chạy trong quá trình debug Round 6/6B hôm nay (29/06, 30/06 và mọi rerun) thành `debug`, để chúng không bị tính vào "bài học lịch sử" của các lần PM chạy POW sau này.

**Acceptance:** lịch sử quyết định có field phân loại run; PM chỉ tham chiếu run `production`; dữ liệu POW đã nhiễm từ debug hôm nay được dọn lại đúng nhãn.

## G1. Temperature/seed control cho Phase I (Analyst agents) — ưu tiên cao nhất
**Việc:** Set `temperature` thấp (đề xuất 0.2–0.4) cho Market/News/Fundamentals/Sentiment analyst agents. Set `seed` cố định nếu API hỗ trợ (kiểm tra: DeepSeek/GLM/Claude API nào hỗ trợ seed parameter — không phải tất cả đều có).
- KHÔNG áp dụng cùng mức cho Phase II (Research debate) — debate cần một biên độ diễn giải để Bull/Bear có góc nhìn khác nhau một cách có ý nghĩa (không phải nhiễu).
- Phase V (PM) nên ở mức thấp tương tự Phase I vì đây là bước chốt, không phải bước cần đa dạng góc nhìn.

**Acceptance:** chạy lại main.py đầy đủ 5 lần cho cùng mã/ngày, MD5 của market/news/fundamentals/sentiment report phải giống nhau hoàn toàn (hoặc gần giống nếu vẫn cho phép biên độ nhỏ — định nghĩa rõ ngưỡng "giống" nếu không set được seed tuyệt đối, vd similarity > 95% theo số liệu/claim chính, không cần giống từng chữ).

## G2. Sensitivity nội tại TRONG 1 LẦN CHẠY — không cần N runs (đã đổi thiết kế do ràng buộc ngân sách)
> **Lý do đổi:** bản gốc của G2 (đo EV qua nhiều lần chạy thật) cần N lần gọi API mỗi mã — không khả thi với ngân sách hiện tại khi chạy ~70 mã. Thay bằng: PM tự đo độ nhạy của CHÍNH NÓ trong một lần chạy duy nhất, bằng phép tính cộng trừ (không gọi LLM thêm lần nào).

**Việc:** Sau khi PM tính EV từ bộ xác suất Bull/Base/Bear đã chốt, bắt buộc tính thêm 2 kịch bản biên (arithmetic, không phải LLM call mới):
- `EV_low`: lệch xác suất Bear +5 điểm % (lấy từ Base), giữ Bull nguyên → tính lại EV.
- `EV_high`: lệch xác suất Bull +5 điểm % (lấy từ Base), giữ Bear nguyên → tính lại EV.
- Báo cáo dải: "EV trong khoảng [EV_low, EV_high] nếu xác suất lệch ±5 điểm %."

**Gắn nhãn độ tin cậy (Conviction) dựa trên dải này — hiển thị NGAY CẠNH Final Signal:**
- Nếu dải `[EV_low, EV_high]` cắt qua ranh giới giữa 2 rating khác nhau (vd dải chứa cả vùng Hold và vùng Underweight) → `Conviction: THẤP — kết luận nhạy với giả định xác suất, lệch nhỏ có thể đổi rating`.
- Nếu dải nằm hoàn toàn trong vùng 1 rating, cách ranh giới ≥ 1 ngưỡng (đề xuất: cách biên rating ≥3 điểm % EV) → `Conviction: CAO`.
- Mức giữa → `Conviction: TRUNG BÌNH`.

**Acceptance:** mọi report có dòng "EV sensitivity: [X%, Y%]" + nhãn Conviction ngay cạnh Final Signal; không cần chạy lại pipeline để biết — toàn bộ tính trong 1 lần PM output.

**Lưu ý:** đây không thay thế hoàn toàn việc đo variance thật qua nhiều run (vẫn nên làm sampling nhỏ — vd 5-10 mã đại diện trong 70 mã, chạy 3 lần — một lần, để kiểm định xem Conviction tự đánh giá có khớp với variance thật quan sát được hay không). Nhưng KHÔNG cần làm N-run cho MỌI mã, MỌI lần chạy.

## G3. Điều tra field Trader N/A ở Run 5 — không bỏ qua âm thầm
**Việc:** Run 5 trong Step 3, Trader action không parse được (regex không match) nhưng PM vẫn ra Hold — cần xác nhận PM có thực sự dùng input Trader hay tự suy luận khi thiếu dữ liệu upstream.

**Fix:** Thêm log/assert: nếu một field bắt buộc từ Phase trước bị thiếu/null khi vào Phase V, PM phải fail rõ ràng hoặc log cảnh báo "thiếu input Trader, quyết định dựa trên dữ liệu không đầy đủ" — KHÔNG được âm thầm tiếp tục như không có gì xảy ra. Đồng thời sửa root cause của lỗi regex parse Trader action (tìm trong code Trader agent/parser).

**Acceptance:** không còn N/A âm thầm lọt qua Phase V; nếu thiếu input, output phải tự gắn cờ rõ.

## CẬP NHẬT SO VỚI ROUND 6 GỐC
- **F1a (snapshot theo input_snapshot_id) — KHÔNG CẦN** vì raw data không phải nguồn variance; bỏ mục này.
- **F1b (giảm temperature) — ÁP DỤNG NHƯNG ĐỔI LAYER:** áp cho Phase I + Phase V (không phải "Phase II+V" như viết ban đầu) — vì Phase II+V đã tự chứng minh ổn định ở tầng rating khi nhận input cố định; G1 thay thế F1b với layer chính xác hơn.
- **F2 + F3 (lịch sử dạng phân phối, neo vào giá thật) — GIỮ NGUYÊN, vẫn cần làm**, nay càng quan trọng hơn vì G2 vừa cho thấy ngay cả "ổn định" có thể là giả.

## THỨ TỰ ĐỀ XUẤT (Round 6B)
1. **G0** (điều tra phân loại run + dọn dữ liệu POW đã nhiễm từ debug) — làm TRƯỚC mọi mục khác.
2. **G3** (fix lỗi parse Trader) — dọn lỗi âm thầm trước khi đo lại.
3. **G1** (temperature/seed Phase I + V) — chặn nguồn variance gốc.
4. **G2** (sensitivity trong 1 lần chạy + Conviction label).
5. Test trên **MÃ KHÁC, KHÔNG PHẢI POW** (POW đã có quá nhiều run debug hôm nay — dùng mã sạch để không tốn thêm chi phí và để không làm nhiễu thêm lịch sử POW).
6. Tiếp tục **F2 + F3** từ Round 6 gốc (không đổi) — áp dụng cùng nguyên tắc phân loại run từ G0.

## TEST CASE (Round 6B)
Chạy `main.py` đầy đủ cho POW 1 lần với G1 đã áp (không cần N lần — phù hợp ngân sách). Acceptance: (a) report có dòng "EV sensitivity: [X%, Y%]" + nhãn Conviction; (b) không còn N/A âm thầm ở field nào; (c) **(một lần, để kiểm định, không lặp lại cho mã sau)** chạy lại pipeline đầy đủ 3 lần cho 1-2 mã mẫu để xác nhận nhãn Conviction THẤP có thực sự tương ứng với rating dao động qua các run thật — nếu khớp, tin Conviction cho các mã sau mà không cần chạy N lần mỗi mã.

---
---

# ROUND 7 🔲 — KẾT QUẢ ĐIỀU TRA: FABRICATED EXTERNAL CITATION (đã thay giả thuyết anchoring)

> **KẾT LUẬN ĐIỀU TRA (đã xác nhận qua log, không phải giả thuyết):** Khả năng anchoring vào sell-side research bị LOẠI HOÀN TOÀN. Bằng chứng: (1) Fundamentals agent ở report PVD v3 chỉ gọi tool raw financial data — KHÔNG gọi `get_news`/`get_marketwire` nào; (2) "40.3" và "Phuoc Duong" xuất hiện lần đầu trong OUTPUT của Fundamentals (dòng 807), KHÔNG có trong bất kỳ Tool Message nào trước đó; (3) News agent chạy SAU Fundamentals, và response marketwire cho PVD 7 ngày qua RỖNG — không có bài báo nào được nạp.
>
> **Sự thật:** Fundamentals agent tự tính EPS 2026E ≈ 2,520 VND × P/E 16x = 40.3 nghìn từ raw data (phép tính có thể đúng quy trình) — SAU ĐÓ **bịa ra "Analyst Phuoc Duong (09/04/2026) từ Vietcap"** như một nguồn xác nhận không tồn tại trong context, để tăng độ thuyết phục cho con số tự tính. Classic LLM confabulation: fabricate external consensus.
>
> **Vì sao nghiêm trọng hơn Round 3 (Sông Hậu 1):** Sông Hậu 1 sai rõ ràng, kiểm tra được (PVN sở hữu, không phải POW). Citation "Phuoc Duong/Vietcap/09/04/2026" nguy hiểm hơn vì: (a) nó có cấu trúc giống thật — tên người, tổ chức, ngày cụ thể — đánh lừa cả người đọc kỹ; (b) con số 40.3 TÌNH CỜ trùng với Vietcap thật (đã xác nhận qua ảnh chụp), khiến claim *cảm giác* hợp lý và không bị nghi ngờ — nhưng sự trùng khớp là ngẫu nhiên, còn cái TÊN nguồn là bịa hoàn toàn. Đây là dạng hallucination khó bắt nhất: đúng về kết quả ngẫu nhiên, sai hoàn toàn về cách biện minh. Nếu không có ảnh chụp Vietcap để đối chiếu, KHÔNG CÁCH NÀO phát hiện được.
>
> **H1-H4 trong thiết kế gốc của Round 7 (sequencing để chống anchoring) KHÔNG còn cần thiết** — vì không có gì để anchor từ (context rỗng). Thay bằng I-series dưới đây: chặn việc agent tự bịa citation, bất kể context có gì.

## I1. Cấm agent tự trích dẫn nguồn ngoài KHÔNG có trong context của chính nó (chặn ở nguồn)
**Việc:** Sửa system prompt của MỌI agent có thể đưa ra claim định giá/nhận định (Fundamentals, Research, PM): cấm tuyệt đối việc đề cập tên analyst, công ty chứng khoán, tổ chức, hoặc nguồn bên ngoài cụ thể NẾU thông tin đó không xuất hiện trong context/tool output đã cung cấp cho chính lượt gọi đó.
- Chỉ thị cụ thể trong prompt: "Bạn KHÔNG được đề cập tên analyst, CTCK, hay nguồn bên ngoài nào trừ khi thông tin đó có trong dữ liệu/tool output đã được cung cấp trong context này. Nếu bạn không có nguồn tin tức/research bên ngoài trong context, không được tự suy ra hoặc tạo ra một nguồn để minh họa/xác nhận kết quả của bạn."
- Đây là vấn đề tổng quát, không riêng "trùng target price" — cùng cơ chế confabulation có thể xảy ra ở dạng khác (vd tự trích "theo báo cáo SSI...", "theo chuyên gia X..." mà không có gì trong context).

**Acceptance:** mọi tên riêng (người/tổ chức) xuất hiện trong output của Fundamentals/Research/PM phải truy được nguồn gốc từ tool output/context thực tế đã cấp cho agent đó trong đúng lượt gọi.

## I2. Validator/grounding check riêng cho citation bên thứ ba (khác C-series — không có gì để verify đối chiếu, phải verify SỰ TỒN TẠI trong context)
**Việc:** Đây khác C-series (Round 3, verify claim có ĐÚNG không bằng nguồn ngoài) — ở đây cần verify claim có **xuất hiện trong context của chính run đó** không, trước khi verify đúng/sai với thế giới thật.
- Thêm bước kiểm tra tự động (regex/NER đơn giản, không cần LLM call): quét output của mỗi agent tìm pattern tên riêng + tổ chức tài chính (vd "Vietcap", "SSI", "VNDirect", "VCBS", tên người + "analyst/chuyên gia") → đối chiếu xem cụm từ đó (hoặc gần giống) có xuất hiện trong TOÀN BỘ context/tool output đã cấp cho agent đó trong lượt gọi này không.
- Nếu phát hiện tên riêng/tổ chức trong output mà KHÔNG có trong context → tự động gắn cờ `UNGROUNDED_CITATION`, regenerate (block tương tự A7) hoặc tối thiểu strip claim đó khỏi report cuối + log lại để review.

**Acceptance:** report PVD rerun không còn xuất hiện "Phuoc Duong"/"Vietcap" trong output của Fundamentals nếu News agent chưa chạy/chưa có data — vì claim đó không grounded trong context của chính lượt gọi đó.

## I3. Test xác nhận bổ sung — case CÓ broker note thật (đề xuất của Claude Code, nên làm)
**Việc:** Test khác I1/I2 ở câu hỏi: khi marketwire/news THỰC SỰ có broker note với target price khác hẳn fundamental calculation của agent, agent có anchor theo broker note đó không (đây mới là câu hỏi anchoring thật, khác case PVD).
- Chọn 1 mã có MarketWire feed chứa broker note thật trong 7 ngày gần nhất, với target price khác đáng kể (>15%) so với khả năng fundamental calculation tự nhiên của agent.
- Chạy pipeline, so sánh target L1/L2 tự tính của agent với target trong broker note đó.
- Nếu agent "trôi" gần broker note dù phương pháp tính khác hẳn → đây là anchoring thật (khác I1, vì context CÓ data để anchor từ) → áp dụng lại nguyên tắc sequencing (Lượt 1 tính độc lập / Lượt 2 đối chiếu, không sửa số) như H1 thiết kế gốc.
- Nếu agent giữ vững phương pháp riêng, chỉ NHẬN XÉT khác biệt mà không sửa số → an toàn, không cần H1.

**Acceptance:** có ít nhất 1 test case với broker note thật trong context, kết luận rõ agent có anchor hay không; nếu CÓ anchor → bổ sung lại H1 (sequencing) đã thiết kế ở bản gốc Round 7.

## THỨ TỰ ĐỀ XUẤT (Round 7, đã cập nhật)
1. **I1** (cấm tự bịa citation trong prompt) — rẻ nhất, làm ngay.
2. **I2** (validator UNGROUNDED_CITATION) — cơ chế bắt tự động, làm cùng lúc với A7/C-series nếu dùng chung infra.
3. **I3** (test case có broker note thật) — làm sau, để xác nhận có cần thêm H1 (sequencing) hay không.

## TEST CASE (Round 7)
(a) Rerun PVD nguyên trạng (không sửa data input) sau khi áp I1+I2 — claim "Phuoc Duong/Vietcap" phải biến mất hoặc bị strip vì News agent chưa chạy lúc Fundamentals tính L2.
(b) Chạy I3 trên 1 mã có broker note thật trong context — xác nhận agent không anchor (hoặc nếu có, bổ sung H1).
(c) Quét batch một số report gần đây (không chỉ PVD) tìm pattern `UNGROUNDED_CITATION` để đánh giá mức độ phổ biến của lỗi này trong các report đã chạy trước đây.

## ÁP DỤNG TỔNG QUÁT
I1-I2 là cơ chế chặn confabulation citation, áp dụng MỌI mã, MỌI agent có thể tạo claim — không hardcode tên CTCK/analyst nào. Đây là lớp bảo vệ độc lập với C-series (Round 3, verify tên tài sản) — cùng họ "grounding" nhưng nhằm vào loại claim khác (nguồn/trích dẫn bên thứ ba thay vì tên tài sản/công ty con).
