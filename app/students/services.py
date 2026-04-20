from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus

from sqlalchemy import desc, select

from app.accounts.models import Account, ProfessionalProfile
from app.common.api import ApiError
from app.common.utils.time import ensure_aware, relative_time_label
from app.extensions import db
from app.jobs.services import create_audit_log
from app.students.models import (
    StudentDailySignal,
    StudentDailySummary,
    StudentHealthContext,
    StudentInteraction,
    StudentProfile,
)
from app.workouts.models import WorkoutPlan


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StudentScoreResult:
    score: int
    trend: str
    status: str
    insight: str


def require_student(account_id, student_id) -> StudentProfile:
    student = StudentProfile.query.filter_by(id=student_id, account_id=account_id).first()
    if student is None:
        raise ApiError("Aluno não encontrado", HTTPStatus.NOT_FOUND)
    return student


def create_student(*, account_id, professional_id, actor_user_id, data) -> StudentProfile:
    account = Account.query.filter_by(id=account_id, deleted_at=None).first()
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    total_students = StudentProfile.query.filter_by(account_id=account_id).count()
    if total_students >= account.max_students:
        raise ApiError("Limite de alunos da conta atingido", HTTPStatus.CONFLICT)

    student = StudentProfile(
        account_id=account_id,
        primary_professional_id=professional_id,
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        birth_date=data.birth_date,
        sex=data.sex,
        goal_type=data.goal_type,
        main_objective_text=data.main_objective_text,
        notes=data.notes,
        status="new",
        adherence_score=50,
        adherence_trend="stable",
        last_signal_summary="Cadastro realizado",
    )
    db.session.add(student)
    db.session.flush()

    health_context = StudentHealthContext(student_id=student.id)
    db.session.add(health_context)

    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="student_profile",
        entity_id=student.id,
        action="created",
        new_values={"full_name": student.full_name, "goal_type": student.goal_type},
    )
    db.session.commit()
    return student


def update_student(*, student: StudentProfile, actor_user_id, data) -> StudentProfile:
    old_values = {
        "full_name": student.full_name,
        "goal_type": student.goal_type,
        "status": student.status,
    }
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(student, key, value)
    create_audit_log(
        account_id=student.account_id,
        actor_user_id=actor_user_id,
        entity_type="student_profile",
        entity_id=student.id,
        action="updated",
        old_values=old_values,
        new_values={"full_name": student.full_name, "goal_type": student.goal_type, "status": student.status},
    )
    db.session.commit()
    return student


def archive_student(*, student: StudentProfile, actor_user_id) -> StudentProfile:
    student.status = "archived"
    student.archived_at = utcnow()
    create_audit_log(
        account_id=student.account_id,
        actor_user_id=actor_user_id,
        entity_type="student_profile",
        entity_id=student.id,
        action="archived",
        new_values={"status": student.status},
    )
    db.session.commit()
    return student


def compute_student_score(student: StudentProfile) -> StudentScoreResult:
    now = utcnow()
    score = student.adherence_score or 70
    reasons: list[str] = []

    if student.last_contact_at:
        days_without_contact = (now - ensure_aware(student.last_contact_at)).days
        if days_without_contact >= 7:
            score -= 22
            reasons.append("sem contato há mais de 7 dias")
        elif days_without_contact >= 3:
            score -= 12
            reasons.append("sem contato há alguns dias")
    else:
        score -= 8
        reasons.append("sem contato registrado")

    if student.last_activity_at:
        inactivity_days = (now - ensure_aware(student.last_activity_at)).days
        if inactivity_days >= 5:
            score -= 18
            reasons.append("baixa atividade recente")
        elif inactivity_days >= 2:
            score -= 8
            reasons.append("atividade recente abaixo do ideal")
    elif student.status != "new":
        score -= 10
        reasons.append("sem atividade recente")

    since = now - timedelta(days=7)
    recent_signals = StudentDailySignal.query.filter(
        StudentDailySignal.student_id == student.id,
        StudentDailySignal.created_at >= since,
    ).all()

    positive_count = sum(1 for item in recent_signals if item.signal_type in {"meal", "workout", "message"})
    negative_count = sum(1 for item in recent_signals if item.signal_type in {"absence", "manual_note"})
    score += min(positive_count * 2, 8)
    score -= min(negative_count * 4, 16)

    status = "active"
    trend = student.adherence_trend or "stable"
    if student.archived_at:
        status = "archived"
    elif student.status == "new" and not student.last_activity_at:
        status = "new"
    elif score < 45:
        status = "no_signal"
        trend = "down"
    elif score < 65:
        status = "attention"
        trend = "down" if trend == "stable" else trend
    insight = "; ".join(reasons) if reasons else "rotina estável e sem alertas relevantes"
    return StudentScoreResult(score=max(0, min(score, 100)), trend=trend, status=status, insight=insight)


