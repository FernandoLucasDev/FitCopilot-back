from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus

from app.common.api import ApiError
from app.extensions import db
from app.jobs.services import create_audit_log
from app.students.services import require_student
from app.workouts.models import WorkoutDayExercise, WorkoutPlan, WorkoutPlanDay


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_workout_plan(*, account_id, student_id, actor_user_id, data) -> WorkoutPlan:
    student = require_student(account_id, student_id)
    latest = (
        WorkoutPlan.query.filter_by(student_id=student.id)
        .order_by(WorkoutPlan.version_number.desc())
        .first()
    )
    version = 1 if latest is None else latest.version_number + 1
    plan = WorkoutPlan(
        account_id=account_id,
        student_id=student.id,
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
            exercise = WorkoutDayExercise(
                workout_plan_day_id=day.id,
                order_index=exercise_input.order_index,
                exercise_name=exercise_input.exercise_name,
                sets_count=exercise_input.sets_count,
                reps_text=exercise_input.reps_text,
                rest_seconds=exercise_input.rest_seconds,
                notes=exercise_input.notes,
            )
            db.session.add(exercise)
    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="workout_plan",
        entity_id=plan.id,
        action="created",
        new_values={"title": plan.title, "version": plan.version_number},
    )
    db.session.commit()
    return plan


def require_workout_plan(account_id, plan_id) -> WorkoutPlan:
    plan = WorkoutPlan.query.filter_by(id=plan_id, account_id=account_id).first()
    if plan is None:
        raise ApiError("Ficha não encontrada", HTTPStatus.NOT_FOUND)
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


def activate_workout_plan(*, plan: WorkoutPlan, actor_user_id) -> WorkoutPlan:
    active_plan = WorkoutPlan.query.filter_by(student_id=plan.student_id, status="active").first()
    if active_plan and active_plan.id != plan.id:
        active_plan.status = "archived"
        active_plan.archived_at = utcnow()
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


def serialize_workout_plan(plan: WorkoutPlan | None) -> dict | None:
    if plan is None:
        return None
    return {
        "id": str(plan.id),
        "title": plan.title,
        "objective": plan.objective,
        "status": plan.status,
        "versionNumber": plan.version_number,
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
