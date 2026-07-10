from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus

from app.common.api import ApiError
from app.extensions import db
from app.jobs.services import create_audit_log
from app.nutrition.models import NutritionPlan, NutritionPlanFoodItem, NutritionPlanMeal, StudentNutritionPlan
from app.students.services import require_student


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_nutrition_plan(*, account_id, student_id, actor_user_id, data) -> NutritionPlan:
    linked_student_id = student_id or data.student_id
    student = require_student(account_id, linked_student_id) if linked_student_id else None
    latest = (
        NutritionPlan.query.filter_by(student_id=student.id if student else None, created_by_user_id=actor_user_id)
        .order_by(NutritionPlan.version_number.desc())
        .first()
        if student
        else None
    )
    version = 1 if latest is None else latest.version_number + 1
    plan = NutritionPlan(
        account_id=account_id,
        student_id=student.id if student else None,
        created_by_user_id=actor_user_id,
        title=data.title,
        objective=data.objective,
        notes=data.notes,
        version_number=version,
        previous_version_id=latest.id if latest else None,
        status="draft",
    )
    db.session.add(plan)
    db.session.flush()

    for meal_input in data.meals:
        meal = NutritionPlanMeal(
            nutrition_plan_id=plan.id,
            label=meal_input.label,
            order_index=meal_input.order_index,
            notes=meal_input.notes,
        )
        db.session.add(meal)
        db.session.flush()
        for item_input in meal_input.items:
            db.session.add(
                NutritionPlanFoodItem(
                    nutrition_plan_meal_id=meal.id,
                    order_index=item_input.order_index,
                    food_name=item_input.food_name,
                    quantity_text=item_input.quantity_text,
                    calories=item_input.calories,
                    protein_grams=item_input.protein_grams,
                    carbs_grams=item_input.carbs_grams,
                    fats_grams=item_input.fats_grams,
                    notes=item_input.notes,
                )
            )

    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="nutrition_plan",
        entity_id=plan.id,
        action="created",
        new_values={"title": plan.title, "version": plan.version_number, "student_id": str(student.id) if student else None},
    )
    db.session.commit()
    return plan


def require_nutrition_plan(account_id, plan_id) -> NutritionPlan:
    plan = NutritionPlan.query.filter_by(id=plan_id, account_id=account_id).first()
    if plan is None:
        raise ApiError("Plano alimentar não encontrado", HTTPStatus.NOT_FOUND)
    return plan


def update_nutrition_plan(*, plan: NutritionPlan, actor_user_id, data) -> NutritionPlan:
    old_values = {"title": plan.title, "objective": plan.objective}
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(plan, key, value)
    create_audit_log(
        account_id=plan.account_id,
        actor_user_id=actor_user_id,
        entity_type="nutrition_plan",
        entity_id=plan.id,
        action="updated",
        old_values=old_values,
        new_values={"title": plan.title, "objective": plan.objective},
    )
    db.session.commit()
    return plan


def archive_nutrition_plan(*, plan: NutritionPlan, actor_user_id) -> NutritionPlan:
    old_values = {"status": plan.status, "archived_at": plan.archived_at.isoformat() if plan.archived_at else None}
    plan.status = "archived"
    plan.archived_at = utcnow()
    StudentNutritionPlan.query.filter_by(plan_id=plan.id, active=True).update({"active": False})
    create_audit_log(
        account_id=plan.account_id,
        actor_user_id=actor_user_id,
        entity_type="nutrition_plan",
        entity_id=plan.id,
        action="archived",
        old_values=old_values,
        new_values={"status": plan.status, "archived_at": plan.archived_at.isoformat()},
    )
    db.session.commit()
    return plan


