from __future__ import annotations

from flask import Blueprint, current_app, request
from flask_jwt_extended import jwt_required

from app.auth.schemas import LoginInput, PasswordResetRequestInput, PasswordResetVerifyInput, RegisterInput
from app.auth.services import (
    authenticate,
    build_auth_payload,
    register_account_and_owner,
    request_professional_password_reset,
    verify_professional_password_reset,
)
from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth
from app.common.security.rate_limit import check_rate_limit, client_ip


auth_bp = Blueprint("auth", __name__)


@auth_bp.post("/auth/register")
def register():
    data = parse_json(RegisterInput)
    user, token = register_account_and_owner(data)
    return success_response(build_auth_payload(user, token), 201)


@auth_bp.post("/auth/login")
def login():
    data = parse_json(LoginInput)
    user, token = authenticate(data.email, data.password)
    return success_response(build_auth_payload(user, token))


@auth_bp.post("/auth/password-reset/request")
def request_password_reset():
    data = parse_json(PasswordResetRequestInput)
    check_rate_limit(
        key=f"password-reset:{client_ip()}:{data.email.lower()}",
        limit=int(current_app.config.get("PASSWORD_RESET_RATE_LIMIT_PER_HOUR", 5)),
        window_seconds=3600,
    )
    return success_response(request_professional_password_reset(email=data.email, requested_by_ip=request.remote_addr), 202)


@auth_bp.post("/auth/password-reset/verify")
def verify_password_reset():
    data = parse_json(PasswordResetVerifyInput)
    return success_response(verify_professional_password_reset(email=data.email, code=data.code, new_password=data.new_password))


@auth_bp.post("/auth/logout")
@jwt_required()
def logout():
    return success_response({"message": "Logout concluído no cliente."})


@auth_bp.get("/auth/me")
@require_auth()
def me():
    auth = current_auth()
    return success_response(build_auth_payload(auth.user))
