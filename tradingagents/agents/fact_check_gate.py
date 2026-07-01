"""
fact_check_gate.py — C3 Gate node.

Runs between Phase I analysts and Phase II researchers (Bull/Bear debate).
Detects hallucinated entity claims in fundamentals_report via C1/C2 verifier.
Injects corrections into state so Phase II agents cannot use contradicted/unverified claims.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def create_fact_check_gate(llm):
    """C3 Gate: verify entity claims in fundamentals_report before Phase II."""

    def fact_check_gate_node(state) -> dict:
        from tradingagents.dataflows.market_router import is_vn_ticker
        from tradingagents.agents.utils.vn_entity_verifier import (
            extract_and_verify_entity_claims,
            fetch_company_profile_block,
            build_corrections_block,
        )

        ticker = state["company_of_interest"]

        # Gate chỉ áp dụng cho mã VN
        if not is_vn_ticker(ticker):
            return {"fact_check_corrections": "", "verified_entity_claims": ""}

        fundamentals_report = state.get("fundamentals_report", "")
        if not fundamentals_report:
            return {"fact_check_corrections": "", "verified_entity_claims": ""}

        print(f"  [FactCheck] Verifying entity claims in {ticker} fundamentals report...")

        # Dùng profile pre-fetched ở C5 (trading_graph) nếu có, không thì fetch lại
        profile_text = state.get("company_profile_block", "") or ""
        if not profile_text:
            profile_text = fetch_company_profile_block(ticker)

        claims = extract_and_verify_entity_claims(
            fundamentals_report, profile_text, ticker, llm
        )

        contradicted = [c for c in claims if c.get("verdict") == "CONTRADICTED"]
        unverified   = [c for c in claims if c.get("verdict") == "UNVERIFIED"]
        supported    = [c for c in claims if c.get("verdict") == "SUPPORTED"]

        print(
            f"  [FactCheck] {len(supported)} SUPPORTED | "
            f"{len(unverified)} UNVERIFIED | "
            f"{len(contradicted)} CONTRADICTED"
        )

        if contradicted:
            for c in contradicted:
                print(
                    f"  [FactCheck] ❌ CONTRADICTED: "
                    f"\"{c.get('entity','')} {c.get('relation','')} {c.get('target','')}\""
                )

        corrections_md = build_corrections_block(claims)

        if corrections_md:
            print(
                f"  [FactCheck] Corrections injected into Phase II context "
                f"({len(contradicted)} contradicted, {len(unverified)} unverified)"
            )

        return {
            "fact_check_corrections": corrections_md,
            "verified_entity_claims": json.dumps(claims, ensure_ascii=False),
        }

    return fact_check_gate_node
