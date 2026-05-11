from __future__ import annotations

from flask import Blueprint, request

from app.referral.services import referral_gateway
from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth

referral_bp = Blueprint("referral", __name__)


def _token():
    return current_auth().user.core_access_token


@referral_bp.get("/referral/link")
@require_auth()
def referral_link():
    """Retorna (ou cria) o link de indicação do personal logado."""
    return success_response(referral_gateway.get_link(token=_token()))


@referral_bp.get("/referral/stats")
@require_auth()
def referral_stats():
    """Dashboard: indicados ativos, crédito mensal, histórico."""
    return success_response(referral_gateway.get_stats(token=_token()))


@referral_bp.get("/referral/credit")
@require_auth()
def referral_credit():
    """Crédito calculado para o mês atual."""
    return success_response(referral_gateway.get_credit(token=_token()))


@referral_bp.post("/referral/register")
@require_auth()
def referral_register():
    """
    Registra que este personal usou um código de indicação ao se cadastrar.
    Body: { "referral_code": "abc123" }
    """
    payload = request.get_json() or {}
    code = (payload.get("referral_code") or "").strip()
    if not code:
        from app.common.api import error_response
        return error_response("referral_code é obrigatório", status=400)
    return success_response(referral_gateway.register(token=_token(), referral_code=code))
