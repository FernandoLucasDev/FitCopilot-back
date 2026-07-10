from __future__ import annotations

from pydantic import Field

from app.common.schemas.base import ApiSchema


class SendStudentWhatsAppMessageInput(ApiSchema):
    message_text: str = Field(min_length=1, max_length=2000)
    message_type: str = Field(default="text")
    subject_hint: str | None = None


class StudentAutomationConfigInput(ApiSchema):
    daily_checkin_active: bool | None = None
    daily_checkin_hour: int | None = None
    reminder_active: bool | None = None
    reengagement_active: bool | None = None
    preferred_window_start: int | None = None
    preferred_window_end: int | None = None
    nutrition_no_log_active: bool | None = None
    nutrition_over_target_active: bool | None = None


class SimulateInboundWhatsAppInput(ApiSchema):
    phone: str | None = None
    message_type: str = Field(default="text")
    text_body: str | None = None
    media_json: dict | None = None
    raw_payload_json: dict | None = None
