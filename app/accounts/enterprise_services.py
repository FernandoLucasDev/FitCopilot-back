from __future__ import annotations

from dataclasses import dataclass, field

from app.accounts.models import Account, AccountMembership
from app.operations.services import latest_operational_score
from app.students.models import StudentProfile


def list_child_accounts(network_account_id) -> list[Account]:
    return (
        Account.query.filter_by(parent_account_id=network_account_id, deleted_at=None)
        .order_by(Account.name.asc())
        .all()
    )


def is_enterprise_unit(account: Account) -> bool:
    return account.account_type == "unit" and account.parent_account_id is not None


def is_network(account: Account) -> bool:
    return account.account_type == "network"


def resolve_effective_config(account: Account, key: str) -> dict:
    """Merges the parent network's config dict with the account's own (own wins field by field)."""
    own = getattr(account, key, None) or {}
    if account.parent_account_id is None:
        return dict(own)
    parent = Account.query.filter_by(id=account.parent_account_id, deleted_at=None).first()
    parent_config = getattr(parent, key, None) or {} if parent else {}
    merged = dict(parent_config)
    merged.update(own)
    return merged


def resolve_professional_scope_filter(auth) -> str | None:
    """Returns the ProfessionalProfile.id to filter students by, when the caller is
    a plain PROFESSIONAL member of an enterprise unit account. Returns None for
    every other case (studio/professional accounts, managers, network owners) —
    preserving today's "see everyone in the account" behavior everywhere else.
    """
    if auth.member_role != "PROFESSIONAL":
        return None
    account = Account.query.filter_by(id=auth.account_id).first()
    if account is None or account.account_type != "unit":
        return None
    professional = getattr(auth.user, "professional_profile", None)
    return str(professional.id) if professional else None


@dataclass
class EnterpriseScope:
    level: str  # "network" | "unit" | "none"
    visible_account_ids: list[str] = field(default_factory=list)
    role: str | None = None


def resolve_enterprise_scope(user, account: Account) -> EnterpriseScope:
    """Determines what a user can see for a given account, accounting for network-level access."""
    if account is None:
        return EnterpriseScope(level="none")

    direct_membership = AccountMembership.query.filter_by(
        account_id=account.id, user_id=user.id, status="ACTIVE", deleted_at=None
    ).first()

    if account.account_type == "network":
        if direct_membership and direct_membership.role == "NETWORK_OWNER":
            child_ids = [str(child.id) for child in list_child_accounts(account.id)]
            return EnterpriseScope(level="network", visible_account_ids=[str(account.id)] + child_ids, role="NETWORK_OWNER")
        return EnterpriseScope(level="none")

    if account.account_type == "unit":
        if direct_membership and direct_membership.role in {"UNIT_MANAGER", "PROFESSIONAL", "OWNER", "ADMIN"}:
            return EnterpriseScope(level="unit", visible_account_ids=[str(account.id)], role=direct_membership.role)
        if account.parent_account_id is not None:
            parent_membership = AccountMembership.query.filter_by(
                account_id=account.parent_account_id, user_id=user.id, status="ACTIVE", deleted_at=None
            ).first()
            if parent_membership and parent_membership.role == "NETWORK_OWNER":
                return EnterpriseScope(level="unit", visible_account_ids=[str(account.id)], role="NETWORK_OWNER")
        return EnterpriseScope(level="none")

    return EnterpriseScope(level="none")


def _unit_summary(unit: Account) -> dict:
    students = StudentProfile.query.filter_by(account_id=unit.id, archived_at=None).all()
    attention_count = 0
    for student in students:
        operational = latest_operational_score(student)
        if operational["status"] in {"attention", "cooling", "risk"}:
            attention_count += 1
    students_count = len(students)
    retention_rate = round(((students_count - attention_count) / students_count) * 100) if students_count else 100
    return {
        "unitId": str(unit.id),
        "unitName": unit.name,
        "studentsCount": students_count,
        "attentionCount": attention_count,
        "retentionRate": retention_rate,
    }


def get_network_dashboard(network_account_id) -> dict:
    network = Account.query.filter_by(id=network_account_id, deleted_at=None).first()
    units = list_child_accounts(network_account_id)
    unit_summaries = [_unit_summary(unit) for unit in units]

    average_retention = (
        round(sum(item["retentionRate"] for item in unit_summaries) / len(unit_summaries))
        if unit_summaries
        else 0
    )
    for item in unit_summaries:
        item["churnAlert"] = item["retentionRate"] < average_retention

    ranked = sorted(unit_summaries, key=lambda item: item["retentionRate"], reverse=True)
    return {
        "networkId": str(network_account_id),
        "networkName": network.name if network else None,
        "unitsCount": len(ranked),
        "studentsTotal": sum(item["studentsCount"] for item in ranked),
        "averageRetentionRate": average_retention,
        "units": ranked,
    }
