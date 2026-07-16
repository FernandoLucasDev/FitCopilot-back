from __future__ import annotations

from flask import Blueprint, request

from app.billing.services import billing_gateway
from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth


billing_bp = Blueprint("billing", __name__)


def _core_token():
    auth = current_auth()
    return auth.user.core_access_token


@billing_bp.get("/billing/plans")
@require_auth()
def billing_plans():
    return success_response({"items": billing_gateway.get_plans(token=_core_token())})


@billing_bp.get("/billing/subscriptions/me")
@require_auth()
def billing_subscription():
    auth = current_auth()
    org_id = request.headers.get("X-ORG-ID") or (auth.user.account.external_org_id if auth.user.account else None)
    plan_code = auth.user.account.current_plan_code if auth.user.account else None
    return success_response(billing_gateway.get_subscription(token=_core_token(), org_id=org_id, plan_code=plan_code))


@billing_bp.get("/billing/entitlements/me")
@require_auth()
def billing_entitlements():
    auth = current_auth()
    org_id = request.headers.get("X-ORG-ID") or (auth.user.account.external_org_id if auth.user.account else None)
    plan_code = auth.user.account.current_plan_code if auth.user.account else None
    return success_response(billing_gateway.get_entitlements(token=_core_token(), org_id=org_id, plan_code=plan_code))


@billing_bp.get("/billing/checkout-config")
@require_auth({"owner", "professional", "admin"})
def billing_checkout_config():
    return success_response(billing_gateway.get_checkout_config(token=_core_token()))


@billing_bp.post("/billing/checkout-session")
@require_auth({"owner", "professional", "admin"})
def billing_checkout():
    auth = current_auth()
    payload = request.get_json() or {}
    org_id = request.headers.get("X-ORG-ID") or (auth.user.account.external_org_id if auth.user.account else None)
    result = billing_gateway.create_checkout_session(
        token=_core_token(),
        plan_code=payload.get("plan_code") or payload.get("plan_id"),
        success_url=payload.get("success_url") or "http://localhost:3000/planos?ok=1",
        cancel_url=payload.get("cancel_url") or "http://localhost:3000/planos?cancel=1",
        org_id=org_id,
        presentation=payload.get("presentation") or "hosted",
        return_url=payload.get("return_url"),
    )
    return success_response(result)


@billing_bp.post("/billing/portal-session")
@require_auth({"owner", "professional", "admin"})
def billing_portal():
    auth = current_auth()
    org_id = request.headers.get("X-ORG-ID") or (auth.user.account.external_org_id if auth.user.account else None)
    result = billing_gateway.create_portal_session(
        token=_core_token(),
        org_id=org_id,
        return_url=(request.get_json() or {}).get("return_url") or "http://localhost:3000/planos",
    )
    return success_response(result)


@billing_bp.post("/billing/setup-intent")
@require_auth({"owner", "professional", "admin"})
def billing_setup_intent():
    auth = current_auth()
    org_id = request.headers.get("X-ORG-ID") or (auth.user.account.external_org_id if auth.user.account else None)
    return success_response(billing_gateway.create_setup_intent(token=_core_token(), org_id=org_id))


@billing_bp.post("/billing/setup-intent/confirm")
@require_auth({"owner", "professional", "admin"})
def billing_confirm_setup_intent():
    auth = current_auth()
    payload = request.get_json() or {}
    org_id = request.headers.get("X-ORG-ID") or (auth.user.account.external_org_id if auth.user.account else None)
    result = billing_gateway.confirm_setup_intent(
        token=_core_token(),
        setup_intent_id=str(payload.get("setup_intent_id") or ""),
        org_id=org_id,
    )
    return success_response(result)
