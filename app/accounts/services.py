from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus

from flask import current_app

from app.accounts.models import Account
from app.common.api import ApiError
from app.extensions import db
from app.jobs.services import create_audit_log

ALLOWED_LOGO_MIME_TYPES = {"image/png", "image/jpeg", "image/svg+xml"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def update_account_vertical(*, account: Account, actor_user_id, professional_vertical: str) -> Account:
    previous = account.professional_vertical
    account.professional_vertical = professional_vertical
    if previous != professional_vertical:
        create_audit_log(
            account_id=account.id,
            actor_user_id=actor_user_id,
            entity_type="account",
            entity_id=account.id,
            action="professional_vertical_updated",
            old_values={"professional_vertical": previous},
            new_values={"professional_vertical": professional_vertical},
        )
    db.session.commit()
    return account


def update_account_branding(*, account: Account, actor_user_id, data) -> Account:
    field_map = {
        "logo_url": "logoUrl",
        "primary_color": "primaryColor",
        "secondary_color": "secondaryColor",
        "bot_name": "botName",
        "email_from_name": "emailFromName",
    }
    updated = dict(account.brand_config or {})
    for field, camel_key in field_map.items():
        value = getattr(data, field)
        if value is not None:
            updated[camel_key] = value
    account.brand_config = updated
    create_audit_log(
        account_id=account.id,
        actor_user_id=actor_user_id,
        entity_type="account",
        entity_id=account.id,
        action="brand_config_updated",
        new_values=updated,
    )
    db.session.commit()
    return account


def update_enterprise_contract(*, account: Account, actor_user_id, data) -> Account:
    field_map = {
        "sla_tier": "slaTier",
        "contract_start_at": "contractStartAt",
        "contract_end_at": "contractEndAt",
        "support_channel": "supportChannel",
        "account_manager_name": "accountManagerName",
    }
    updated = dict(account.enterprise_contract_json or {})
    for field, camel_key in field_map.items():
        value = getattr(data, field)
        if value is not None:
            updated[camel_key] = value
    account.enterprise_contract_json = updated
    create_audit_log(
        account_id=account.id,
        actor_user_id=actor_user_id,
        entity_type="account",
        entity_id=account.id,
        action="enterprise_contract_updated",
        new_values=updated,
    )
    db.session.commit()
    return account


def save_account_logo(*, account: Account, actor_user_id, content: bytes, mime_type: str) -> str:
    if mime_type not in ALLOWED_LOGO_MIME_TYPES:
        raise ApiError("Tipo de arquivo não permitido para logo", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    if not content:
        raise ApiError("Arquivo vazio", HTTPStatus.BAD_REQUEST)
    if len(content) > current_app.config["MAX_CONTENT_LENGTH"]:
        raise ApiError("Arquivo excede o tamanho máximo permitido", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    storage = current_app.extensions["storage_provider"]
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg"}[mime_type]
    filename = f"logo-{utcnow().strftime('%Y%m%d%H%M%S')}.{extension}"
    stored = storage.save(f"accounts/{account.id}/branding", filename, content, mime_type)

    updated = dict(account.brand_config or {})
    updated["logoUrl"] = stored.file_url
    account.brand_config = updated
    create_audit_log(
        account_id=account.id,
        actor_user_id=actor_user_id,
        entity_type="account",
        entity_id=account.id,
        action="logo_uploaded",
        new_values={"logoUrl": stored.file_url},
    )
    db.session.commit()
    return stored.file_url


def serialize_account(account: Account) -> dict:
    from app.accounts.enterprise_services import resolve_effective_config

    return {
        "id": str(account.id),
        "name": account.name,
        "slug": account.slug,
        "timezone": account.timezone,
        "planCode": account.current_plan_code,
        "professionalVertical": account.professional_vertical,
        "accountType": account.account_type,
        "parentAccountId": str(account.parent_account_id) if account.parent_account_id else None,
        "brandConfig": resolve_effective_config(account, "brand_config"),
    }
