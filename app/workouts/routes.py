from __future__ import annotations

from flask import Blueprint

from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth
from app.students.services import require_student
from app.workouts.schemas import AssignWorkoutInput, CreateWorkoutPlanInput, CreateWorkoutSessionInput, UpdateWorkoutPlanInput
from app.workouts.services import (
    activate_workout_plan,
    assign_workout_to_student,
    create_workout_plan,
    create_workout_session,
    get_active_assignment,
    get_active_workout_for_student,
    list_student_sessions,
    list_workout_plans,
    require_workout_plan,
    serialize_student_workout,
    serialize_workout_plan,
    serialize_workout_session,
    update_workout_plan,
)


workouts_bp = Blueprint("workouts", __name__)


@workouts_bp.get("/workouts")
@require_auth({"owner", "professional", "admin"})
def list_workouts():
    auth = current_auth()
    return success_response({"items": list_workout_plans(account_id=auth.account_id, actor_user_id=auth.user.id)})


@workouts_bp.post("/workouts")
@require_auth({"owner", "professional", "admin"})
def post_workout():
    auth = current_auth()
    payload = parse_json(CreateWorkoutPlanInput)
    plan = create_workout_plan(
        account_id=auth.account_id,
        student_id=payload.student_id,
        actor_user_id=auth.user.id,
        data=payload,
    )
    return success_response({"workoutPlan": serialize_workout_plan(plan)}, 201)


@workouts_bp.post("/students/<uuid:student_id>/assign-workout")
@require_auth({"owner", "professional", "admin"})
def assign_workout(student_id):
    auth = current_auth()
    payload = parse_json(AssignWorkoutInput)
    assignment = assign_workout_to_student(
        account_id=auth.account_id,
        student_id=student_id,
        plan_id=payload.plan_id,
        actor_user_id=auth.user.id,
    )
    return success_response({"studentWorkout": serialize_student_workout(assignment)}, 201)


@workouts_bp.post("/workout-sessions")
@require_auth({"owner", "professional", "admin", "student"})
def post_workout_session():
    auth = current_auth()
    payload = parse_json(CreateWorkoutSessionInput)
    session = create_workout_session(account_id=auth.account_id, actor_user_id=auth.user.id, data=payload)
    return success_response({"session": serialize_workout_session(session)}, 201)


@workouts_bp.get("/students/<uuid:student_id>/active-workout")
@require_auth()
def get_student_active_workout(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    assignment = get_active_assignment(student_id)
    workout = get_active_workout_for_student(student_id)
    return success_response({"studentWorkout": serialize_student_workout(assignment), "workoutPlan": serialize_workout_plan(workout)})


@workouts_bp.get("/students/<uuid:student_id>/sessions")
@require_auth()
def get_student_sessions(student_id):
    auth = current_auth()
    return success_response({"items": list_student_sessions(account_id=auth.account_id, student_id=student_id)})


@workouts_bp.get("/students/<uuid:student_id>/workout-plan")
@require_auth()
def get_student_workout(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    return success_response({"workoutPlan": serialize_workout_plan(get_active_workout_for_student(student_id))})


@workouts_bp.post("/students/<uuid:student_id>/workout-plans")
@require_auth({"owner", "professional", "admin"})
def post_student_workout_plan(student_id):
    auth = current_auth()
    payload = parse_json(CreateWorkoutPlanInput)
    plan = create_workout_plan(account_id=auth.account_id, student_id=student_id, actor_user_id=auth.user.id, data=payload)
    return success_response({"workoutPlan": serialize_workout_plan(plan)}, 201)


@workouts_bp.patch("/workout-plans/<uuid:plan_id>")
@require_auth({"owner", "professional", "admin"})
def patch_workout_plan(plan_id):
    auth = current_auth()
    payload = parse_json(UpdateWorkoutPlanInput)
    plan = require_workout_plan(auth.account_id, plan_id)
    return success_response({"workoutPlan": serialize_workout_plan(update_workout_plan(plan=plan, actor_user_id=auth.user.id, data=payload))})


@workouts_bp.post("/workout-plans/<uuid:plan_id>/activate")
@require_auth({"owner", "professional", "admin"})
def activate_plan(plan_id):
    auth = current_auth()
    plan = require_workout_plan(auth.account_id, plan_id)
    return success_response({"workoutPlan": serialize_workout_plan(activate_workout_plan(plan=plan, actor_user_id=auth.user.id))})
