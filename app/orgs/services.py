from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from http import HTTPStatus
from typing import Any

import requests
from flask import current_app

from app.accounts.models import Account, AccountMembership
from app.auth.models import User
from app.common.api import ApiError
from app.common.utils.time import ensure_aware
from app.common.utils.text import slugify
from app.extensions import db
from app.integrations.core_email import core_email_gateway
from app.integrations.core_client import core_client


FITCOPILOT_ROLES = {"OWNER", "ADMIN", "TRAINER", "NUTRITIONIST", "STAFF", "VIEWER"}
CORE_ROLE_BY_LOCAL = {
    "OWNER": "OWNER",
    "ADMIN": "ADMIN",
    "TRAINER": "LAWYER",
    "NUTRITIONIST": "PARALEGAL",
    "STAFF": "PARALEGAL",
    "VIEWER": "VIEWER",
}
LOCAL_ROLE_BY_CORE = {
    "OWNER": "OWNER",
    "ADMIN": "ADMIN",
    "LAWYER": "TRAINER",
    "PARALEGAL": "STAFF",
    "FINANCE": "STAFF",
    "VIEWER": "VIEWER",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def core_orgs_enabled() -> bool:
    return bool(current_app.config.get("CORE_API_URL"))


def normalize_org_membership(row: dict[str, Any]) -> dict[str, Any]:
    org = row.get("organization") if isinstance(row.get("organization"), dict) else row
    return {
        "id": str(org.get("id") or row.get("id")),
        "name": org.get("name") or row.get("name") or "Workspace",
        "slug": org.get("slug") or row.get("slug") or "workspace",
        "memberId": row.get("member_id") or row.get("memberId"),
        "role": LOCAL_ROLE_BY_CORE.get(str(row.get("role") or "").upper(), str(row.get("role") or "TRAINER").upper()),
        "status": str(row.get("status") or "ACTIVE").upper(),
        "canManageBilling": bool(row.get("can_manage_plans") or row.get("canManageBilling") or False),
        "permissions": row.get("feature_permissions_json") or row.get("permissions") or {},
    }


def ensure_owner_membership(user: User) -> None:
    if not user.account_id:
        return
    account = Account.query.filter_by(id=user.account_id, deleted_at=None).first()
    if not account:
        return
    membership = AccountMembership.query.filter_by(account_id=account.id, user_id=user.id, deleted_at=None).first()
    if membership:
        return
    db.session.add(
        AccountMembership(
            account_id=account.id,
            user_id=user.id,
            external_org_id=account.external_org_id,
            role="OWNER" if user.role == "owner" else "TRAINER",
            status="ACTIVE",
            joined_at=utcnow(),
            can_manage_billing=user.role in {"owner", "admin"},
            permissions_json={},
        )
    )
    db.session.flush()


def account_for_org_id(org_id: str) -> Account | None:
    query = Account.query.filter(Account.deleted_at.is_(None))
    account = query.filter(Account.external_org_id == str(org_id)).first()
    if account:
        return account
    try:
        parsed = uuid.UUID(str(org_id))
    except (TypeError, ValueError):
        return None
    return query.filter(Account.id == parsed).first()


def ensure_local_account_for_org(org: dict[str, Any]) -> Account:
    org_id = str(org["id"])
    account = Account.query.filter_by(external_org_id=org_id, deleted_at=None).first()
    if account:
        account.name = org.get("name") or account.name
        account.slug = org.get("slug") or account.slug
        return account

    base_slug = slugify(org.get("slug") or org.get("name") or "workspace")
    slug = base_slug
    idx = 1
    while Account.query.filter_by(slug=slug).first():
        idx += 1
        slug = f"{base_slug}-{idx}"

    account = Account(
        name=org.get("name") or "Workspace",
        slug=slug,
        email=f"{slug}@workspace.fitcopilot.local",
        phone=None,
        settings_json={"workspace_theme": "fitcopilot", "student_panel_mode": "assistant"},
        external_org_id=org_id,
    )
    db.session.add(account)
    db.session.flush()
    return account


def sync_core_membership(user: User, row: dict[str, Any]) -> AccountMembership:
    item = normalize_org_membership(row)
    account = ensure_local_account_for_org(item)
    membership = AccountMembership.query.filter_by(account_id=account.id, user_id=user.id, deleted_at=None).first()
    if not membership:
        membership = AccountMembership(account_id=account.id, user_id=user.id)
        db.session.add(membership)
    membership.external_org_id = item["id"]
    membership.external_member_id = item.get("memberId")
    membership.role = item["role"]
    membership.status = item["status"]
    membership.can_manage_billing = bool(item.get("canManageBilling"))
    membership.permissions_json = item.get("permissions") or {}
    membership.joined_at = membership.joined_at or utcnow()
    db.session.flush()
    return membership


def membership_to_payload(membership: AccountMembership) -> dict[str, Any]:
    account = Account.query.filter_by(id=membership.account_id).first()
    return {
        "id": str(account.external_org_id or account.id),
        "accountId": str(account.id),
        "name": account.name,
        "slug": account.slug,
        "memberId": str(membership.external_member_id or membership.id),
        "role": membership.role,
        "status": membership.status,
        "canManageBilling": bool(membership.can_manage_billing),
        "permissions": membership.permissions_json or {},
    }


def list_user_organizations(user: User) -> list[dict[str, Any]]:
    ensure_owner_membership(user)
    rows: list[dict[str, Any]] = []

    if core_orgs_enabled() and user.core_access_token:
        try:
            core_rows = core_client.request(method="GET", path="/orgs/mine/", token=user.core_access_token)
            for row in core_rows if isinstance(core_rows, list) else []:
                membership = sync_core_membership(user, row)
                rows.append(membership_to_payload(membership))
            db.session.commit()
            return rows
        except requests.HTTPError:
            db.session.rollback()
            raise
        except Exception:
            db.session.rollback()

    local_memberships = (
        AccountMembership.query.filter_by(user_id=user.id, status="ACTIVE", deleted_at=None)
        .order_by(AccountMembership.created_at.asc())
        .all()
    )
    return [membership_to_payload(item) for item in local_memberships]


def create_organization(user: User, name: str) -> dict[str, Any]:
    if core_orgs_enabled() and user.core_access_token:
        row = core_client.request(method="POST", path="/orgs/", token=user.core_access_token, json={"name": name})
        membership = sync_core_membership(user, {"organization": row, "role": "OWNER", "status": "ACTIVE", "can_manage_plans": True})
        db.session.commit()
        return membership_to_payload(membership)

    base_slug = slugify(name)
    account = ensure_local_account_for_org({"id": f"local-org-{base_slug}-{secrets.token_hex(4)}", "name": name, "slug": base_slug})
    membership = AccountMembership(
        account_id=account.id,
        user_id=user.id,
        external_org_id=account.external_org_id,
        role="OWNER",
        status="ACTIVE",
        joined_at=utcnow(),
        can_manage_billing=True,
        permissions_json={},
    )
    db.session.add(membership)
    db.session.commit()
    return membership_to_payload(membership)


def resolve_invite(token: str) -> dict[str, Any]:
    local = AccountMembership.query.filter_by(invite_token=token, deleted_at=None).first()
    if local:
        account = Account.query.filter_by(id=local.account_id).first()
        return {
            "token": token,
            "organizationId": str(account.external_org_id or account.id),
            "organizationName": account.name,
            "invitedEmail": local.invited_email,
            "role": local.role,
            "expiresAt": local.invite_expires_at.isoformat() if local.invite_expires_at else None,
            "acceptedAt": local.joined_at.isoformat() if local.status == "ACTIVE" and local.user_id else None,
            "hasAccount": bool(User.query.filter_by(email=(local.invited_email or "").lower(), deleted_at=None).first()),
            "isExpired": bool(local.invite_expires_at and ensure_aware(local.invite_expires_at) <= utcnow()),
        }
    if core_orgs_enabled():
        row = core_client.request(method="GET", path=f"/orgs/invites/resolve/?token={token}")
        return {
            "token": row.get("token") or token,
            "organizationId": row.get("organization_id"),
            "organizationName": row.get("organization_name"),
            "invitedEmail": row.get("invited_email"),
            "inviterEmail": row.get("inviter_email"),
            "inviterName": row.get("inviter_name"),
            "role": LOCAL_ROLE_BY_CORE.get(str(row.get("role") or "").upper(), row.get("role")),
            "expiresAt": row.get("expires_at"),
            "acceptedAt": row.get("accepted_at"),
            "hasAccount": bool(row.get("has_account")),
            "isExpired": bool(row.get("is_expired")),
        }
    raise ApiError("Convite nao encontrado", HTTPStatus.NOT_FOUND)


def invite_member(user: User, org_id: str, email: str, role: str) -> dict[str, Any]:
    role = role.upper()
    if role not in FITCOPILOT_ROLES:
        raise ApiError("Perfil invalido para convite", HTTPStatus.BAD_REQUEST)
    account = account_for_org_id(org_id)
    if not account:
        raise ApiError("Workspace nao encontrado", HTTPStatus.NOT_FOUND)
    _require_local_manager(user, account)

    if core_orgs_enabled() and user.core_access_token and account.external_org_id:
        row = core_client.request(
            method="POST",
            path=f"/orgs/{account.external_org_id}/invite/",
            token=user.core_access_token,
            json={"email": email, "role": CORE_ROLE_BY_LOCAL.get(role, "LAWYER")},
        )
        normalized_email = str(row.get("email") or email).strip().lower()
        invite = AccountMembership.query.filter_by(
            account_id=account.id,
            invited_email=normalized_email,
            status="INVITED",
            deleted_at=None,
        ).first()
        if not invite:
            invite = AccountMembership(account_id=account.id)
            db.session.add(invite)
        invited_user = User.query.filter_by(email=normalized_email, deleted_at=None).first()
        invite.user_id = invited_user.id if invited_user else None
        invite.external_org_id = account.external_org_id
        invite.external_member_id = str(row.get("id") or "") or invite.external_member_id
        invite.role = role
        invite.status = "INVITED"
        invite.invited_email = normalized_email
        invite.invited_by_user_id = user.id
        invite.invite_token = str(row.get("token") or "") or invite.invite_token
        expires_at = row.get("expires_at")
        if expires_at:
            try:
                invite.invite_expires_at = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            except ValueError:
                invite.invite_expires_at = utcnow() + timedelta(days=7)
        else:
            invite.invite_expires_at = utcnow() + timedelta(days=7)
        invite.permissions_json = invite.permissions_json or {}
        db.session.commit()
        return {
            "id": row.get("id"),
            "email": normalized_email,
            "role": role,
            "token": row.get("token"),
            "expiresAt": row.get("expires_at"),
            "acceptedAt": row.get("accepted_at"),
            "acceptUrl": f"/accept-invite?token={row.get('token')}",
        }

    normalized_email = email.strip().lower()
    invited_user = User.query.filter_by(email=normalized_email, deleted_at=None).first()
    existing = None
    if invited_user:
        existing = AccountMembership.query.filter_by(account_id=account.id, user_id=invited_user.id, deleted_at=None).first()
    if existing and existing.status == "ACTIVE":
        raise ApiError("Este usuario ja faz parte do workspace", HTTPStatus.CONFLICT)

    invite = existing or AccountMembership.query.filter_by(account_id=account.id, invited_email=normalized_email, status="INVITED", deleted_at=None).first()
    if not invite:
        invite = AccountMembership(account_id=account.id)
        db.session.add(invite)
    invite.user_id = invited_user.id if invited_user else None
    invite.external_org_id = account.external_org_id
    invite.role = role
    invite.status = "INVITED"
    invite.invited_email = normalized_email
    invite.invited_by_user_id = user.id
    invite.invite_token = invite.invite_token or secrets.token_urlsafe(32)
    invite.invite_expires_at = utcnow() + timedelta(days=7)
    invite.permissions_json = invite.permissions_json or {}
    db.session.commit()
    email_delivery = _send_local_invite_email(
        invited_by_user=user,
        account=account,
        invite=invite,
    )
    return {
        "id": str(invite.id),
        "email": invite.invited_email,
        "role": invite.role,
        "token": invite.invite_token,
        "expiresAt": invite.invite_expires_at.isoformat(),
        "acceptUrl": f"/accept-invite?token={invite.invite_token}",
        "emailDeliveryStatus": email_delivery["status"],
        "emailDeliveryDetail": email_delivery.get("detail"),
    }


def list_members(user: User, org_id: str) -> list[dict[str, Any]]:
    account = account_for_org_id(org_id)
    if not account:
        raise ApiError("Workspace nao encontrado", HTTPStatus.NOT_FOUND)
    _require_local_manager(user, account, allow_read=True)
    rows = AccountMembership.query.filter_by(account_id=account.id, deleted_at=None).order_by(AccountMembership.created_at.asc()).all()
    return [_member_payload(row) for row in rows]


def update_member(user: User, org_id: str, member_id: str, data: dict[str, Any]) -> dict[str, Any]:
    account = account_for_org_id(org_id)
    if not account:
        raise ApiError("Workspace nao encontrado", HTTPStatus.NOT_FOUND)
    _require_local_manager(user, account)
    member = AccountMembership.query.filter_by(id=member_id, account_id=account.id, deleted_at=None).first()
    if not member:
        raise ApiError("Membro nao encontrado", HTTPStatus.NOT_FOUND)
    if data.get("role"):
        role = str(data["role"]).upper()
        if role not in FITCOPILOT_ROLES:
            raise ApiError("Perfil invalido", HTTPStatus.BAD_REQUEST)
        member.role = role
    if data.get("status"):
        member.status = str(data["status"]).upper()
    if "canManageBilling" in data:
        member.can_manage_billing = bool(data["canManageBilling"])
    if isinstance(data.get("permissions"), dict):
        member.permissions_json = data["permissions"]
    db.session.commit()
    return _member_payload(member)


def remove_member(user: User, org_id: str, member_id: str) -> None:
    account = account_for_org_id(org_id)
    if not account:
        raise ApiError("Workspace nao encontrado", HTTPStatus.NOT_FOUND)
    _require_local_manager(user, account)
    member = AccountMembership.query.filter_by(id=member_id, account_id=account.id, deleted_at=None).first()
    if not member:
        raise ApiError("Membro nao encontrado", HTTPStatus.NOT_FOUND)
    if member.user_id == user.id:
        raise ApiError("Voce nao pode remover a si mesmo por aqui", HTTPStatus.BAD_REQUEST)
    member.deleted_at = utcnow()
    member.status = "REMOVED"
    db.session.commit()


def accept_invite(user: User, token: str) -> dict[str, Any]:
    local = AccountMembership.query.filter_by(invite_token=token, deleted_at=None).first()
    if local:
        if local.invite_expires_at and ensure_aware(local.invite_expires_at) <= utcnow():
            raise ApiError("Convite expirado", HTTPStatus.BAD_REQUEST)
        if (local.invited_email or "").lower() != user.email.lower():
            raise ApiError("Este convite pertence a outro email", HTTPStatus.FORBIDDEN)
        core_row: dict[str, Any] | None = None
        if core_orgs_enabled() and user.core_access_token and local.external_org_id:
            core_row = core_client.request(
                method="POST",
                path="/orgs/invites/accept/",
                token=user.core_access_token,
                json={"token": token},
            )
        existing = AccountMembership.query.filter_by(account_id=local.account_id, user_id=user.id, deleted_at=None).first()
        target = existing or local
        target.user_id = user.id
        target.status = "ACTIVE"
        target.joined_at = utcnow()
        target.invite_token = None
        target.external_org_id = target.external_org_id or Account.query.get(target.account_id).external_org_id
        if user.account_id is None:
            user.account_id = target.account_id
        db.session.commit()
        return {
            "detail": (core_row or {}).get("detail", "Convite aceito") if core_row else "Convite aceito",
            "organizationId": str((core_row or {}).get("organization_id") or target.external_org_id or target.account_id),
            "organizationName": (core_row or {}).get("organization_name"),
        }

    if core_orgs_enabled() and user.core_access_token:
        row = core_client.request(method="POST", path="/orgs/invites/accept/", token=user.core_access_token, json={"token": token})
        org_id = row.get("organization_id")
        if org_id:
            memberships = core_client.request(method="GET", path="/orgs/mine/", token=user.core_access_token)
            for item in memberships if isinstance(memberships, list) else []:
                normalized = normalize_org_membership(item)
                if normalized["id"] == str(org_id):
                    sync_core_membership(user, item)
                    break
            db.session.commit()
        return {"detail": row.get("detail", "Convite aceito"), "organizationId": org_id, "organizationName": row.get("organization_name")}
    raise ApiError("Convite nao encontrado", HTTPStatus.NOT_FOUND)


def _member_payload(member: AccountMembership) -> dict[str, Any]:
    user = User.query.filter_by(id=member.user_id).first() if member.user_id else None
    return {
        "id": str(member.id),
        "userId": str(member.user_id) if member.user_id else None,
        "name": user.full_name if user else None,
        "email": user.email if user else member.invited_email,
        "role": member.role,
        "status": member.status,
        "canManageBilling": bool(member.can_manage_billing),
        "permissions": member.permissions_json or {},
        "invitedEmail": member.invited_email,
        "joinedAt": member.joined_at.isoformat() if member.joined_at else None,
        "createdAt": member.created_at.isoformat() if member.created_at else None,
    }


def _send_local_invite_email(*, invited_by_user: User, account: Account, invite: AccountMembership) -> dict[str, str | None]:
    if not current_app.config.get("CORE_API_URL"):
        return {"status": "not_configured", "detail": "CORE_API_URL nao configurado"}
    if not invited_by_user.core_access_token:
        return {"status": "missing_core_session", "detail": "Usuario sem sessao ativa no Core"}
    if not invite.invited_email or not invite.invite_token:
        return {"status": "skipped", "detail": "Convite sem email ou token"}

    accept_url = _invite_accept_url(invite.invite_token)
    role_label = _role_label(invite.role)
    try:
        core_email_gateway.send_html_email(
            access_token=invited_by_user.core_access_token,
            to_email=invite.invited_email,
            subject=f"Convite para a academia {account.name} no FitCopilot",
            html_content=_build_invite_email_html(
                invited_by_name=invited_by_user.full_name or invited_by_user.email,
                organization_name=account.name,
                role_label=role_label,
                accept_url=accept_url,
                expires_at=invite.invite_expires_at,
            ),
        )
    except Exception as exc:
        current_app.logger.warning(
            "org_invite_email_failed account_id=%s invite_id=%s email=%s error=%s",
            account.id,
            invite.id,
            invite.invited_email,
            str(exc),
        )
        return {"status": "failed", "detail": str(exc)}
    return {"status": "sent", "detail": None}


def _invite_accept_url(token: str) -> str:
    frontend_base = str(current_app.config.get("FRONTEND_URL") or "http://127.0.0.1:3000").rstrip("/")
    return f"{frontend_base}/accept-invite?token={token}"


def _role_label(role: str | None) -> str:
    labels = {
        "OWNER": "Dono",
        "ADMIN": "Gestor",
        "TRAINER": "Personal",
        "NUTRITIONIST": "Nutricionista",
        "STAFF": "Funcionario",
        "VIEWER": "Somente leitura",
    }
    return labels.get(str(role or "").upper(), role or "Membro")


def _build_invite_email_html(
    *,
    invited_by_name: str,
    organization_name: str,
    role_label: str,
    accept_url: str,
    expires_at: datetime | None,
) -> str:
    expires_text = expires_at.strftime("%d/%m/%Y %H:%M") if expires_at else "7 dias"
    safe_inviter = escape(invited_by_name)
    safe_org = escape(organization_name)
    safe_role = escape(role_label)
    safe_url = escape(accept_url, quote=True)
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;background:#faf9f7;color:#1f1a17;padding:28px;border-radius:18px;border:1px solid #eadfd8;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:22px;">
        <div style="width:34px;height:34px;border-radius:10px;background:#a63a22;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;">FC</div>
        <strong style="font-size:20px;">FitCopilot</strong>
      </div>
      <h1 style="margin:0 0 12px 0;font-size:26px;line-height:1.2;">Voce foi convidado para uma academia</h1>
      <p style="margin:0 0 16px 0;color:#5f5651;font-size:15px;line-height:1.6;">
        {safe_inviter} convidou voce para participar do workspace <strong>{safe_org}</strong> no FitCopilot.
      </p>
      <div style="background:#fff;border:1px solid #eadfd8;border-radius:14px;padding:16px;margin:18px 0;">
        <p style="margin:0 0 8px 0;"><strong>Perfil:</strong> {safe_role}</p>
        <p style="margin:0;"><strong>Expira em:</strong> {escape(expires_text)}</p>
      </div>
      <a href="{safe_url}" target="_blank" style="display:inline-block;background:#1f1a17;color:#fff;text-decoration:none;padding:13px 18px;border-radius:10px;font-weight:700;">
        Aceitar convite
      </a>
      <p style="margin:18px 0 0 0;color:#7a706a;font-size:12px;line-height:1.5;">
        Se o botao nao funcionar, copie e cole este link no navegador:<br />
        <a href="{safe_url}" target="_blank" style="color:#a63a22;">{safe_url}</a>
      </p>
    </div>
    """


def _require_local_manager(user: User, account: Account, *, allow_read: bool = False) -> AccountMembership:
    membership = AccountMembership.query.filter_by(account_id=account.id, user_id=user.id, status="ACTIVE", deleted_at=None).first()
    if not membership:
        raise ApiError("Voce nao faz parte deste workspace", HTTPStatus.FORBIDDEN)
    if membership.role in {"OWNER", "ADMIN"}:
        return membership
    if allow_read and membership.role in {"TRAINER", "NUTRITIONIST", "STAFF", "VIEWER"}:
        return membership
    raise ApiError("Sem permissao para gerenciar membros", HTTPStatus.FORBIDDEN)
