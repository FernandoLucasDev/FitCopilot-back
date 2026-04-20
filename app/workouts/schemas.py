from __future__ import annotations

from datetime import date

from pydantic import Field

from app.common.schemas.base import ApiSchema


class WorkoutExerciseInput(ApiSchema):
    order_index: int
    exercise_name: str = Field(min_length=1, max_length=160)
    sets_count: int | None = None
    reps_text: str | None = None
    rest_seconds: int | None = None
    notes: str | None = None


class WorkoutDayInput(ApiSchema):
    label: str = Field(min_length=1, max_length=80)
    order_index: int
    notes: str | None = None
    exercises: list[WorkoutExerciseInput]


class CreateWorkoutPlanInput(ApiSchema):
    title: str = Field(min_length=2, max_length=120)
    objective: str | None = None
    notes: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    days: list[WorkoutDayInput]


class UpdateWorkoutPlanInput(ApiSchema):
    title: str | None = None
    objective: str | None = None
    notes: str | None = None
