from __future__ import annotations

from datetime import date, datetime, timezone
from http import HTTPStatus

from app.common.api import ApiError
from app.extensions import db
from app.jobs.services import create_audit_log
from app.students.models import StudentDailySignal, StudentProfile
from app.students.services import require_student
from app.workouts.models import ExerciseLog, StudentWorkout, WorkoutDayExercise, WorkoutPlan, WorkoutPlanDay, WorkoutSession


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_workout_plan(*, account_id, student_id, actor_user_id, data) -> WorkoutPlan:
    linked_student_id = student_id or data.student_id
    student = require_student(account_id, linked_student_id) if linked_student_id else None
    latest = (
        WorkoutPlan.query.filter_by(student_id=student.id if student else None, created_by_user_id=actor_user_id)
        .order_by(WorkoutPlan.version_number.desc())
        .first()
        if student
        else None
    )
    version = 1 if latest is None else latest.version_number + 1
    plan = WorkoutPlan(
        account_id=account_id,
        student_id=student.id if student else None,
        created_by_user_id=actor_user_id,
        title=data.title,
        objective=data.objective,
        notes=data.notes,
        version_number=version,
        previous_version_id=latest.id if latest else None,
        valid_from=data.valid_from,
        valid_until=data.valid_until,
        status="draft",
    )
    db.session.add(plan)
    db.session.flush()

    for day_input in data.days:
        day = WorkoutPlanDay(
            workout_plan_id=plan.id,
            label=day_input.label,
            order_index=day_input.order_index,
            notes=day_input.notes,
        )
        db.session.add(day)
        db.session.flush()
        for exercise_input in day_input.exercises:
            db.session.add(
                WorkoutDayExercise(
                    workout_plan_day_id=day.id,
                    order_index=exercise_input.order_index,
                    exercise_name=exercise_input.exercise_name,
                    sets_count=exercise_input.sets_count,
                    reps_text=exercise_input.reps_text,
                    rest_seconds=exercise_input.rest_seconds,
                    notes=exercise_input.notes,
                )
            )

    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_plan",
        entity_id=plan.id,
        action="created",
        new_values={"title": plan.title, "version": plan.version_number, "student_id": str(student.id) if student else None},
    )
    db.session.commit()
    return plan


def list_workout_plans(*, account_id, actor_user_id) -> list[dict]:
    plans = (
        WorkoutPlan.query.filter_by(account_id=account_id, created_by_user_id=actor_user_id)
        .order_by(WorkoutPlan.updated_at.desc())
        .all()
    )
    return [serialize_workout_plan(plan, include_assignment_summary=True) for plan in plans]


def require_workout_plan(account_id, plan_id) -> WorkoutPlan:
    plan = WorkoutPlan.query.filter_by(id=plan_id, account_id=account_id).first()
    if plan is None:
        raise ApiError("Ficha nao encontrada", HTTPStatus.NOT_FOUND)
    return plan


def update_workout_plan(*, plan: WorkoutPlan, actor_user_id, data) -> WorkoutPlan:
    old_values = {"title": plan.title, "objective": plan.objective}
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(plan, key, value)
    create_audit_log(
        account_id=plan.account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_plan",
        entity_id=plan.id,
        action="updated",
        old_values=old_values,
        new_values={"title": plan.title, "objective": plan.objective},
    )
    db.session.commit()
    return plan


def archive_workout_plan(*, plan: WorkoutPlan, actor_user_id) -> WorkoutPlan:
    old_values = {"status": plan.status, "archived_at": plan.archived_at.isoformat() if plan.archived_at else None}
    plan.status = "archived"
    plan.archived_at = utcnow()
    StudentWorkout.query.filter_by(plan_id=plan.id, active=True).update({"active": False})
    create_audit_log(
        account_id=plan.account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_plan",
        entity_id=plan.id,
        action="archived",
        old_values=old_values,
        new_values={"status": plan.status, "archived_at": plan.archived_at.isoformat()},
    )
    db.session.commit()
    return plan


