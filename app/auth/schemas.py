from __future__ import annotations

from pydantic import EmailStr, Field

from app.common.schemas.base import ApiSchema


class RegisterInput(ApiSchema):
    account_name: str = Field(min_length=2, max_length=160)
    account_email: EmailStr
    account_phone: str | None = None
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    professional_type: str = "personal_trainer"


class LoginInput(ApiSchema):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
