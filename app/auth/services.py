from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
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


def _vertical_from_professional_type(professional_type: str | None) -> str:
    normalized = (professional_type or "").strip().lower()
    if normalized in {"nutritionist", "nutricionista"}:
        return "nutricionista"
    if normalized == "academia":
        return "academia"
    return "personal_trainer"


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
        professional_vertical=_vertical_from_professional_type(data.professional_type),
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
            from app.accounts.enterprise_services import resolve_effective_config

            brand = resolve_effective_config(user.account, "brand_config") if user.account else {}
            brand_name = brand.get("botName") or "FitCopilot"
            core_email_gateway.send_html_email(
                access_token=user.core_access_token,
                to_email=email,
                subject=f"Código para redefinir sua senha {brand_name}",
                html_content=_build_professional_reset_html(
                    user.full_name,
                    code,
                    brand_name=brand_name,
                    primary_color=brand.get("primaryColor") or "#111827",
                ),
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


def _account_summary(account: Account) -> dict:
    from app.accounts.services import serialize_account

    return serialize_account(account)


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
        "account": None if account is None else _account_summary(account),
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


def _build_professional_reset_html(full_name: str, code: str, *, brand_name: str = "FitCopilot", primary_color: str = "#111827") -> str:
    first_name = escape((full_name or "profissional").split()[0])
    safe_brand = escape(brand_name or "FitCopilot")
    safe_code = escape(code)
    safe_color = escape(primary_color or "#a63a22")
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background-color:#faf9f7;color:#1f1a17;padding:28px;border-radius:18px;border:1px solid #eadfd8;">
      <div style="display:block;margin-bottom:24px;">
        <div style="display:inline-block;background-color:{safe_color};color:#ffffff;border-radius:12px;padding:10px 12px;font-weight:700;">FC</div>
        <span style="font-size:18px;font-weight:700;margin-left:10px;">{safe_brand}</span>
      </div>
      <h2 style="margin:0 0 10px 0;font-size:24px;line-height:1.2;color:#1f1a17;">Redefinicao de senha</h2>
      <p style="margin:0 0 18px 0;font-size:15px;line-height:1.6;color:#665f5a;">
        Oi, {first_name}. Use o codigo abaixo para confirmar sua identidade e criar uma nova senha.
      </p>
      <div style="background-color:#ffffff;border:1px solid #eadfd8;border-radius:16px;padding:22px;text-align:center;margin:22px 0;">
        <div style="font-size:12px;font-weight:700;letter-spacing:1.5px;color:{safe_color};text-transform:uppercase;">Codigo de seguranca</div>
        <div style="font-size:36px;letter-spacing:9px;font-weight:700;color:#1f1a17;margin-top:8px;">{safe_code}</div>
      </div>
      <p style="margin:0 0 14px 0;font-size:14px;line-height:1.6;color:#665f5a;">
        Esse codigo expira em 10 minutos. Se voce nao pediu essa alteracao, ignore este e-mail e sua senha continua igual.
      </p>
      <div style="background-color:#f1ebe7;border-radius:14px;padding:14px;margin-top:20px;">
        <p style="margin:0;font-size:12px;line-height:1.5;color:#665f5a;">
          Por seguranca, nunca compartilhe este codigo com outras pessoas.
        </p>
      </div>
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
