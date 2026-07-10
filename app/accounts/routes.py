from __future__ import annotations

from flask import Blueprint, request

from app.accounts.enterprise_services import get_network_dashboard
from app.accounts.onboarding_service import commit_onboarding_import, parse_onboarding_csv
from app.accounts.schemas import CommitOnboardingInput, UpdateAccountInput, UpdateBrandConfigInput
from app.accounts.services import (
    save_account_logo,
    serialize_account,
    update_account_branding,
    update_account_vertical,
)
from app.common.api import ApiError, success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth, require_enterprise_role
from http import HTTPStatus


accounts_bp = Blueprint("accounts", __name__)


@accounts_bp.get("/account")
@require_auth()
def get_account():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    return success_response({"account": serialize_account(account)})


@accounts_bp.patch("/account")
@require_auth({"owner", "admin"})
def patch_account():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    payload = parse_json(UpdateAccountInput)
    account = update_account_vertical(
        account=account,
        actor_user_id=auth.user.id,
        professional_vertical=payload.professional_vertical,
    )
    return success_response({"account": serialize_account(account)})


@accounts_bp.patch("/account/branding")
@require_auth({"owner", "admin"})
def patch_account_branding():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    payload = parse_json(UpdateBrandConfigInput)
    account = update_account_branding(account=account, actor_user_id=auth.user.id, data=payload)
    return success_response({"account": serialize_account(account)})


@accounts_bp.post("/account/branding/logo")
@require_auth({"owner", "admin"})
def upload_account_logo():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    upload = request.files.get("file")
    if upload is None:
        raise ApiError("Arquivo ausente", HTTPStatus.BAD_REQUEST)
    logo_url = save_account_logo(
        account=account,
        actor_user_id=auth.user.id,
        content=upload.read(),
        mime_type=upload.mimetype,
    )
    return success_response({"account": serialize_account(account), "logoUrl": logo_url})


@accounts_bp.get("/account/network/dashboard")
@require_enterprise_role(network_owner=True)
def get_network_dashboard_endpoint():
    auth = current_auth()
    return success_response(get_network_dashboard(auth.account_id))


@accounts_bp.post("/account/onboarding/preview")
@require_auth({"owner", "admin"})
def preview_onboarding_import():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    upload = request.files.get("file")
    if upload is None:
        raise ApiError("Arquivo CSV ausente", HTTPStatus.BAD_REQUEST)
    preview = parse_onboarding_csv(account=account, content=upload.read())
    return success_response(preview)


@accounts_bp.post("/account/onboarding/commit")
@require_auth({"owner", "admin"})
def commit_onboarding_import_endpoint():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    payload = parse_json(CommitOnboardingInput)
    result = commit_onboarding_import(
        account_id=account.id,
        actor_user_id=auth.user.id,
        rows=[row.model_dump() for row in payload.rows],
    )
    status_code = HTTPStatus.CREATED if result["status"] == "completed" else HTTPStatus.CONFLICT
    return success_response(result, status_code)
