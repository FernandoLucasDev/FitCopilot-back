from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db.mixins import UUIDPrimaryKeyMixin
from app.extensions import db


# ---------------------------------------------------------------------------
# Tipos canônicos de evento — usados como constants em todo o sistema
# ---------------------------------------------------------------------------
class EventType:
    # Treino
    WORKOUT_STARTED = "workout_started"
    WORKOUT_COMPLETED = "workout_completed"
    WORKOUT_SKIPPED = "workout_skipped"
    SESSION_FEEDBACK_RECEIVED = "session_feedback_received"

    # Alimentação
    MEAL_LOGGED = "meal_logged"

    # Comunicação
    MESSAGE_RECEIVED = "message_received"
    CHECKIN_SENT = "checkin_sent"
    REENGAGEMENT_SENT = "reengagement_sent"

    # Score
    SCORE_CHANGED = "score_changed"

    # IA
    INSIGHT_GENERATED = "insight_generated"
    PATTERN_INSIGHT_GENERATED = "pattern_insight_generated"

    # Outros
    ABSENCE_DETECTED = "absence_detected"
    STUDENT_CREATED = "student_created"
    WORKOUT_ASSIGNED = "workout_assigned"

    # Academia (conectores externos)
    ACADEMY_CHECKIN_DETECTED = "academy_checkin_detected"
    ACADEMY_ABSENCE_DETECTED = "academy_absence_detected"

    # Wearables
    WEARABLE_CONNECTED = "wearable_connected"
    WEARABLE_DISCONNECTED = "wearable_disconnected"
    WEARABLE_SYNC_COMPLETED = "wearable_sync_completed"
    WEARABLE_ALERT_TRIGGERED = "wearable_alert_triggered"


class EventSource:
    WHATSAPP = "whatsapp"
    SYSTEM = "system"
    PROFESSIONAL = "professional"
    PORTAL = "portal"
    CELERY = "celery"
    ACADEMY = "academy"
    WEARABLE = "wearable"


class StudentEvent(UUIDPrimaryKeyMixin, db.Model):
    """
    Append-only log de eventos do aluno.
    Tudo o que acontece — treino, refeição, mensagem, score — gera um evento aqui.
    Esta é a fonte de verdade para timeline, IA e automações.
    """

    __tablename__ = "student_events"

    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)
    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, index=True, default=EventSource.SYSTEM)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(db.Text())

    payload_json: Mapped[dict] = mapped_column(
        JSONB().with_variant(db.JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    class Meta:
        indexes = [
            db.Index("ix_student_events_student_occurred", "student_id", "occurred_at"),
            db.Index("ix_student_events_student_type", "student_id", "event_type"),
            db.Index("ix_student_events_account_occurred", "account_id", "occurred_at"),
        ]

    def __repr__(self) -> str:
        return f"<StudentEvent {self.event_type} student={self.student_id}>"


class StudentHealthScore(UUIDPrimaryKeyMixin, db.Model):
    """
    Score de saúde operacional do aluno, persistido diariamente.
    Permite calcular tendências reais ao longo do tempo.

    Níveis:
      ok        → score >= 75  (verde)
      attention → score 55-74  (amarelo)
      cooling   → score 35-54  (laranja)
      risk      → score < 35   (vermelho)
    """

    __tablename__ = "student_health_scores"
    __table_args__ = (
        UniqueConstraint("student_id", "score_date", "score_type", name="uq_student_health_score_student_date_type"),
    )

    student_id: Mapped[str] = mapped_column(db.ForeignKey("student_profiles.id"), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(db.ForeignKey("accounts.id"), nullable=False, index=True)

    score_date: Mapped[datetime] = mapped_column(db.Date(), nullable=False, index=True)
    score_type: Mapped[str] = mapped_column(String(20), nullable=False, default="operational", index=True)

    raw_score: Mapped[int] = mapped_column(db.Integer, nullable=False, default=70)
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="ok", index=True)
    trend: Mapped[str] = mapped_column(String(20), nullable=False, default="stable")

    # Breakdown por dimensão
    components_json: Mapped[dict] = mapped_column(
        JSONB().with_variant(db.JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<StudentHealthScore student={self.student_id} date={self.score_date} score={self.raw_score} level={self.level}>"
