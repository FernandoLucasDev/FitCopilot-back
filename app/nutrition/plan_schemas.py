from __future__ import annotations

from pydantic import Field

from app.common.schemas.base import ApiSchema


class NutritionFoodItemInput(ApiSchema):
    order_index: int
    food_name: str = Field(min_length=1, max_length=160)
    quantity_text: str | None = None
    calories: int | None = None
    protein_grams: int | None = None
    carbs_grams: int | None = None
    fats_grams: int | None = None
    notes: str | None = None


class NutritionPlanMealInput(ApiSchema):
    label: str = Field(min_length=1, max_length=80)
    order_index: int
    notes: str | None = None
    items: list[NutritionFoodItemInput]


class CreateNutritionPlanInput(ApiSchema):
    title: str = Field(min_length=2, max_length=120)
    objective: str | None = None
    notes: str | None = None
    meals: list[NutritionPlanMealInput]
    student_id: str | None = None


class UpdateNutritionPlanInput(ApiSchema):
    title: str | None = None
    objective: str | None = None
    notes: str | None = None


class AssignNutritionPlanInput(ApiSchema):
    plan_id: str
