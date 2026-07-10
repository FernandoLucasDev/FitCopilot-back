from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from secrets import token_urlsafe

from sqlalchemy import desc, select
from werkzeug.security import generate_password_hash

from app.accounts.models import Account, ProfessionalProfile
from app.auth.models import User
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


def get_student_access_status(student: StudentProfile) -> str:
    if not student.email:
        return "no_email"
    if student.user_id:
        return "active"
    return "pending_activation"


def ensure_student_portal_user(student: StudentProfile) -> User:
    if student.user_id:
        user = User.query.filter_by(id=student.user_id).first()
        if user is not None:
            return user

    if not student.email:
        raise ApiError("Aluno sem e-mail para ativar acesso.", HTTPStatus.CONFLICT)

    existing_user = User.query.filter_by(email=student.email).first()
    if existing_user is not None:
        student.user_id = existing_user.id
        existing_user.is_active = True
        existing_user.is_email_verified = True
        if existing_user.account_id is None:
            existing_user.account_id = student.account_id
        db.session.flush()
        return existing_user

    user = User(
        account_id=student.account_id,
        role="student",
        full_name=student.full_name,
        email=student.email,
        phone=student.phone,
        password_hash=generate_password_hash(token_urlsafe(32)),
        is_active=True,
        is_email_verified=True,
    )
    db.session.add(user)
    db.session.flush()
    student.user_id = user.id
    return user


def create_student(*, account_id, professional_id, actor_user_id, data) -> StudentProfile:
    account = Account.query.filter_by(id=account_id, deleted_at=None).first()
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    total_students = StudentProfile.query.filter_by(account_id=account_id, archived_at=None).count()
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
    if student.phone:
        try:
            from app.whatsapp.services import send_onboarding_message

            send_onboarding_message(student=student, actor_user_id=actor_user_id, enqueue=False)
        except Exception as exc:  # pragma: no cover - onboarding should not block student creation
            from flask import current_app

            current_app.logger.warning("student_auto_whatsapp_onboarding_failed student_id=%s error=%s", student.id, exc)
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


def delete_student(*, student: StudentProfile, actor_user_id) -> None:
    from app.events.models import StudentEvent, StudentHealthScore
    from app.files.models import StudentFile
    from app.insights.models import AIInsight
    from app.jobs.models import BackgroundJob
    from app.messaging.models import SuggestedMessage
    from app.operations.models import AutomationDecision
    from app.physical.models import (
        PhysicalAssessment,
        PhysicalAssessmentAIRun,
        PhysicalAssessmentComparison,
        PhysicalAssessmentPhoto,
    )
    from app.reports.models import GeneratedReport
    from app.students.portal_models import StudentLoginChallenge
    from app.whatsapp.models import (
        InboundMessageRecord,
        OutboundMessageDispatch,
        WhatsAppDeliveryStatusEvent,
        WhatsAppSession,
    )
    from app.workouts.models import ExerciseLog, StudentWorkout, WorkoutDayExercise, WorkoutPlan, WorkoutPlanDay, WorkoutSession

    student_id = student.id
    account_id = student.account_id
    user_id = student.user_id
    old_values = {"full_name": student.full_name, "email": student.email, "phone": student.phone}

    dispatch_ids = [row.id for row in OutboundMessageDispatch.query.filter_by(student_id=student_id).all()]
    if dispatch_ids:
        WhatsAppDeliveryStatusEvent.query.filter(
            WhatsAppDeliveryStatusEvent.outbound_dispatch_id.in_(dispatch_ids)
        ).delete(synchronize_session=False)

    session_ids = [row.id for row in WorkoutSession.query.filter_by(student_id=student_id).all()]
    if session_ids:
        ExerciseLog.query.filter(ExerciseLog.session_id.in_(session_ids)).delete(synchronize_session=False)

    plan_ids = [row.id for row in WorkoutPlan.query.filter_by(student_id=student_id).all()]
    if plan_ids:
        day_ids = [row.id for row in WorkoutPlanDay.query.filter(WorkoutPlanDay.workout_plan_id.in_(plan_ids)).all()]
        if day_ids:
            WorkoutDayExercise.query.filter(WorkoutDayExercise.workout_plan_day_id.in_(day_ids)).delete(
                synchronize_session=False
            )
        WorkoutPlan.query.filter(WorkoutPlan.id.in_(plan_ids)).update(
            {"previous_version_id": None},
            synchronize_session=False,
        )
        WorkoutPlanDay.query.filter(WorkoutPlanDay.workout_plan_id.in_(plan_ids)).delete(synchronize_session=False)

    assessment_ids = [row.id for row in PhysicalAssessment.query.filter_by(student_id=student_id).all()]
    if assessment_ids:
        PhysicalAssessmentComparison.query.filter(
            (PhysicalAssessmentComparison.from_assessment_id.in_(assessment_ids))
            | (PhysicalAssessmentComparison.to_assessment_id.in_(assessment_ids))
        ).delete(synchronize_session=False)
        PhysicalAssessmentPhoto.query.filter(PhysicalAssessmentPhoto.assessment_id.in_(assessment_ids)).delete(
            synchronize_session=False
        )
        PhysicalAssessmentAIRun.query.filter(PhysicalAssessmentAIRun.assessment_id.in_(assessment_ids)).delete(
            synchronize_session=False
        )

    AIInsight.query.filter_by(student_id=student_id).delete(synchronize_session=False)

    for model in (
        SuggestedMessage,
        AutomationDecision,
        StudentEvent,
        StudentHealthScore,
        StudentLoginChallenge,
        WhatsAppSession,
        OutboundMessageDispatch,
        InboundMessageRecord,
        GeneratedReport,
        StudentFile,
        PhysicalAssessment,
        WorkoutSession,
        StudentWorkout,
        WorkoutPlan,
        StudentDailySignal,
        StudentDailySummary,
        StudentInteraction,
        StudentHealthContext,
    ):
        model.query.filter_by(student_id=student_id).delete(synchronize_session=False)

    BackgroundJob.query.filter_by(student_id=student_id).update({"student_id": None}, synchronize_session=False)

    if user_id:
        user = User.query.filter_by(id=user_id, role="student").first()
        if user is not None:
            db.session.delete(user)

    db.session.delete(student)
    db.session.flush()
    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="student_profile",
        entity_id=student_id,
        action="deleted",
        old_values=old_values,
    )
    db.session.commit()


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


def list_students_for_workspace(
    *, account_id, search: str | None = None, status: str | None = None, primary_professional_id=None
) -> list[dict]:
    query = StudentProfile.query.filter_by(account_id=account_id)
    if primary_professional_id is not None:
        query = query.filter(StudentProfile.primary_professional_id == primary_professional_id)
    if status == "archived":
        query = query.filter(StudentProfile.archived_at.is_not(None))
    else:
        query = query.filter(StudentProfile.archived_at.is_(None))
    if search:
        like = f"%{search}%"
        query = query.filter(StudentProfile.full_name.ilike(like))
    if status and status not in {"all", "archived"}:
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
        "email": student.email,
        "portalAccessStatus": get_student_access_status(student),
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
    from app.workouts.services import get_active_workout_for_student

    assignment_plan = get_active_workout_for_student(student_id)
    if assignment_plan is not None:
        return assignment_plan
    return (
        WorkoutPlan.query.filter_by(student_id=student_id, status="active")
        .order_by(desc(WorkoutPlan.updated_at))
        .first()
    )
