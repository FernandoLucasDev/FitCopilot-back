from __future__ import annotations

from typing import Literal

from app.common.schemas.base import ApiSchema


PROFESSIONAL_VERTICALS = ("personal_trainer", "nutricionista", "academia")


class UpdateAccountInput(ApiSchema):
    professional_vertical: Literal["personal_trainer", "nutricionista", "academia"]


class UpdateBrandConfigInput(ApiSchema):
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    bot_name: str | None = None
    email_from_name: str | None = None


class UpdateContractInput(ApiSchema):
    sla_tier: str | None = None
    contract_start_at: str | None = None
    contract_end_at: str | None = None
    support_channel: str | None = None
    account_manager_name: str | None = None


class OnboardingRowInput(ApiSchema):
    line: int | None = None
    full_name: str
    email: str
    phone: str | None = None
    target_account_id: str
    professional_id: str
    unit_slug: str | None = None


class CommitOnboardingInput(ApiSchema):
    rows: list[OnboardingRowInput]
