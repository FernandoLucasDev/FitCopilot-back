from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from app.common.api import ApiError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LocalCoreResponse:
    payload: dict[str, Any] | list[Any] | None


class LocalCoreGateway:
    def _state_file(self) -> Path:
        file_path = Path(current_app.config["LOCAL_CORE_STATE_FILE"])
        if not file_path.is_absolute():
            file_path = Path(current_app.root_path).parent / file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    def _default_state(self) -> dict[str, Any]:
        return {
            "users": {},
            "orgs": {},
            "tokens": {},
            "refresh_tokens": {},
            "plans": [
                {
                    "code": "free",
                    "name": "Starter",
                    "price": 0,
                    "description": "Para profissionais com carteira enxuta e acompanhamento proximo.",
                    "recommended": False,
                    "limits": {"students": 5, "aiCredits": 100, "uploads": 10, "reports": 2},
                    "features": [
                        {"name": "Carteira operacional", "included": True},
                        {"name": "Leituras de IA do dia", "included": True},
                        {"name": "Checkout e billing local", "included": False},
                    ],
                },
                {
                    "code": "pro",
                    "name": "Pro",
                    "price": 89,
                    "description": "Melhor equilibrio para acompanhamento frequente com IA pragmatica.",
                    "recommended": True,
                    "limits": {"students": 30, "aiCredits": 2000, "uploads": 100, "reports": 30},
                    "features": [
                        {"name": "Workspace completo", "included": True},
                        {"name": "Painel agregado do aluno", "included": True},
                        {"name": "Sugestoes de mensagem", "included": True},
                    ],
                },
                {
                    "code": "elite",
                    "name": "Elite",
                    "price": 149,
                    "description": "Operacao maior com mais margem de IA, relatorios e automacoes futuras.",
                    "recommended": False,
                    "limits": {"students": 9999, "aiCredits": 9999, "uploads": 500, "reports": 150},
                    "features": [
                        {"name": "Uso intensivo de IA", "included": True},
                        {"name": "Relatorios em escala", "included": True},
                        {"name": "Preparado para WhatsApp", "included": True},
                    ],
                },
                {
                    "code": "academia",
                    "name": "Academia",
                    "price": 349,
                    "description": "Operacao multi-profissional com assentos incluidos para a equipe.",
                    "recommended": False,
                    "limits": {"students": 9999, "aiCredits": 9999, "uploads": 9999, "reports": 9999},
                    "features": [
                        {"name": "5 profissionais incluidos", "included": True},
                        {"name": "Dashboard centralizado", "included": True},
                        {"name": "Billing por assentos", "included": True},
                    ],
                },
            ],
        }

    def _load_state(self) -> dict[str, Any]:
        file_path = self._state_file()
        if not file_path.exists():
            state = self._default_state()
            self._save_state(state)
            return state
        return json.loads(file_path.read_text(encoding="utf-8"))

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_file().write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")

    def _require_user_by_token(self, state: dict[str, Any], token: str | None) -> dict[str, Any]:
        if not token:
            raise ApiError("Sessao CORE ausente", HTTPStatus.UNAUTHORIZED)
        email = state["tokens"].get(token)
        if not email or email not in state["users"]:
            raise ApiError("Sessao CORE invalida", HTTPStatus.UNAUTHORIZED)
        return state["users"][email]

    def ensure_seed_professional(self, *, full_name: str, email: str, password: str, org_name: str, org_slug: str) -> None:
        state = self._load_state()
        if email in state["users"]:
            return
        org_id = f"local-org-{org_slug}"
        state["orgs"][org_id] = self._build_org_payload(name=org_name, slug=org_slug, owner_email=email)
        state["users"][email] = {
            "id": len(state["users"]) + 1,
            "email": email,
            "full_name": full_name,
            "phone": None,
            "password_hash": generate_password_hash(password),
            "org_id": org_id,
            "role": "owner",
        }
        self._save_state(state)

    def request(
        self,
        *,
        method: str,
        path: str,
        token: str | None = None,
        json_payload: dict[str, Any] | None = None,
        org_id: str | None = None,
    ) -> Any:
        state = self._load_state()
        payload = json_payload or {}
        normalized_path = path.rstrip("/")

        if method == "POST" and normalized_path == "/auth/register":
            return self._register(state, payload)
        if method == "POST" and normalized_path == "/auth/login":
            return self._login(state, payload)
        if method == "GET" and normalized_path == "/auth/me":
            return self._me(state, token)
        if method == "POST" and normalized_path == "/auth/refresh":
            return self._refresh(state, payload)
        if method == "GET" and normalized_path == "/billing/plans":
            return state["plans"]
        if method == "GET" and normalized_path == "/billing/subscriptions/me":
            user = self._require_user_by_token(state, token)
            return self._subscription_payload(state, user["org_id"])
        if method == "GET" and normalized_path == "/payments/config":
            return {"publishable_key": "pk_test_local_fitcopilot", "mode": "mock"}
        if method == "POST" and normalized_path == "/payments/setup-intent":
            user = self._require_user_by_token(state, token)
            return self._setup_intent(state, user["org_id"])
        if method == "POST" and normalized_path == "/payments/setup-intent/confirm":
            user = self._require_user_by_token(state, token)
            return self._confirm_setup_intent(state, user["org_id"], payload)
        if method == "GET" and normalized_path == "/billing/entitlements/me":
            user = self._require_user_by_token(state, token)
            return self._entitlements_payload(state, user["org_id"])
        if method == "POST" and normalized_path == "/payments/checkout-session":
            user = self._require_user_by_token(state, token)
            plan_code = payload.get("plan_id") or payload.get("plan_code")
            return self._checkout(state, user["org_id"], plan_code, payload)
        if method == "POST" and normalized_path == "/payments/portal-session":
            user = self._require_user_by_token(state, token)
            return self._portal(user["org_id"], payload)
        if normalized_path.endswith("/entitlements") and org_id:
            return self._entitlements_payload(state, org_id)
        if normalized_path.endswith("/billing-summary") and org_id:
            return self._subscription_payload(state, org_id)
        if normalized_path.endswith("/setup-intent") and org_id:
            return self._setup_intent(state, org_id)
        if normalized_path.endswith("/setup-intent/confirm") and org_id:
            return self._confirm_setup_intent(state, org_id, payload)
        if normalized_path.endswith("/plan-switch") and org_id:
            plan_code = payload.get("plan_code")
            return self._checkout(state, org_id, plan_code, payload)
        if normalized_path.endswith("/portal") and org_id:
            return self._portal(org_id, payload)
        raise ApiError(f"Endpoint CORE local nao suportado: {method} {path}", HTTPStatus.NOT_IMPLEMENTED)

    def _register(self, state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        email = str(payload["email"]).strip().lower()
        if email in state["users"]:
            raise ApiError("Usuario ja existe no CORE local", HTTPStatus.CONFLICT)
        org_slug = email.split("@")[0].replace(".", "-")
        org_id = f"local-org-{org_slug}"
        state["orgs"][org_id] = self._build_org_payload(name=f"Workspace {payload['full_name']}", slug=org_slug, owner_email=email)
        state["users"][email] = {
            "id": len(state["users"]) + 1,
            "email": email,
            "full_name": payload["full_name"],
            "phone": payload.get("phone"),
            "password_hash": generate_password_hash(payload["password"]),
            "org_id": org_id,
            "role": "owner",
        }
        self._save_state(state)
        return self._issue_session(state, state["users"][email])

    def _login(self, state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        email = str(payload["email"]).strip().lower()
        user = state["users"].get(email)
        if not user or not check_password_hash(user["password_hash"], payload["password"]):
            raise ApiError("Credenciais invalidas no CORE local", HTTPStatus.UNAUTHORIZED)
        self._save_state(state)
        return self._issue_session(state, user)

    def _me(self, state: dict[str, Any], token: str | None) -> dict[str, Any]:
        user = self._require_user_by_token(state, token)
        org = state["orgs"][user["org_id"]]
        return {
            "user": {"id": user["id"], "email": user["email"], "full_name": user["full_name"], "role": user["role"]},
            "account": {"org_id": org["id"], "name": org["name"], "slug": org["slug"]},
            "organizations": [{"organization": {"id": org["id"], "name": org["name"], "slug": org["slug"]}}],
        }

    def _refresh(self, state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        refresh_token = payload.get("refresh_token")
        email = state["refresh_tokens"].get(refresh_token)
        if not email or email not in state["users"]:
            raise ApiError("Refresh token invalido", HTTPStatus.UNAUTHORIZED)
        return self._issue_session(state, state["users"][email])

    def _issue_session(self, state: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
        access = f"local-core-access-{secrets.token_hex(12)}"
        refresh = f"local-core-refresh-{secrets.token_hex(12)}"
        state["tokens"][access] = user["email"]
        state["refresh_tokens"][refresh] = user["email"]
        self._save_state(state)
        org = state["orgs"][user["org_id"]]
        return {
            "access": access,
            "refresh_token": refresh,
            "user": {"id": user["id"], "email": user["email"], "full_name": user["full_name"], "role": user["role"]},
            "org_id": org["id"],
            "account": {"org_id": org["id"], "name": org["name"], "slug": org["slug"]},
            "organizations": [{"organization": {"id": org["id"], "name": org["name"], "slug": org["slug"]}}],
        }

    def _build_org_payload(self, *, name: str, slug: str, owner_email: str) -> dict[str, Any]:
        return {
            "id": f"org-{slug}",
            "name": name,
            "slug": slug,
            "owner_email": owner_email,
            "plan_code": "free",
            "students_used": 6,
            "ai_credits_used": 24,
            "uploads_used": 4,
            "reports_used": 2,
            "payment_method": {"brand": "visa", "last4": "4242", "expMonth": "12", "expYear": "2029", "holderName": name},
            "invoices": [],
        }

    def _plan_by_code(self, state: dict[str, Any], code: str) -> dict[str, Any]:
        for plan in state["plans"]:
            if plan["code"] == code:
                return plan
        raise ApiError("Plano nao encontrado no CORE local", HTTPStatus.NOT_FOUND)

    def _subscription_payload(self, state: dict[str, Any], org_id: str) -> dict[str, Any]:
        org = state["orgs"][org_id]
        plan = self._plan_by_code(state, org["plan_code"])
        return {
            "planCode": plan["code"],
            "planName": plan["name"],
            "status": "active",
            "billingCycle": "month",
            "amount": plan["price"],
            "currency": "BRL",
            "nextBillingAt": (date.today() + timedelta(days=30)).isoformat(),
            "cancelAtPeriodEnd": False,
            "usage": {
                "studentsUsed": org["students_used"],
                "aiCreditsUsed": org["ai_credits_used"],
                "uploadsUsed": org["uploads_used"],
                "reportsUsed": org["reports_used"],
            },
            "paymentMethod": org["payment_method"],
            "invoices": org["invoices"],
        }

    def _entitlements_payload(self, state: dict[str, Any], org_id: str) -> dict[str, Any]:
        org = state["orgs"][org_id]
        plan = self._plan_by_code(state, org["plan_code"])
        return {
            "plan_code": plan["code"].upper(),
            "features": {
                "workspace": True,
                "students": True,
                "reports": True,
                "ai": True,
                "billing_proxy": True,
            },
            "limits": {
                "students": {"max": plan["limits"]["students"], "remaining": max(plan["limits"]["students"] - org["students_used"], 0)},
                "ai_credits": {
                    "max": plan["limits"]["aiCredits"],
                    "remaining": max(plan["limits"]["aiCredits"] - org["ai_credits_used"], 0),
                },
            },
            "usage_scope": "organization",
        }

    def _checkout(self, state: dict[str, Any], org_id: str, plan_code: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        if not plan_code:
            raise ApiError("plan_code e obrigatorio", HTTPStatus.BAD_REQUEST)
        org = state["orgs"][org_id]
        plan = self._plan_by_code(state, plan_code)
        org["plan_code"] = plan["code"]
        invoice = {
            "id": f"inv-{secrets.token_hex(6)}",
            "amount": plan["price"],
            "currency": "BRL",
            "status": "paid",
            "issuedAt": utcnow().date().isoformat(),
            "paidAt": utcnow().isoformat(),
            "invoiceUrl": f"http://localhost:3000/billing/invoices/{plan['code']}",
        }
        org["invoices"] = [invoice, *org["invoices"]][:10]
        self._save_state(state)
        return {
            "mode": "local-core",
            "plan_code": plan["code"],
            "url": payload.get("success_url") or "http://localhost:3000/plans?ok=1",
            "subscription": self._subscription_payload(state, org_id),
        }

    def _portal(self, org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": "local-core",
            "org_id": org_id,
            "url": payload.get("return_url") or "http://localhost:3000/billing",
        }

    def _setup_intent(self, state: dict[str, Any], org_id: str) -> dict[str, Any]:
        org = state["orgs"][org_id]
        setup_intent_id = f"seti_{secrets.token_hex(8)}"
        org["pending_setup_intent"] = setup_intent_id
        self._save_state(state)
        return {
            "client_secret": f"{setup_intent_id}_secret_local",
            "setup_intent_id": setup_intent_id,
            "mode": "mock",
        }

    def _confirm_setup_intent(self, state: dict[str, Any], org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        org = state["orgs"][org_id]
        setup_intent_id = str(payload.get("setup_intent_id") or "")
        if not setup_intent_id or setup_intent_id != org.get("pending_setup_intent"):
            raise ApiError("setup_intent_id invalido", HTTPStatus.BAD_REQUEST)
        org["pending_setup_intent"] = None
        self._save_state(state)
        payment_method = dict(org.get("payment_method") or {})
        return {"payment_method": payment_method, "mode": "mock"}


local_core_gateway = LocalCoreGateway()
