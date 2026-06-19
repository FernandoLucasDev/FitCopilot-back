from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import wraps
from http import HTTPStatus

from flask import g, request
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

from app.auth.models import User
from app.common.api import ApiError


@dataclass
class AuthContext:
    user: User
    account_id: uuid.UUID | None
    organization_id: str | None = None
    member_role: str | None = None


def require_auth(roles: set[str] | None = None):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            verify_jwt_in_request()
            identity = get_jwt_identity()
            claims = get_jwt()
            user = User.query.filter_by(id=identity, deleted_at=None).first()
            if user is None or not user.is_active:
                raise ApiError("Usuário inválido", HTTPStatus.UNAUTHORIZED)
            if roles and user.role not in roles:
                raise ApiError("Acesso negado", HTTPStatus.FORBIDDEN)
            account_id = user.account_id or claims.get("account_id")
            organization_id = request.headers.get("X-ORG-ID")
            member_role = None
            if organization_id and user.role != "student":
                from app.accounts.models import AccountMembership
                from app.orgs.services import account_for_org_id, ensure_owner_membership

                ensure_owner_membership(user)
                account = account_for_org_id(organization_id)
                if account is None:
                    raise ApiError("Workspace nao encontrado", HTTPStatus.FORBIDDEN)
                membership = AccountMembership.query.filter_by(
                    account_id=account.id,
                    user_id=user.id,
                    status="ACTIVE",
                    deleted_at=None,
                ).first()
                if membership is None:
                    raise ApiError("Voce nao faz parte deste workspace", HTTPStatus.FORBIDDEN)
                account_id = account.id
                organization_id = str(account.external_org_id or account.id)
                member_role = membership.role
                if roles:
                    if member_role == "VIEWER" and request.method not in {"GET", "HEAD", "OPTIONS"}:
                        raise ApiError("Perfil somente leitura nao pode alterar este workspace", HTTPStatus.FORBIDDEN)
                    if roles <= {"owner", "admin"} and member_role not in {"OWNER", "ADMIN"}:
                        raise ApiError("Apenas gestores do workspace podem acessar esta area", HTTPStatus.FORBIDDEN)

            g.auth = AuthContext(user=user, account_id=account_id, organization_id=organization_id, member_role=member_role)
            return fn(*args, **kwargs)

        return wrapped

    return decorator


def current_auth() -> AuthContext:
    context = getattr(g, "auth", None)
    if context is None:
        raise ApiError("Contexto de autenticação ausente", HTTPStatus.UNAUTHORIZED)
    return context
