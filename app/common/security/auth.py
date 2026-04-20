from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import wraps
from http import HTTPStatus

from flask import g
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

from app.auth.models import User
from app.common.api import ApiError


@dataclass
class AuthContext:
    user: User
    account_id: uuid.UUID | None


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
            g.auth = AuthContext(user=user, account_id=user.account_id or claims.get("account_id"))
            return fn(*args, **kwargs)

        return wrapped

    return decorator


def current_auth() -> AuthContext:
    context = getattr(g, "auth", None)
    if context is None:
        raise ApiError("Contexto de autenticação ausente", HTTPStatus.UNAUTHORIZED)
    return context
