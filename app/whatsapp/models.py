from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.extensions import db


class WhatsAppSession(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "whatsapp_sessions"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="whatsapp")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    current_flow: Mapped[str | None] = mapped_column(String(40), index=True)
    current_step: Mapped[str | None] = mapped_column(String(60))
    context_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboundMessageDispatch(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "outbound_message_dispatches"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    related_entity_type: Mapped[str | None] = mapped_column(String(60), index=True)
    related_entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    message_category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    core_message_public_id: Mapped[str | None] = mapped_column(String(120), index=True)
    core_channel_account_id: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    external_reference: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    requested_by_service: Mapped[str] = mapped_column(String(80), nullable=False)
    local_status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    payload_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)


class InboundMessageRecord(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "inbound_message_records"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(120), index=True)
    wa_from_phone: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    text_body: Mapped[str | None] = mapped_column(db.Text())
    media_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    parsed_intent: Mapped[str | None] = mapped_column(String(60), index=True)
    confidence: Mapped[float | None] = mapped_column(db.Float())
    raw_payload_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    processed: Mapped[bool] = mapped_column(nullable=False, default=False)
    processing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WhatsAppAutomationRule(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "whatsapp_automation_rules"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    schedule_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    filters_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    template_config_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)


class WhatsAppDeliveryStatusEvent(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "whatsapp_delivery_status_events"

    outbound_dispatch_id: Mapped[str] = mapped_column(db.ForeignKey("outbound_message_dispatches.id"), nullable=False, index=True)
    core_message_public_id: Mapped[str | None] = mapped_column(String(120), index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    event_payload_json: Mapped[dict] = mapped_column(JSONB().with_variant(db.JSON(), "sqlite"), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
