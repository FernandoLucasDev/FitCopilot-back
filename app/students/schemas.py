from __future__ import annotations

from datetime import date

from pydantic import Field

from app.common.schemas.base import ApiSchema


class CreateStudentInput(ApiSchema):
    full_name: str = Field(min_length=2, max_length=160)
    email: str | None = None
    phone: str | None = None
    birth_date: date | None = None
    sex: str | None = None
    goal_type: str | None = None
    main_objective_text: str | None = None
    notes: str | None = None


class UpdateStudentInput(ApiSchema):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    email: str | None = None
    phone: str | None = None
    birth_date: date | None = None
    sex: str | None = None
    current_weight_kg: float | None = None
    height_cm: float | None = None
    goal_type: str | None = None
    main_objective_text: str | None = None
    notes: str | None = None
    status: str | None = None
    daily_calorie_target: int | None = Field(default=None, ge=800, le=8000)
