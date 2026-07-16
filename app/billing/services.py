from __future__ import annotations

from datetime import date
import logging
from typing import Any

from flask import current_app
import requests

from app.integrations.core_client import core_client


logger = logging.getLogger(__name__)


class BillingGateway:
    PLAN_ALIASES: dict[str, str] = {
        "STARTER": "FREE",
        "PREMIUM": "ELITE",
        "SCALE": "ELITE",
        "PRO_TESTE_199": "PRO",
    }

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
        return bool(current_app.config.get("CORE_API_URL")) and current_app.config.get("CORE_PROXY_MODE") != "disabled"

    def _plan_by_code(self, code: str | None) -> dict[str, Any]:
        normalized = self._canonical_plan_code(code)
        return next((plan for plan in self.LOCAL_PLANS if plan["code"] == normalized), self.LOCAL_PLANS[0])

    def _canonical_plan_code(self, code: str | None) -> str:
        normalized = str(code or "FREE").strip().upper()
        return self.PLAN_ALIASES.get(normalized, normalized)

    def _safe_core_request(self, *, fallback: Any, operation: str, **kwargs) -> Any:
        try:
            return core_client.request(**kwargs)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning("Core billing %s failed status=%s; using local fallback", operation, status)
            return fallback

    def _local_subscription(self, *, plan_code: str | None = None) -> dict[str, Any]:
        plan = self._plan_by_code(plan_code)
        return {
            "planCode": plan["code"],
            "planName": plan["name"],
            "status": "active",
            "billingCycle": "month",
            "amount": plan["price"],
            "currency": "BRL",
            "nextBillingAt": date.today().isoformat(),
            "cancelAtPeriodEnd": False,
            "usage": {"studentsUsed": 0, "aiCreditsUsed": 0, "uploadsUsed": 0, "reportsUsed": 0},
            "paymentMethod": None,
            "invoices": [],
            "source": "local_fallback",
        }

    def _local_entitlements(self, *, plan_code: str | None = None) -> dict[str, Any]:
        plan = self._plan_by_code(plan_code)
        return {
            "plan_code": plan["code"],
            "features": {
                "clients": True,
                "workouts": True,
                "automations": plan["code"] in {"PRO", "ELITE", "ACADEMIA"},
                "whatsapp_auto": plan["code"] in {"PRO", "ELITE", "ACADEMIA"},
                "ai_advanced": plan["code"] in {"ELITE", "ACADEMIA"},
                "academy_dashboard": plan["code"] == "ACADEMIA",
            },
            "limits": {
                "clients.active.max_count": plan["limits"]["students"],
                "ai.calls.monthly_count": plan["limits"]["aiCredits"],
                "uploads.monthly_count": plan["limits"]["uploads"],
                "reports.monthly_count": plan["limits"]["reports"],
            },
            "usage_scope": "organization",
            "source": "local_fallback",
        }

    def get_plans(self, *, token: str):
        if not self._enabled():
            return self.LOCAL_PLANS
        raw = self._safe_core_request(
            fallback=self.LOCAL_PLANS,
            operation="plans",
            method="GET",
            path="/billing/plans/",
            token=token,
        )
        if isinstance(raw, dict):
            raw = raw.get("results") or raw.get("items") or raw.get("data") or self.LOCAL_PLANS
        items = raw if isinstance(raw, list) else self.LOCAL_PLANS
        normalized_by_code: dict[str, dict[str, Any]] = {}
        priority_by_code: dict[str, tuple[int, int]] = {}
        for item in items:
            candidate = self._normalize_core_plan(item)
            if not candidate:
                continue
            code = str(candidate["code"]).upper()
            raw_code = str(item.get("code") or item.get("slug") or code).strip().upper()
            priority = (
                0 if self._canonical_plan_code(raw_code) == raw_code else 1,
                0 if str(item.get("stripe_price_id") or "").strip() else 1,
            )
            if code not in normalized_by_code or priority < priority_by_code[code]:
                normalized_by_code[code] = candidate
                priority_by_code[code] = priority
        normalized = list(normalized_by_code.values())
        normalized.sort(
            key=lambda item: next(
                (index for index, plan in enumerate(self.LOCAL_PLANS) if plan["code"] == item["code"]),
                len(self.LOCAL_PLANS),
            )
        )
        return normalized or self.LOCAL_PLANS

    def get_subscription(self, *, token: str, org_id: str | None = None, plan_code: str | None = None):
        fallback = self._local_subscription(plan_code=plan_code)
        if not self._enabled():
            return fallback
        if not token:
            return fallback
        if org_id:
            return self._safe_core_request(
                fallback=fallback,
                operation="org billing summary",
                method="GET",
                path=f"/orgs/{org_id}/billing-summary/",
                token=token,
                org_id=org_id,
            )
        return self._safe_core_request(
            fallback=fallback,
            operation="subscription",
            method="GET",
            path="/billing/subscriptions/me/",
            token=token,
        )

    def get_entitlements(self, *, token: str, org_id: str | None = None, plan_code: str | None = None):
        fallback = self._local_entitlements(plan_code=plan_code)
        if not self._enabled():
            return fallback
        if not token:
            return fallback
        if org_id:
            return self._safe_core_request(
                fallback=fallback,
                operation="org entitlements",
                method="GET",
                path=f"/orgs/{org_id}/entitlements/",
                token=token,
                org_id=org_id,
            )
        return self._safe_core_request(
            fallback=fallback,
            operation="entitlements",
            method="GET",
            path="/billing/entitlements/me/",
            token=token,
        )

    def get_checkout_config(self, *, token: str):
        if not self._enabled():
            return {"publishable_key": None, "mode": "mock"}
        if not token:
            return {"publishable_key": None, "mode": "unavailable"}
        return self._safe_core_request(
            fallback={"publishable_key": None, "mode": "unavailable"},
            operation="checkout config",
            method="GET",
            path="/payments/config/",
            token=token,
        )

    def create_checkout_session(
        self,
        *,
        token: str,
        plan_code: str,
        success_url: str,
        cancel_url: str,
        org_id: str | None = None,
        presentation: str = "hosted",
        return_url: str | None = None,
    ):
        if not self._enabled():
            return {"checkout_url": success_url, "url": success_url, "mode": "mock", "plan_code": plan_code}
        if org_id:
            result = core_client.request(
                method="POST",
                path=f"/orgs/{org_id}/checkout/",
                token=token,
                org_id=org_id,
                json={
                    "plan_code": plan_code,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "presentation": presentation,
                    "return_url": return_url,
                },
            )
            if isinstance(result, dict) and result.get("checkout_url") and not result.get("url"):
                result["url"] = result["checkout_url"]
            return result
        result = core_client.request(
            method="POST",
            path="/payments/checkout-session/",
            token=token,
            json={
                "plan_id": plan_code,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "presentation": presentation,
                "return_url": return_url,
            },
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

    def create_setup_intent(self, *, token: str, org_id: str | None = None):
        if not self._enabled():
            return {"client_secret": None, "setup_intent_id": None, "mode": "mock"}
        if org_id:
            return core_client.request(
                method="POST",
                path=f"/orgs/{org_id}/setup-intent/",
                token=token,
                org_id=org_id,
            )
        return core_client.request(method="POST", path="/payments/setup-intent/", token=token)

    def confirm_setup_intent(self, *, token: str, setup_intent_id: str, org_id: str | None = None):
        if not self._enabled():
            return {
                "mode": "mock",
                "payment_method": {
                    "brand": "visa",
                    "last4": "4242",
                    "exp_month": 12,
                    "exp_year": 2030,
                },
            }
        if org_id:
            return core_client.request(
                method="POST",
                path=f"/orgs/{org_id}/setup-intent/confirm/",
                token=token,
                org_id=org_id,
                json={"setup_intent_id": setup_intent_id},
            )
        return core_client.request(
            method="POST",
            path="/payments/setup-intent/confirm/",
            token=token,
            json={"setup_intent_id": setup_intent_id},
        )

    def _normalize_core_plan(self, item: dict[str, Any]) -> dict[str, Any] | None:
        code = self._canonical_plan_code(item.get("code") or item.get("slug") or "FREE")
        if code not in {plan["code"] for plan in self.LOCAL_PLANS}:
            return None
        stripe_price_id = str(item.get("stripe_price_id") or "").strip()
        if code != "FREE" and "stripe_price_id" in item and not stripe_price_id:
            return None
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
        plan = self._plan_by_code(entitlements.get("plan_code") or (entitlements.get("plan") or {}).get("code"))
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
