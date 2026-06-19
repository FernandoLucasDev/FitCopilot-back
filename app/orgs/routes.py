from __future__ import annotations

from flask import Blueprint, request

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.orgs.services import (
    accept_invite,
    create_organization,
    invite_member,
    list_members,
    list_user_organizations,
    remove_member,
    resolve_invite,
    update_member,
)


orgs_bp = Blueprint("orgs", __name__)


@orgs_bp.get("/orgs/mine")
@require_auth()
def mine():
    auth = current_auth()
    return success_response({"items": list_user_organizations(auth.user)})


@orgs_bp.post("/orgs")
@require_auth({"owner", "professional", "admin"})
def create():
    auth = current_auth()
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        from app.common.api import ApiError
        from http import HTTPStatus

        raise ApiError("Nome do workspace e obrigatorio", HTTPStatus.BAD_REQUEST)
    return success_response({"organization": create_organization(auth.user, name)}, 201)


@orgs_bp.get("/orgs/invites/resolve")
def resolve():
    token = str(request.args.get("token") or "").strip()
    if not token:
        from app.common.api import ApiError
        from http import HTTPStatus

        raise ApiError("Token do convite e obrigatorio", HTTPStatus.BAD_REQUEST)
    return success_response({"invite": resolve_invite(token)})


@orgs_bp.post("/orgs/invites/accept")
@require_auth()
def accept():
    auth = current_auth()
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token") or "").strip()
    if not token:
        from app.common.api import ApiError
        from http import HTTPStatus

        raise ApiError("Token do convite e obrigatorio", HTTPStatus.BAD_REQUEST)
    return success_response(accept_invite(auth.user, token))


@orgs_bp.get("/orgs/<org_id>/members")
@require_auth({"owner", "professional", "admin"})
def members(org_id: str):
    auth = current_auth()
    return success_response({"items": list_members(auth.user, org_id)})


@orgs_bp.post("/orgs/<org_id>/invite")
@require_auth({"owner", "professional", "admin"})
def invite(org_id: str):
    auth = current_auth()
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email") or "").strip()
    role = str(payload.get("role") or "TRAINER").strip()
    return success_response({"invite": invite_member(auth.user, org_id, email, role)}, 201)


@orgs_bp.patch("/orgs/<org_id>/members/<member_id>")
@require_auth({"owner", "professional", "admin"})
def patch_member(org_id: str, member_id: str):
    auth = current_auth()
    payload = request.get_json(silent=True) or {}
    return success_response({"member": update_member(auth.user, org_id, member_id, payload)})


@orgs_bp.delete("/orgs/<org_id>/members/<member_id>")
@require_auth({"owner", "professional", "admin"})
def delete_member(org_id: str, member_id: str):
    auth = current_auth()
    remove_member(auth.user, org_id, member_id)
    return success_response({"status": "removed"})