def recompute_student_score(student: StudentProfile) -> StudentProfile:
    result = compute_student_score(student)
    student.adherence_score = result.score
    student.adherence_trend = result.trend
    if student.status != "archived":
        student.status = result.status
    student.last_signal_summary = result.insight
    db.session.commit()
    return student


def list_students_for_workspace(*, account_id, search: str | None = None, status: str | None = None) -> list[dict]:
    query = StudentProfile.query.filter_by(account_id=account_id)
    if search:
        like = f"%{search}%"
        query = query.filter(StudentProfile.full_name.ilike(like))
    if status and status != "all":
        mapped = {"silent": "no_signal"}.get(status, status)
        query = query.filter(StudentProfile.status == mapped)
    students = query.order_by(StudentProfile.full_name.asc()).all()
    return [serialize_student_list_item(student) for student in students]


def serialize_student_list_item(student: StudentProfile) -> dict:
    score = compute_student_score(student)
    goal = student.main_objective_text or student.goal_type or "Acompanhamento"
    initials = "".join(part[0] for part in student.full_name.split()[:2]).upper()
    status_map = {"active": "ok", "attention": "attention", "no_signal": "silent", "new": "new", "archived": "new"}
    trend_map = {"stable": "flat", "up": "up", "down": "down"}
    return {
        "id": str(student.id),
        "name": student.full_name,
        "initials": initials,
        "goal": goal,
        "status": status_map.get(score.status, "ok"),
        "lastEvent": student.last_signal_summary or "Sem sinais recentes",
        "lastContact": relative_time_label(student.last_contact_at) or "—",
        "lastContactRelativeText": relative_time_label(student.last_contact_at),
        "lastActivityRelativeText": relative_time_label(student.last_activity_at),
        "adherence": score.score,
        "score": score.score,
        "trend": trend_map.get(score.trend, "flat"),
        "scoreLabel": score.insight,
        "flags": {
            "needsAttention": score.status in {"attention", "no_signal"},
            "isNew": score.status == "new",
        },
    }


def get_recent_signals(student_id, limit: int = 6) -> list[StudentDailySignal]:
    return (
        StudentDailySignal.query.filter_by(student_id=student_id)
        .order_by(desc(StudentDailySignal.created_at))
        .limit(limit)
        .all()
    )


def get_recent_interactions(student_id, limit: int = 6) -> list[StudentInteraction]:
    return (
        StudentInteraction.query.filter_by(student_id=student_id)
        .order_by(desc(StudentInteraction.interaction_at))
        .limit(limit)
        .all()
    )


def get_latest_summary(student_id) -> StudentDailySummary | None:
    return (
        StudentDailySummary.query.filter_by(student_id=student_id)
        .order_by(desc(StudentDailySummary.summary_date))
        .first()
    )


def get_active_workout(student_id) -> WorkoutPlan | None:
    return (
        WorkoutPlan.query.filter_by(student_id=student_id, status="active")
        .order_by(desc(WorkoutPlan.updated_at))
        .first()
    )
