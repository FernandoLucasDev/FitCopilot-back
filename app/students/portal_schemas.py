from __future__ import annotations

from pydantic import EmailStr, Field

from app.common.schemas.base import ApiSchema


class StudentOtpRequestInput(ApiSchema):
    email: EmailStr


class StudentOtpVerifyInput(ApiSchema):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)
