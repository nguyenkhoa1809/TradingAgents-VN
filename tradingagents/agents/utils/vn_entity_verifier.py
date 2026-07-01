"""
vn_entity_verifier.py — C1/C2/C5: Company profile grounding + entity claim verification.

Flow:
  fetch_company_profile_block()          → C5: ground truth text từ vnstock
  extract_and_verify_entity_claims()     → C1+C2: extract + verify vs profile trong một LLM call
  build_corrections_block()              → C3: build markdown block để inject vào Phase II
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)


def fetch_company_profile_block(symbol: str, source: str = "VCI") -> str:
    """Fetch company description từ vnstock làm ground truth text cho C5.

    Kết hợp overview + profile + subsidiaries.
    Trả về empty string nếu fetch thất bại — verifier sẽ UNVERIFIED tất cả claims.
    """
    try:
        import vnstock_data  # noqa: F401 — triggers auth
        from vnstock_data import Company
    except ImportError:
        return ""

    parts = []
    try:
        c = Company(symbol=symbol, source=source)

        # overview — metadata công ty
        try:
            ov = c.overview(show=False)
            if ov is not None and not (hasattr(ov, "empty") and ov.empty):
                text = ov.to_string(index=False) if hasattr(ov, "to_string") else str(ov)[:2000]
                parts.append(f"COMPANY OVERVIEW:\n{text}")
        except Exception as e:
            logger.debug("Company.overview failed for %s: %s", symbol, e)

        # profile — mô tả hoạt động kinh doanh, tài sản, nhà máy
        try:
            pr = c.profile(show=False)
            if pr is not None and not (hasattr(pr, "empty") and pr.empty):
                text = pr.to_string(index=False) if hasattr(pr, "to_string") else str(pr)[:3000]
                parts.append(f"COMPANY PROFILE:\n{text}")
        except Exception as e:
            logger.debug("Company.profile failed for %s: %s", symbol, e)

        # subsidiaries — danh sách công ty con/liên kết
        try:
            subs = c.subsidiaries(show=False)
            if subs is not None and not (hasattr(subs, "empty") and subs.empty):
                text = subs.to_string(index=False) if hasattr(subs, "to_string") else str(subs)[:1500]
                parts.append(f"SUBSIDIARIES/AFFILIATES:\n{text}")
        except Exception as e:
            logger.debug("Company.subsidiaries failed for %s: %s", symbol, e)

    except Exception as e:
        logger.warning("Company profile fetch failed for %s (source=%s): %s", symbol, source, e)

    # KBS fallback nếu VCI không có dữ liệu
    if not parts and source == "VCI":
        time.sleep(0.5)
        return fetch_company_profile_block(symbol, source="KBS")

    return "\n\n".join(parts)


def extract_and_verify_entity_claims(
    text: str,
    profile_text: str,
    ticker: str,
    llm,
) -> list[dict]:
    """C1 + C2: Extract verifiable entity claims và verify vs company profile trong một LLM call.

    Trả về list of {entity, relation, target, context_snippet, verdict, source}.
    verdict: SUPPORTED / CONTRADICTED / UNVERIFIED.

    Key design: LLM chỉ được dùng profile_text làm reference — KHÔNG dùng training memory.
    """
    if not text or not text.strip():
        return []

    profile_section = (
        f"\n\nCOMPANY PROFILE (ground truth — dùng DUY NHẤT phần này, "
        f"KHÔNG dùng kiến thức training):\n{profile_text[:3500]}"
        if profile_text.strip()
        else "\n\n(Không có hồ sơ công ty — đánh dấu tất cả claims là UNVERIFIED)"
    )

    prompt = (
        f"Bạn đang fact-check một báo cáo phân tích tài chính cho mã {ticker}.\n\n"
        "BƯỚC 1 — Trích xuất ONLY các claim thực thể có thể verify:\n"
        "- Công ty/thực thể nào SỞ HỮU hoặc VẬN HÀNH tài sản cụ thể "
        "(nhà máy điện, nhà máy, dự án, công ty con)\n"
        "- Trạng thái vận hành dự án, ngày COD, công suất (MW/GW)\n"
        "- Sự kiện doanh nghiệp đã xảy ra (M&A hoàn thành, ký hợp đồng, phát hành xong)\n"
        "KHÔNG trích xuất: dự báo tương lai, định giá, quan điểm thị trường, "
        "nhận định phân tích.\n\n"
        "BƯỚC 2 — Verify từng claim CHỈ DÙNG hồ sơ công ty được cung cấp.\n"
        "Verdict:\n"
        "- SUPPORTED: hồ sơ xác nhận rõ ràng claim này\n"
        "- CONTRADICTED: hồ sơ bác bỏ hoặc mâu thuẫn với claim này\n"
        "- UNVERIFIED: claim không được đề cập trong hồ sơ\n\n"
        f"BÁO CÁO CẦN KIỂM TRA (5000 ký tự đầu):\n{text[:5000]}"
        f"{profile_section}\n\n"
        'Trả về JSON: {"claims": [{"entity": "...", "relation": "...", "target": "...", '
        '"context_snippet": "...", "verdict": "SUPPORTED|CONTRADICTED|UNVERIFIED", '
        '"source": "trích dẫn nguyên văn từ hồ sơ, hoặc \'không có trong hồ sơ\'"}]}\n'
        'Nếu không có claim thực thể nào, trả về {"claims": []}.'
    )

    try:
        from langchain_core.messages import HumanMessage
        resp = llm.invoke([HumanMessage(content=prompt)])
        raw = getattr(resp, "content", None) or str(resp)
        raw = raw.strip()
        # Strip markdown code fences
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        claims = data.get("claims", [])
        # Normalize verdict to uppercase
        for c in claims:
            v = str(c.get("verdict", "UNVERIFIED")).strip().upper()
            if v not in ("SUPPORTED", "CONTRADICTED", "UNVERIFIED"):
                v = "UNVERIFIED"
            c["verdict"] = v
        return claims
    except Exception as e:
        logger.warning("extract_and_verify_entity_claims failed for %s: %s", ticker, e)
        return []


def build_corrections_block(claims: list[dict]) -> str:
    """Build corrections markdown để inject vào Phase II context (C3).

    Trả về empty string nếu không có contradictions hay unverified claims.
    """
    contradicted = [c for c in claims if c.get("verdict") == "CONTRADICTED"]
    unverified = [c for c in claims if c.get("verdict") == "UNVERIFIED"]

    if not contradicted and not unverified:
        return ""

    lines = []

    if contradicted:
        lines.append("\n⚠️ **FACT-CHECK CORRECTIONS — BÁO CÁO FUNDAMENTALS CÓ LỖI THỰC TẾ**")
        lines.append(
            "Các claim sau đây trong báo cáo Fundamentals đã bị **BÁC BỎ** "
            "bởi hồ sơ công ty từ vnstock:"
        )
        for c in contradicted:
            entity = c.get("entity", "")
            relation = c.get("relation", "")
            target = c.get("target", "")
            source = (c.get("source") or "hồ sơ công ty")[:120]
            lines.append(
                f"- ❌ **BÁC BỎ**: \"{entity} {relation} {target}\" "
                f"[nguồn: {source}]"
            )
        lines.append(
            "\n**TUYỆT ĐỐI KHÔNG dùng các claim trên làm luận điểm, catalyst, "
            "hay input định giá ở bất kỳ phase nào. "
            "Nếu buộc phải đề cập thì gắn nhãn ❌ BÁC BỎ.**"
        )

    if unverified:
        lines.append(
            "\n📋 **CLAIM CHƯA KIỂM CHỨNG** "
            "(không tìm thấy trong hồ sơ công ty — "
            "KHÔNG được dùng làm trụ cột luận điểm hoặc catalyst chính):"
        )
        for c in unverified:
            entity = c.get("entity", "")
            relation = c.get("relation", "")
            target = c.get("target", "")
            lines.append(
                f"- ❓ [CHƯA KIỂM CHỨNG]: \"{entity} {relation} {target}\""
            )

    return "\n".join(lines)
