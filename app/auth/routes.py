from __future__ import annotations

from flask import Blueprint
from flask_jwt_extended import jwt_required

from app.auth.schemas import LoginInput, RegisterInput
from app.auth.services import authenticate, build_auth_payload, register_account_and_owner
from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth


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


@auth_bp.post("/auth/logout")
@jwt_required()
def logout():
    return success_response({"message": "Logout concluído no cliente."})


@auth_bp.get("/auth/me")
@require_auth()
def me():
    auth = current_auth()
    return success_response(build_auth_payload(auth.user))