def activate_workout_plan(*, plan: WorkoutPlan, actor_user_id) -> WorkoutPlan:
    if plan.archived_at is not None:
        raise ApiError("Ficha arquivada nao pode ser ativada.", HTTPStatus.CONFLICT)
    active_plan = WorkoutPlan.query.filter_by(student_id=plan.student_id, status="active").first()
    if active_plan and active_plan.id != plan.id:
        active_plan.status = "draft"
    plan.status = "active"
    create_audit_log(
        account_id=plan.account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_plan",
        entity_id=plan.id,
        action="activated",
        new_values={"status": "active"},
    )
    db.session.commit()
    return plan


def assign_workout_to_student(*, account_id, student_id, plan_id, actor_user_id) -> StudentWorkout:
    student = require_student(account_id, student_id)
    plan = require_workout_plan(account_id, plan_id)
    if plan.archived_at is not None:
        raise ApiError("Ficha arquivada nao pode ser atribuida ao aluno.", HTTPStatus.CONFLICT)
    current_assignment = get_active_assignment(student.id)
    if current_assignment and current_assignment.plan_id != plan.id:
        current_assignment.active = False
        if current_assignment.plan and current_assignment.plan.status == "active":
            current_assignment.plan.status = "draft"
    StudentWorkout.query.filter_by(student_id=student.id, active=True).update({"active": False})
    assignment = StudentWorkout(
        student_id=student.id,
        plan_id=plan.id,
        assigned_by_user_id=actor_user_id,
        assigned_at=utcnow(),
        active=True,
    )
    db.session.add(assignment)
    db.session.flush()
    plan.student_id = student.id
    plan.status = "active"
    student.last_signal_summary = f"Ficha ativa: {plan.title}"
    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="student_workout",
        entity_id=assignment.id,
        action="assigned",
        new_values={"student_id": str(student.id), "plan_id": str(plan.id)},
    )
    db.session.commit()
    return assignment


def get_active_assignment(student_id) -> StudentWorkout | None:
    return (
        StudentWorkout.query.filter_by(student_id=student_id, active=True)
        .order_by(StudentWorkout.assigned_at.desc())
        .first()
    )


def get_active_workout_for_student(student_id) -> WorkoutPlan | None:
    assignment = get_active_assignment(student_id)
    if assignment and assignment.plan and assignment.plan.archived_at is None:
        return assignment.plan
    return (
        WorkoutPlan.query.filter_by(student_id=student_id, status="active", archived_at=None)
        .order_by(WorkoutPlan.updated_at.desc())
        .first()
    )


def list_student_workout_plans(*, account_id, student_id) -> list[dict]:
    require_student(account_id, student_id)
    plans = (
        WorkoutPlan.query.filter(
            WorkoutPlan.account_id == account_id,
            WorkoutPlan.student_id == student_id,
            WorkoutPlan.archived_at.is_(None),
        )
        .order_by(WorkoutPlan.status.desc(), WorkoutPlan.updated_at.desc())
        .all()
    )
    return [serialize_workout_plan(plan, include_assignment_summary=True) for plan in plans]


def create_workout_session(*, account_id, actor_user_id, data) -> WorkoutSession:
    student = require_student(account_id, data.student_id)
    plan = require_workout_plan(account_id, data.plan_id)
    assignment = get_active_assignment(student.id)
    session = WorkoutSession(
        student_id=student.id,
        plan_id=plan.id,
        student_workout_id=assignment.id if assignment and assignment.plan_id == plan.id else None,
        session_date=data.date,
        status=data.status,
        notes=data.notes,
    )
    db.session.add(session)
    db.session.flush()

    for exercise in data.exercises:
        db.session.add(
            ExerciseLog(
                session_id=session.id,
                exercise_name=exercise.exercise_name,
                sets_completed=exercise.sets_completed,
                reps_completed=exercise.reps_completed,
                notes=exercise.notes,
            )
        )

    student.last_activity_at = utcnow()
    student.last_signal_summary = _session_signal_summary(plan.title, data.status)
    _register_session_signal(account_id=account_id, actor_user_id=actor_user_id, student=student, plan=plan, session=session)

    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_session",
        entity_id=session.id,
        action="created",
        new_values={"student_id": str(student.id), "plan_id": str(plan.id), "status": session.status},
    )
    db.session.commit()
    return session


