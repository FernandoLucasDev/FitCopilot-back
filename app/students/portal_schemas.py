from __future__ import annotations

from pydantic import EmailStr, Field

from app.common.schemas.base import ApiSchema


class StudentOtpRequestInput(ApiSchema):
    email: EmailStr


class StudentOtpVerifyInput(ApiSchema):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class StudentExerciseLogInput(ApiSchema):
    exercise_name: str = Field(min_length=1, max_length=160)
    sets_completed: int | None = None
    reps_completed: str | None = None
    notes: str | None = None


class StudentWorkoutSessionInput(ApiSchema):
    plan_id: str
    date: str | None = None
    status: str = Field(pattern="^(pending|completed|skipped)$")
    notes: str | None = None
    exercises: list[StudentExerciseLogInput] = Field(default_factory=list)
