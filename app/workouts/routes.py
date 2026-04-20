from __future__ import annotations

from flask import Blueprint

from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth
from app.students.services import get_active_workout, require_student
from app.workouts.schemas import CreateWorkoutPlanInput, UpdateWorkoutPlanInput
from app.workouts.services import (
    activate_workout_plan,
    create_workout_plan,
    require_workout_plan,
    serialize_workout_plan,
    update_workout_plan,
)


workouts_bp = Blueprint("workouts", __name__)


@workouts_bp.get("/students/<uuid:student_id>/workout-plan")
@require_auth()
def get_student_workout(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    return success_response({"workoutPlan": serialize_workout_plan(get_active_workout(student_id))})


@workouts_bp.post("/students/<uuid:student_id>/workout-plans")
@require_auth({"owner", "professional", "admin"})
def post_workout_plan(student_id):
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
