from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

import requests
from flask_jwt_extended import create_access_token
from werkzeug.security import check_password_hash, generate_password_hash

from app.accounts.models import Account, ProfessionalProfile
from app.auth.core_auth_service import core_auth_service
from app.auth.models import ProfessionalPasswordResetChallenge, User
from app.common.api import ApiError
from app.common.utils.time import ensure_aware
from app.common.utils.text import slugify
from app.extensions import db
from app.integrations.core_email import core_email_gateway
from app.jobs.services import create_audit_log
from app.orgs.services import ensure_owner_membership, list_user_organizations


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


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
    ensure_owner_membership(user)
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
    user = User.query.filter_by(email=email, deleted_at=None).first()
    if user is None:
        raise ApiError("Credenciais inválidas", HTTPStatus.UNAUTHORIZED)
    if not check_password_hash(user.password_hash, password):
        raise ApiError("Credenciais inválidas", HTTPStatus.UNAUTHORIZED)
    if not user.is_active:
        raise ApiError("Usuário inativo", HTTPStatus.FORBIDDEN)
    core_payload = None
    if core_auth_service.is_enabled():
        core_payload = _authenticate_with_core(user=user, password=password)
    user.last_login_at = utcnow()
    if core_payload:
        user.external_user_id = _extract_external_user_id(core_payload)
        user.core_access_token = core_payload.get("access")
        user.core_refresh_token = core_payload.get("refresh_token") or core_payload.get("refresh")
        external_org_id = _extract_external_org_id(core_payload)
        if user.account and external_org_id:
            user.account.external_org_id = external_org_id
    ensure_owner_membership(user)
    db.session.commit()
    return user, issue_token(user)


def request_professional_password_reset(*, email: str, requested_by_ip: str | None = None) -> dict:
    user = User.query.filter(User.email == email, User.deleted_at.is_(None), User.role.in_(["owner", "professional", "admin"])).first()
    if user is None:
        return {"status": "accepted", "expiresInSeconds": 600}

    code = _generate_code()
    challenge = ProfessionalPasswordResetChallenge(
        user_id=user.id,
        email=email,
        otp_code_hash=_hash_code(code),
        delivery_status="pending",
        expires_at=utcnow() + timedelta(minutes=10),
        requested_by_ip=requested_by_ip,
    )
    db.session.add(challenge)
    db.session.flush()

    if user.core_access_token:
        try:
            core_email_gateway.send_html_email(
                access_token=user.core_access_token,
                to_email=email,
                subject="Código para redefinir sua senha FitCopilot",
                html_content=_build_professional_reset_html(user.full_name, code),
            )
            challenge.delivery_status = "sent"
        except Exception:
            challenge.delivery_status = "failed"
    else:
        challenge.delivery_status = "debug"
    db.session.commit()
    is_dev = os.getenv("FLASK_ENV", "development").lower() != "production"
    return {"status": "accepted", "expiresInSeconds": 600, "debugCode": code if is_dev and challenge.delivery_status != "sent" else None}


def verify_professional_password_reset(*, email: str, code: str, new_password: str) -> dict:
    user = User.query.filter(User.email == email, User.deleted_at.is_(None), User.role.in_(["owner", "professional", "admin"])).first()
    if user is None:
        raise ApiError("Código inválido ou expirado", HTTPStatus.UNAUTHORIZED)

    challenge = (
        ProfessionalPasswordResetChallenge.query.filter_by(email=email, consumed_at=None)
        .order_by(ProfessionalPasswordResetChallenge.created_at.desc())
        .first()
    )
    if challenge is None or ensure_aware(challenge.expires_at) < utcnow() or challenge.otp_code_hash != _hash_code(code):
        raise ApiError("Código inválido ou expirado", HTTPStatus.UNAUTHORIZED)

    user.password_hash = generate_password_hash(new_password)
    challenge.verified_at = utcnow()
    challenge.consumed_at = utcnow()
    create_audit_log(
        account_id=user.account_id,
        actor_user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        action="password_reset",
        new_values={"email": user.email},
    )
    db.session.commit()
    return {"status": "password_updated"}


def issue_token(user: User) -> str:
    return create_access_token(identity=str(user.id), additional_claims={"account_id": str(user.account_id) if user.account_id else None})


def build_auth_payload(user: User, token: str | None = None) -> dict:
    professional = user.professional_profile
    account = user.account
    try:
        organizations = list_user_organizations(user)
    except Exception:
        organizations = []

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
        "organizations": organizations,
    }


def _build_professional_reset_html(full_name: str, code: str) -> str:
    first_name = (full_name or "profissional").split()[0]
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;background:#0f172a;color:#e2e8f0;padding:24px;border-radius:16px;">
      <h2 style="margin:0 0 8px 0;">Redefinição de senha FitCopilot</h2>
      <p style="margin:0 0 18px 0;">Olá {first_name}, use o código abaixo para criar uma nova senha.</p>
      <div style="font-size:32px;letter-spacing:8px;font-weight:700;background:#111827;border-radius:12px;padding:16px;text-align:center;">{code}</div>
      <p style="margin-top:18px;font-size:12px;color:#94a3b8;">Esse código expira em 10 minutos. Se você não pediu isso, ignore este e-mail.</p>
    </div>
    """


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
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
        org = first.get("organization") if isinstance(first, dict) else None
        if org and org.get("id"):
            return str(org["id"])
    return None


def _authenticate_with_core(*, user: User, password: str) -> dict | None:
    try:
        return core_auth_service.login(email=user.email, password=password)
    except requests.HTTPError as login_error:
        response = login_error.response
        status_code = response.status_code if response is not None else None
        if status_code == HTTPStatus.BAD_REQUEST:
            return None
        if status_code not in {HTTPStatus.FORBIDDEN, HTTPStatus.UNAUTHORIZED}:
            raise

    try:
        return core_auth_service.register(
            full_name=user.full_name,
            email=user.email,
            password=password,
            phone=user.phone,
        )
    except requests.HTTPError as signup_error:
        response = signup_error.response
        status_code = response.status_code if response is not None else None
        if status_code == HTTPStatus.BAD_REQUEST:
            return None
        if status_code in {HTTPStatus.BAD_REQUEST, HTTPStatus.CONFLICT}:
            raise ApiError("Nao foi possivel abrir sessao no Core para essa conta.", HTTPStatus.BAD_GATEWAY) from signup_error
        raise
