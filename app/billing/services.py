from __future__ import annotations

from datetime import date

from flask import current_app

from app.integrations.core_client import core_client


class BillingGateway:
    def _enabled(self) -> bool:
        return bool(current_app.config.get("CORE_API_URL"))

    def get_plans(self, *, token: str):
        if not self._enabled():
            return [
                {
                    "code": "starter",
                    "name": "Starter",
                    "price": 49,
                    "limits": {"students": 10, "aiCredits": 50, "uploads": 15, "reports": 5},
                },
                {
                    "code": "pro",
                    "name": "Pro",
                    "price": 99,
                    "limits": {"students": 40, "aiCredits": 300, "uploads": 100, "reports": 30},
                },
                {
                    "code": "scale",
                    "name": "Scale",
                    "price": 199,
                    "limits": {"students": 120, "aiCredits": 1000, "uploads": 500, "reports": 150},
                },
            ]
        return core_client.request(method="GET", path="/billing/plans/", token=token)

    def get_subscription(self, *, token: str, org_id: str | None = None):
        if not self._enabled():
            return {
                "planCode": "starter",
                "planName": "Starter",
                "status": "active",
                "billingCycle": "month",
                "amount": 49,
                "currency": "BRL",
                "nextBillingAt": date.today().isoformat(),
                "cancelAtPeriodEnd": False,
                "usage": {"studentsUsed": 6, "aiCreditsUsed": 24, "uploadsUsed": 4, "reportsUsed": 2},
                "paymentMethod": {"brand": "visa", "last4": "4242", "expMonth": "12", "expYear": "2029"},
                "invoices": [],
            }
        if org_id:
            return core_client.request(method="GET", path=f"/orgs/{org_id}/entitlements/", token=token, org_id=org_id)
        return core_client.request(method="GET", path="/billing/subscriptions/me/", token=token)

    def get_entitlements(self, *, token: str, org_id: str | None = None):
        if not self._enabled():
            return {
                "plan_code": "STARTER",
                "features": {"workspace": True, "students": True, "reports": True, "ai": True},
                "limits": {
                    "students": {"max": 10, "remaining": 4},
                    "ai_credits": {"max": 50, "remaining": 26},
                },
                "usage_scope": "organization",
            }
        if org_id:
            return core_client.request(method="GET", path=f"/orgs/{org_id}/entitlements/", token=token, org_id=org_id)
        return core_client.request(method="GET", path="/billing/entitlements/me/", token=token)

    def create_checkout_session(self, *, token: str, plan_code: str, success_url: str, cancel_url: str, org_id: str | None = None):
        if not self._enabled():
            return {"checkout_url": success_url, "mode": "mock", "plan_code": plan_code}
        if org_id:
            return core_client.request(
                method="POST",
                path=f"/orgs/{org_id}/plan-switch/",
                token=token,
                org_id=org_id,
                json={"plan_code": plan_code, "success_url": success_url, "cancel_url": cancel_url},
            )
        return core_client.request(
            method="POST",
            path="/payments/checkout-session/",
            token=token,
            json={"plan_id": plan_code, "success_url": success_url, "cancel_url": cancel_url},
        )

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


billing_gateway = BillingGateway()
