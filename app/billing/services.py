from __future__ import annotations

from datetime import date
from typing import Any

from flask import current_app

from app.integrations.core_client import core_client


class BillingGateway:
    LOCAL_PLANS: list[dict[str, Any]] = [
        {
            "code": "FREE",
            "name": "Starter",
            "price": 0,
            "description": "Para começar sem compromisso.",
            "recommended": False,
            "limits": {"students": 5, "aiCredits": 100, "uploads": 10, "reports": 2},
            "features": [
                {"name": "Até 5 alunos", "included": True},
                {"name": "Fichas de treino", "included": True},
                {"name": "Check-in manual", "included": True},
                {"name": "WhatsApp automático", "included": False},
                {"name": "Automações", "included": False},
                {"name": "IA avançada", "included": False},
            ],
        },
        {
            "code": "PRO",
            "name": "Pro",
            "price": 89,
            "description": "Para quem quer crescer com consistência.",
            "recommended": True,
            "limits": {"students": 30, "aiCredits": 2000, "uploads": 100, "reports": 30},
            "features": [
                {"name": "Até 30 alunos", "included": True},
                {"name": "WhatsApp automático", "included": True},
                {"name": "Automações ativas", "included": True},
                {"name": "Análise de refeição", "included": True},
                {"name": "IA avançada", "included": False},
                {"name": "Dashboard Academia", "included": False},
            ],
        },
        {
            "code": "ELITE",
            "name": "Elite",
            "price": 149,
            "description": "Sem limite operacional para personal estabelecido.",
            "recommended": False,
            "limits": {"students": 9999, "aiCredits": 9999, "uploads": 500, "reports": 150},
            "features": [
                {"name": "Alunos ilimitados", "included": True},
                {"name": "WhatsApp automático", "included": True},
                {"name": "IA avançada", "included": True},
                {"name": "Análise de foto de refeição", "included": True},
                {"name": "Insights prioritários", "included": True},
                {"name": "Dashboard Academia", "included": False},
            ],
        },
        {
            "code": "ACADEMIA",
            "name": "Academia",
            "price": 349,
            "description": "Para academias com 5 profissionais incluídos.",
            "recommended": False,
            "limits": {"students": 9999, "aiCredits": 9999, "uploads": 9999, "reports": 9999},
            "features": [
                {"name": "5 personals incluídos", "included": True},
                {"name": "+R$60 por personal extra", "included": True},
                {"name": "Dashboard centralizado", "included": True},
                {"name": "Tudo do Elite", "included": True},
                {"name": "IA avançada", "included": True},
                {"name": "Dashboard Academia", "included": True},
            ],
        },
    ]

    def _enabled(self) -> bool:
        return bool(current_app.config.get("CORE_API_URL"))

    def _plan_by_code(self, code: str | None) -> dict[str, Any]:
        normalized = str(code or "FREE").upper()
        return next((plan for plan in self.LOCAL_PLANS if plan["code"] == normalized), self.LOCAL_PLANS[0])

    def get_plans(self, *, token: str):
        if not self._enabled():
            return self.LOCAL_PLANS
        raw = core_client.request(method="GET", path="/billing/plans/", token=token)
        return [self._normalize_core_plan(item) for item in raw]

    def get_subscription(self, *, token: str, org_id: str | None = None):
        if not self._enabled():
            return {
                "planCode": "FREE",
                "planName": "Starter",
                "status": "active",
                "billingCycle": "month",
                "amount": 0,
                "currency": "BRL",
                "nextBillingAt": date.today().isoformat(),
                "cancelAtPeriodEnd": False,
                "usage": {"studentsUsed": 0, "aiCreditsUsed": 0, "uploadsUsed": 0, "reportsUsed": 0},
                "paymentMethod": None,
                "invoices": [],
            }
        if org_id:
            entitlements = core_client.request(method="GET", path=f"/orgs/{org_id}/entitlements/", token=token, org_id=org_id)
            return self._subscription_from_entitlements(entitlements)
        return core_client.request(method="GET", path="/billing/subscriptions/me/", token=token)

    def get_entitlements(self, *, token: str, org_id: str | None = None):
        if not self._enabled():
            return {
                "plan_code": "FREE",
                "features": {"clients": True, "workouts": True, "automations": False, "whatsapp_auto": False},
                "limits": {"clients.active.max_count": 5, "ai.calls.monthly_count": 100},
                "usage_scope": "organization",
            }
        if org_id:
            return core_client.request(method="GET", path=f"/orgs/{org_id}/entitlements/", token=token, org_id=org_id)
        return core_client.request(method="GET", path="/billing/entitlements/me/", token=token)

    def create_checkout_session(self, *, token: str, plan_code: str, success_url: str, cancel_url: str, org_id: str | None = None):
        if not self._enabled():
            return {"checkout_url": success_url, "url": success_url, "mode": "mock", "plan_code": plan_code}
        if org_id:
            result = core_client.request(
                method="POST",
                path=f"/orgs/{org_id}/plan-switch/",
                token=token,
                org_id=org_id,
                json={"plan_code": plan_code, "success_url": success_url, "cancel_url": cancel_url},
            )
            if isinstance(result, dict) and result.get("checkout_url") and not result.get("url"):
                result["url"] = result["checkout_url"]
            return result
        result = core_client.request(
            method="POST",
            path="/payments/checkout-session/",
            token=token,
            json={"plan_id": plan_code, "success_url": success_url, "cancel_url": cancel_url},
        )
        if isinstance(result, dict) and result.get("checkout_url") and not result.get("url"):
            result["url"] = result["checkout_url"]
        return result

    def create_portal_session(self, *, token: str, org_id: str | None = None, return_url: str | None = None):
        if not self._enabled():
            return {"portal_url": return_url or "http://localhost:3000/billing", "mode": "mock"}
        if org_id:
            return core_client.request(
                method="POST",
                path=f"/orgs/{org_id}/portal/",
                token=token,
                org_id=org_id,
                json={"return_url": return_url},
            )
        return core_client.request(method="POST", path="/payments/portal-session/", token=token)

    def _normalize_core_plan(self, item: dict[str, Any]) -> dict[str, Any]:
        code = str(item.get("code") or item.get("slug") or "FREE").upper()
        local = self._plan_by_code(code)
        return {
            **local,
            "code": code,
            "name": item.get("name") or local["name"],
            "description": item.get("description") or local["description"],
            "price": int(item.get("price_cents") or 0) / 100,
            "currency": item.get("currency") or "BRL",
            "billingCycle": item.get("interval") or "month",
        }

    def _subscription_from_entitlements(self, entitlements: dict[str, Any]) -> dict[str, Any]:
        plan = self._plan_by_code(entitlements.get("plan_code"))
        usage = entitlements.get("usage") or {}
        return {
            "planCode": plan["code"],
            "planName": plan["name"],
            "status": str(entitlements.get("subscription_status") or "ACTIVE").lower(),
            "billingCycle": "month",
            "amount": plan["price"],
            "currency": "BRL",
            "nextBillingAt": entitlements.get("current_period_end") or date.today().isoformat(),
            "cancelAtPeriodEnd": bool(entitlements.get("cancel_at_period_end")),
            "usage": {
                "studentsUsed": int(usage.get("students") or usage.get("clients") or 0),
                "aiCreditsUsed": int(usage.get("ai_calls_monthly") or usage.get("ai_tokens_month") or 0),
                "uploadsUsed": int(usage.get("uploads") or 0),
                "reportsUsed": int(usage.get("reports") or 0),
            },
            "paymentMethod": None,
            "invoices": [],
        }


billing_gateway = BillingGateway()
