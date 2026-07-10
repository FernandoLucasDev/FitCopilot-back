from __future__ import annotations

from flask import Blueprint

from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth
from app.nutrition.plan_schemas import AssignNutritionPlanInput, CreateNutritionPlanInput, UpdateNutritionPlanInput
from app.nutrition.plan_services import (
    archive_nutrition_plan,
    assign_nutrition_plan_to_student,
    create_nutrition_plan,
    get_active_nutrition_plan_for_student,
    list_student_nutrition_plans,
    require_nutrition_plan,
    serialize_nutrition_plan,
    serialize_student_nutrition_plan,
    update_nutrition_plan,
)
from app.nutrition.services import latest_food_score, weekly_food_summary
from app.students.services import require_student


nutrition_bp = Blueprint("nutrition", __name__)


@nutrition_bp.get("/students/<uuid:student_id>/nutrition/weekly-summary")
@require_auth()
def student_weekly_food_summary(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response({"summary": weekly_food_summary(student)})


@nutrition_bp.get("/students/<uuid:student_id>/nutrition/food-score")
@require_auth()
def student_food_score(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response({"score": latest_food_score(student)})


@nutrition_bp.post("/students/<uuid:student_id>/nutrition-plans")
@require_auth({"owner", "professional", "admin"})
def post_student_nutrition_plan(student_id):
    auth = current_auth()
    payload = parse_json(CreateNutritionPlanInput)
    plan = create_nutrition_plan(account_id=auth.account_id, student_id=student_id, actor_user_id=auth.user.id, data=payload)
    return success_response({"nutritionPlan": serialize_nutrition_plan(plan)}, 201)


@nutrition_bp.get("/students/<uuid:student_id>/nutrition-plans")
@require_auth()
def get_student_nutrition_plans(student_id):
    auth = current_auth()
    return success_response({"items": list_student_nutrition_plans(account_id=auth.account_id, student_id=student_id)})


@nutrition_bp.get("/students/<uuid:student_id>/nutrition-plan")
@require_auth()
def get_student_active_nutrition_plan(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    return success_response({"nutritionPlan": serialize_nutrition_plan(get_active_nutrition_plan_for_student(student_id))})


@nutrition_bp.post("/students/<uuid:student_id>/assign-nutrition-plan")
@require_auth({"owner", "professional", "admin"})
def assign_nutrition_plan(student_id):
    auth = current_auth()
    payload = parse_json(AssignNutritionPlanInput)
    assignment = assign_nutrition_plan_to_student(
        account_id=auth.account_id,
        student_id=student_id,
        plan_id=payload.plan_id,
        actor_user_id=auth.user.id,
    )
    return success_response({"studentNutritionPlan": serialize_student_nutrition_plan(assignment)}, 201)


@nutrition_bp.patch("/nutrition-plans/<uuid:plan_id>")
@require_auth({"owner", "professional", "admin"})
def patch_nutrition_plan(plan_id):
    auth = current_auth()
    payload = parse_json(UpdateNutritionPlanInput)
    plan = require_nutrition_plan(auth.account_id, plan_id)
    return success_response({"nutritionPlan": serialize_nutrition_plan(update_nutrition_plan(plan=plan, actor_user_id=auth.user.id, data=payload))})


@nutrition_bp.post("/nutrition-plans/<uuid:plan_id>/archive")
@require_auth({"owner", "professional", "admin"})
def archive_nutrition_plan_endpoint(plan_id):
    auth = current_auth()
    plan = require_nutrition_plan(auth.account_id, plan_id)
    return success_response({"nutritionPlan": serialize_nutrition_plan(archive_nutrition_plan(plan=plan, actor_user_id=auth.user.id))})