def update_workout_session(*, account_id, actor_user_id, session_id, data) -> WorkoutSession:
    student = require_student(account_id, data.student_id)
    plan = require_workout_plan(account_id, data.plan_id)
    session = WorkoutSession.query.filter_by(id=session_id, student_id=student.id).first()
    if session is None:
        raise ApiError("Sessao de treino nao encontrada", HTTPStatus.NOT_FOUND)
    if session.plan_id != plan.id:
        raise ApiError("Sessao pertence a outra ficha.", HTTPStatus.CONFLICT)

    old_values = {"status": session.status, "notes": session.notes}
    session.status = data.status
    session.notes = data.notes
    session.session_date = data.date
    session.exercise_logs.clear()
    db.session.flush()

    for exercise in data.exercises:
        db.session.add(
            ExerciseLog(
                session_id=session.id,
                exercise_name=exercise.exercise_name,
                sets_completed=exercise.sets_completed,
                reps_completed=exercise.reps_completed,
                notes=exercise.notes,
            )
        )

    student.last_activity_at = utcnow()
    student.last_signal_summary = _session_signal_summary(plan.title, data.status)
    _register_session_signal(account_id=account_id, actor_user_id=actor_user_id, student=student, plan=plan, session=session)

    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_session",
        entity_id=session.id,
        action="updated",
        old_values=old_values,
        new_values={"student_id": str(student.id), "plan_id": str(plan.id), "status": session.status},
    )
    db.session.commit()
    return session


def complete_workout_session_without_logs(*, session: WorkoutSession, actor_user_id, note: str) -> WorkoutSession:
    old_values = {"status": session.status, "notes": session.notes}
    session.status = "completed"
    session.notes = f"{session.notes or ''}\n{note}".strip()
    student = session.student
    plan = session.plan
    student.last_activity_at = utcnow()
    student.last_signal_summary = _session_signal_summary(plan.title if plan else "Treino", session.status)
    if plan:
        _register_session_signal(account_id=student.account_id, actor_user_id=actor_user_id, student=student, plan=plan, session=session)
    create_audit_log(
        account_id=student.account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_session",
        entity_id=session.id,
        action="auto_completed",
        old_values=old_values,
        new_values={"student_id": str(student.id), "plan_id": str(session.plan_id), "status": session.status},
    )
    db.session.commit()
    return session


def list_student_sessions(*, account_id, student_id) -> list[dict]:
    require_student(account_id, student_id)
    sessions = (
        WorkoutSession.query.filter_by(student_id=student_id)
        .order_by(WorkoutSession.session_date.desc(), WorkoutSession.created_at.desc())
        .limit(30)
        .all()
    )
    return [serialize_workout_session(item) for item in sessions]


