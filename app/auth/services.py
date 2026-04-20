from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus

from flask_jwt_extended import create_access_token
from werkzeug.security import check_password_hash, generate_password_hash

from app.accounts.models import Account, ProfessionalProfile
from app.auth.core_auth_service import core_auth_service
from app.auth.models import User
from app.common.api import ApiError
from app.common.utils.text import slugify
from app.extensions import db
from app.jobs.services import create_audit_log


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def register_account_and_owner(data) -> tuple[User, str]:
    if User.query.filter_by(email=data.email).first():
        raise ApiError("Já existe usuário com esse email", HTTPStatus.CONFLICT)
    if Account.query.filter_by(email=data.account_email).first():
        raise ApiError("Já existe conta com esse email", HTTPStatus.CONFLICT)

    base_slug = slugify(data.account_name)
    slug = base_slug
    idx = 1
    while Account.query.filter_by(slug=slug).first():
        idx += 1
        slug = f"{base_slug}-{idx}"

    account = Account(
        name=data.account_name,
        slug=slug,
        email=data.account_email,
        phone=data.account_phone,
        settings_json={"workspace_theme": "fitcopilot", "student_panel_mode": "assistant"},
    )
    db.session.add(account)
    db.session.flush()

    core_payload = None
    if core_auth_service.is_enabled():
        core_payload = core_auth_service.register(
            full_name=data.full_name,
            email=data.email,
            password=data.password,
            phone=data.account_phone,
        )

    user = User(
        account_id=account.id,
        role="owner",
        full_name=data.full_name,
        email=data.email,
        password_hash=generate_password_hash(data.password),
        is_active=True,
        is_email_verified=False,
        external_user_id=_extract_external_user_id(core_payload),
        core_access_token=(core_payload or {}).get("access"),
        core_refresh_token=(core_payload or {}).get("refresh_token") or (core_payload or {}).get("refresh"),
    )
    db.session.add(user)
    db.session.flush()

    professional = ProfessionalProfile(
        user_id=user.id,
        account_id=account.id,
        professional_type=data.professional_type,
        onboarding_completed=True,
    )
    db.session.add(professional)
    account.external_org_id = _extract_external_org_id(core_payload)
    create_audit_log(
        account_id=account.id,
        actor_user_id=user.id,
        entity_type="account",
        entity_id=account.id,
        action="created",
        new_values={"name": account.name, "slug": account.slug},
    )
    db.session.commit()
    return user, issue_token(user)


def authenticate(email: str, password: str) -> tuple[User, str]:
    core_payload = None
    if core_auth_service.is_enabled():
        core_payload = core_auth_service.login(email=email, password=password)

    user = User.query.filter_by(email=email, deleted_at=None).first()
    if user is None:
        raise ApiError("Credenciais inválidas", HTTPStatus.UNAUTHORIZED)
    if not core_payload and not check_password_hash(user.password_hash, password):
        raise ApiError("Credenciais inválidas", HTTPStatus.UNAUTHORIZED)
    if not user.is_active:
        raise ApiError("Usuário inativo", HTTPStatus.FORBIDDEN)
    user.last_login_at = utcnow()
    if core_payload:
        user.external_user_id = _extract_external_user_id(core_payload)
        user.core_access_token = core_payload.get("access")
        user.core_refresh_token = core_payload.get("refresh_token") or core_payload.get("refresh")
        external_org_id = _extract_external_org_id(core_payload)
        if user.account and external_org_id:
            user.account.external_org_id = external_org_id
    db.session.commit()
    return user, issue_token(user)


def issue_token(user: User) -> str:
    return create_access_token(identity=str(user.id), additional_claims={"account_id": str(user.account_id) if user.account_id else None})


def build_auth_payload(user: User, token: str | None = None) -> dict:
    professional = user.professional_profile
    account = user.account
    return {
        "token": token,
        "user": {
            "id": str(user.id),
            "fullName": user.full_name,
            "email": user.email,
            "role": user.role,
            "avatarUrl": user.avatar_url,
        },
        "account": None
        if account is None
        else {
            "id": str(account.id),
            "name": account.name,
            "slug": account.slug,
            "timezone": account.timezone,
            "planCode": account.current_plan_code,
        },
        "professionalProfile": None
        if professional is None
        else {
            "id": str(professional.id),
            "professionalType": professional.professional_type,
            "onboardingCompleted": professional.onboarding_completed,
        },
        "core": {
            "externalUserId": user.external_user_id,
            "hasCoreSession": bool(user.core_access_token),
            "externalOrgId": account.external_org_id if account else None,
        },
    }


def _extract_external_user_id(payload: dict | None) -> int | None:
    if not payload:
        return None
    for key in ("user_id", "external_user_id", "id"):
        value = payload.get(key)
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    user_obj = payload.get("user") or {}
    for key in ("id", "user_id", "external_user_id"):
        value = user_obj.get(key)
        if value is not None:
            try:
                return int(value)
            except Exception:
                continue
    return None


def _extract_external_org_id(payload: dict | None) -> str | None:
    if not payload:
        return None
    for key in ("org_id", "organization_id"):
        value = payload.get(key)
        if value:
            return str(value)
    account = payload.get("account") or {}
    if account.get("org_id"):
        return str(account["org_id"])
    organizations = payload.get("organizations") or []
    if organizations:
        first = organizations[0]
        org = first.get("organization") if isinstance(first, dict) else None
        if org and org.get("id"):
            return str(org["id"])
    return None
