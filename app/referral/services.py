"""
Referral Gateway — proxy para a API de referral do DCL-backend.
"""
from __future__ import annotations
from flask import current_app
from app.integrations.core_client import core_client


class ReferralGateway:
    def _enabled(self) -> bool:
        return bool(current_app.config.get("CORE_API_URL"))

    # ------------------------------------------------------------------
    # Link
    # ------------------------------------------------------------------

    def get_link(self, *, token: str) -> dict:
        if not self._enabled():
            return {
                "code": "DEMO123",
                "url": "https://app.fitcopilot.com.br/cadastro?ref=DEMO123",
                "is_active": True,
            }
        return core_client.request(method="GET", path="/referral/link/", token=token)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self, *, token: str) -> dict:
        if not self._enabled():
            return {
                "referral_code": "DEMO123",
                "referral_url": "https://app.fitcopilot.com.br/cadastro?ref=DEMO123",
                "active_referrals": 1,
                "pending_referrals": 1,
                "churned_referrals": 0,
                "monthly_credit_brl": "25.00",
                "gross_credit_brl": "25.00",
                "credit_cap_brl": "75.00",
                "next_threshold": {"referrals_needed": 2, "credit_at_next": "50.00"},
                "recent_conversions": [
                    {"email": "demo@aluno.com", "status": "active", "activated_at": "2026-05-01T00:00:00"}
                ],
            }
        raw = core_client.request(method="GET", path="/referral/stats/", token=token)
        return {
            "referral_code": raw.get("referral_code"),
            "referral_url": raw.get("referral_url") or raw.get("referral_link"),
            "active_referrals": int(raw.get("active_referrals") or raw.get("total_active_referrals") or 0),
            "pending_referrals": int(raw.get("pending_referrals") or 0),
            "churned_referrals": int(raw.get("churned_referrals") or 0),
            "monthly_credit_brl": str(raw.get("monthly_credit_brl") or raw.get("total_commission_pending") or "0.00"),
            "gross_credit_brl": str(raw.get("gross_credit_brl") or raw.get("total_commission_generated") or "0.00"),
            "credit_cap_brl": str(raw.get("credit_cap_brl") or "75.00"),
            "next_threshold": raw.get("next_threshold"),
            "recent_conversions": raw.get("recent_conversions") or [],
        }

    # ------------------------------------------------------------------
    # Crédito mensal
    # ------------------------------------------------------------------

    def get_credit(self, *, token: str) -> dict:
        if not self._enabled():
            return {
                "period_key": "2026-05",
                "active_referrals": 1,
                "gross_credit_brl": "25.00",
                "capped_credit_brl": "25.00",
                "stripe_credit_applied": None,
                "applied_at": None,
                "is_applied": False,
            }
        return core_client.request(method="GET", path="/referral/credit/", token=token)

    # ------------------------------------------------------------------
    # Registrar indicação (novo personal usou o link)
    # ------------------------------------------------------------------

    def register(self, *, token: str, referral_code: str) -> dict:
        if not self._enabled():
            return {"ok": True, "status": "pending", "mode": "mock"}
        return core_client.request(
            method="POST",
            path="/referral/register/",
            token=token,
            json={"referral_code": referral_code},
        )


referral_gateway = ReferralGateway()