def assign_nutrition_plan_to_student(*, account_id, student_id, plan_id, actor_user_id) -> StudentNutritionPlan:
    student = require_student(account_id, student_id)
    plan = require_nutrition_plan(account_id, plan_id)
    if plan.archived_at is not None:
        raise ApiError("Plano arquivado não pode ser atribuído ao paciente.", HTTPStatus.CONFLICT)
    current_assignment = get_active_assignment(student.id)
    if current_assignment and current_assignment.plan_id != plan.id:
        current_assignment.active = False
        if current_assignment.plan and current_assignment.plan.status == "active":
            current_assignment.plan.status = "draft"
    StudentNutritionPlan.query.filter_by(student_id=student.id, active=True).update({"active": False})
    assignment = StudentNutritionPlan(
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
    student.last_signal_summary = f"Plano alimentar ativo: {plan.title}"
    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="student_nutrition_plan",
        entity_id=assignment.id,
        action="assigned",
        new_values={"student_id": str(student.id), "plan_id": str(plan.id)},
    )
    db.session.commit()
    return assignment


def get_active_assignment(student_id) -> StudentNutritionPlan | None:
    return (
        StudentNutritionPlan.query.filter_by(student_id=student_id, active=True)
        .order_by(StudentNutritionPlan.assigned_at.desc())
        .first()
    )


def get_active_nutrition_plan_for_student(student_id) -> NutritionPlan | None:
    assignment = get_active_assignment(student_id)
    if assignment and assignment.plan and assignment.plan.archived_at is None:
        return assignment.plan
    return (
        NutritionPlan.query.filter_by(student_id=student_id, status="active", archived_at=None)
        .order_by(NutritionPlan.updated_at.desc())
        .first()
    )


def list_student_nutrition_plans(*, account_id, student_id) -> list[dict]:
    require_student(account_id, student_id)
    plans = (
        NutritionPlan.query.filter(
            NutritionPlan.account_id == account_id,
            NutritionPlan.student_id == student_id,
            NutritionPlan.archived_at.is_(None),
        )
        .order_by(NutritionPlan.status.desc(), NutritionPlan.updated_at.desc())
        .all()
    )
    return [serialize_nutrition_plan(plan, include_assignment_summary=True) for plan in plans]


def _sum_totals(items) -> dict:
    totals = {"calories": 0, "protein": 0, "carbs": 0, "fats": 0}
    for item in items:
        totals["calories"] += item.calories or 0
        totals["protein"] += item.protein_grams or 0
        totals["carbs"] += item.carbs_grams or 0
        totals["fats"] += item.fats_grams or 0
    return totals


def serialize_nutrition_plan(plan: NutritionPlan | None, *, include_assignment_summary: bool = False) -> dict | None:
    if plan is None:
        return None
    meals_payload = []
    plan_totals = {"calories": 0, "protein": 0, "carbs": 0, "fats": 0}
    for meal in sorted(plan.meals, key=lambda item: item.order_index):
        items = sorted(meal.food_items, key=lambda item: item.order_index)
        meal_totals = _sum_totals(items)
        for key in plan_totals:
            plan_totals[key] += meal_totals[key]
        meals_payload.append(
            {
                "id": str(meal.id),
                "label": meal.label,
                "orderIndex": meal.order_index,
                "notes": meal.notes,
                "items": [
                    {
                        "id": str(item.id),
                        "orderIndex": item.order_index,
                        "foodName": item.food_name,
                        "quantityText": item.quantity_text,
                        "calories": item.calories,
                        "proteinGrams": item.protein_grams,
                        "carbsGrams": item.carbs_grams,
                        "fatsGrams": item.fats_grams,
                        "notes": item.notes,
                    }
                    for item in items
                ],
                "totals": meal_totals,
            }
        )
    payload = {
        "id": str(plan.id),
        "title": plan.title,
        "objective": plan.objective,
        "description": plan.notes,
        "status": plan.status,
        "versionNumber": plan.version_number,
        "studentId": str(plan.student_id) if plan.student_id else None,
        "meals": meals_payload,
        "totals": plan_totals,
    }
    if include_assignment_summary:
        latest_assignment = (
            StudentNutritionPlan.query.filter_by(plan_id=plan.id)
            .order_by(StudentNutritionPlan.assigned_at.desc())
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


def serialize_student_nutrition_plan(assignment: StudentNutritionPlan | None) -> dict | None:
    if assignment is None:
        return None
    return {
        "id": str(assignment.id),
        "studentId": str(assignment.student_id),
        "planId": str(assignment.plan_id),
        "active": assignment.active,
        "assignedAt": assignment.assigned_at.isoformat(),
        "plan": serialize_nutrition_plan(assignment.plan),
    }