def serialize_workout_plan(plan: WorkoutPlan | None, *, include_assignment_summary: bool = False) -> dict | None:
    if plan is None:
        return None
    payload = {
        "id": str(plan.id),
        "title": plan.title,
        "objective": plan.objective,
        "description": plan.notes,
        "status": plan.status,
        "versionNumber": plan.version_number,
        "studentId": str(plan.student_id) if plan.student_id else None,
        "days": [
            {
                "id": str(day.id),
                "label": day.label,
                "orderIndex": day.order_index,
                "notes": day.notes,
                "exercises": [
                    {
                        "id": str(exercise.id),
                        "orderIndex": exercise.order_index,
                        "exerciseName": exercise.exercise_name,
                        "setsCount": exercise.sets_count,
                        "repsText": exercise.reps_text,
                        "restSeconds": exercise.rest_seconds,
                        "notes": exercise.notes,
                    }
                    for exercise in sorted(day.exercises, key=lambda item: item.order_index)
                ],
            }
            for day in sorted(plan.days, key=lambda item: item.order_index)
        ],
    }
    if include_assignment_summary:
        latest_assignment = (
            StudentWorkout.query.filter_by(plan_id=plan.id)
            .order_by(StudentWorkout.assigned_at.desc())
            .first()
        )
        payload["assignment"] = (
            {
                "studentId": str(latest_assignment.student_id),
                "assignedAt": latest_assignment.assigned_at.isoformat(),
                "active": latest_assignment.active,
            }
            if latest_assignment
            else None
        )
    return payload


def serialize_student_workout(assignment: StudentWorkout | None) -> dict | None:
    if assignment is None:
        return None
    plan_payload = serialize_workout_plan(assignment.plan)
    return {
        "id": str(assignment.id),
        "studentId": str(assignment.student_id),
        "planId": str(assignment.plan_id),
        "active": assignment.active,
        "assignedAt": assignment.assigned_at.isoformat(),
        "plan": plan_payload,
    }


def serialize_workout_session(session: WorkoutSession) -> dict:
    return {
        "id": str(session.id),
        "studentId": str(session.student_id),
        "planId": str(session.plan_id),
        "date": session.session_date.isoformat(),
        "status": session.status,
        "notes": session.notes,
        "planTitle": session.plan.title if session.plan else None,
        "exercises": [
            {
                "id": str(item.id),
                "exerciseName": item.exercise_name,
                "setsCompleted": item.sets_completed,
                "repsCompleted": item.reps_completed,
                "notes": item.notes,
            }
            for item in session.exercise_logs
        ],
    }


def summarize_workout_consistency(student: StudentProfile) -> dict:
    today = utcnow().date()
    seven_days_ago = today.fromordinal(today.toordinal() - 6)
    sessions = (
        WorkoutSession.query.filter(
            WorkoutSession.student_id == student.id,
            WorkoutSession.session_date >= seven_days_ago,
        )
        .order_by(WorkoutSession.session_date.desc())
        .all()
    )
    completed = sum(1 for session in sessions if session.status == "completed")
    skipped = sum(1 for session in sessions if session.status == "skipped")
    pending = sum(1 for session in sessions if session.status == "pending")
    trend = "stable"
    if skipped >= 2:
        trend = "down"
    elif completed >= 3 and skipped == 0:
        trend = "up"
    summary = "Sem sessoes registradas na semana."
    if sessions:
        summary = f"{completed} sessoes concluidas, {skipped} ignoradas e {pending} pendentes na semana."
    return {
        "completedCount": completed,
        "skippedCount": skipped,
        "pendingCount": pending,
        "trend": trend,
        "summary": summary,
    }


def _session_signal_summary(plan_title: str, status: str) -> str:
    mapping = {
        "completed": f"Treino concluido: {plan_title}",
        "skipped": f"Treino ignorado: {plan_title}",
        "pending": f"Treino pendente: {plan_title}",
    }
    return mapping.get(status, f"Sessao registrada: {plan_title}")


def _register_session_signal(*, account_id, actor_user_id, student: StudentProfile, plan: WorkoutPlan, session: WorkoutSession) -> None:
    signal_type = "workout" if session.status == "completed" else "absence" if session.status == "skipped" else "manual_note"
    db.session.add(
        StudentDailySignal(
            account_id=account_id,
            student_id=student.id,
            signal_date=session.session_date,
            signal_type=signal_type,
            source="web",
            title=_session_signal_summary(plan.title, session.status),
            body=session.notes,
            payload_json={"plan_id": str(plan.id), "session_id": str(session.id), "status": session.status},
            created_by_user_id=actor_user_id,
            created_at=utcnow(),
        )
    )
