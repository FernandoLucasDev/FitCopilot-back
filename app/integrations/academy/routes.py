from __future__ import annotations

from http import HTTPStatus

from flask import Blueprint, current_app, request

from app.accounts.models import Account
from app.common.api import ApiError, success_response
from app.integrations.academy.services import process_academy_webhook

academy_bp = Blueprint("academy", __name__)


@academy_bp.post("/integrations/academy/<provider>/webhook/<account_id>")
def academy_webhook(provider: str, account_id: str):
    secret = request.headers.get("X-Academy-Webhook-Secret")
    expected_secret = current_app.config.get("ACADEMY_WEBHOOK_SECRET")
    if not secret or secret != expected_secret:
        raise ApiError("Acesso ao webhook de academia invalido.", HTTPStatus.UNAUTHORIZED)

    account = Account.query.filter_by(id=account_id, deleted_at=None).first()
    if account is None:
        raise ApiError("Conta nao encontrada", HTTPStatus.NOT_FOUND)

    payload = request.get_json(silent=True) or {}
    result = process_academy_webhook(account_id=account.id, provider=provider, payload=payload)
    return success_response(result)
