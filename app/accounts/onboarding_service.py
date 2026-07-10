from __future__ import annotations

import csv
import io
import re

from app.accounts.models import Account, ProfessionalProfile
from app.auth.models import User
from app.extensions import db
from app.jobs.services import create_audit_log
from app.students.models import StudentProfile

REQUIRED_COLUMNS = ("full_name", "email")
OPTIONAL_COLUMNS = ("phone", "professional_email", "unit_slug")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _resolve_target_account(account: Account, unit_slug: str | None) -> tuple[Account | None, str | None]:
    if not unit_slug:
        return account, None
    unit = Account.query.filter_by(parent_account_id=account.id, slug=unit_slug, deleted_at=None).first()
    if unit is None:
        return None, f"unidade '{unit_slug}' não encontrada nesta rede"
    return unit, None


def _resolve_professional(target_account: Account, professional_email: str | None) -> tuple[ProfessionalProfile | None, str | None]:
    if professional_email:
        professional = (
            ProfessionalProfile.query.join(User, ProfessionalProfile.user_id == User.id)
            .filter(ProfessionalProfile.account_id == target_account.id, User.email == professional_email)
            .first()
        )
        if professional is None:
            return None, f"profissional '{professional_email}' não encontrado nesta unidade"
        return professional, None

    fallback = (
        ProfessionalProfile.query.filter_by(account_id=target_account.id)
        .order_by(ProfessionalProfile.created_at.asc())
        .first()
    )
    if fallback is None:
        return None, "conta/unidade sem nenhum profissional cadastrado para atribuir o aluno"
    return fallback, None


def parse_onboarding_csv(*, account: Account, content: bytes) -> dict:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"validRows": [], "errors": [{"line": 0, "message": "arquivo não está em UTF-8"}]}

    reader = csv.DictReader(io.StringIO(text))
    missing = [col for col in REQUIRED_COLUMNS if col not in (reader.fieldnames or [])]
    if missing:
        return {"validRows": [], "errors": [{"line": 0, "message": f"colunas obrigatórias ausentes: {', '.join(missing)}"}]}

    valid_rows: list[dict] = []
    errors: list[dict] = []
    seen_emails: set[str] = set()

    for index, row in enumerate(reader, start=2):  # linha 1 é o cabeçalho
        full_name = (row.get("full_name") or "").strip()
        email = (row.get("email") or "").strip().lower()
        phone = (row.get("phone") or "").strip() or None
        professional_email = (row.get("professional_email") or "").strip().lower() or None
        unit_slug = (row.get("unit_slug") or "").strip() or None

        if not full_name:
            errors.append({"line": index, "message": "nome completo ausente"})
            continue
        if not email or not EMAIL_RE.match(email):
            errors.append({"line": index, "message": "e-mail ausente ou inválido"})
            continue
        if email in seen_emails:
            errors.append({"line": index, "message": f"e-mail '{email}' duplicado no arquivo"})
            continue

        target_account, target_error = _resolve_target_account(account, unit_slug)
        if target_error:
            errors.append({"line": index, "message": target_error})
            continue

        if StudentProfile.query.filter_by(account_id=target_account.id, email=email, archived_at=None).first():
            errors.append({"line": index, "message": f"e-mail '{email}' já cadastrado nesta unidade"})
            continue

        professional, professional_error = _resolve_professional(target_account, professional_email)
        if professional_error:
            errors.append({"line": index, "message": professional_error})
            continue

        seen_emails.add(email)
        valid_rows.append(
            {
                "line": index,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "target_account_id": str(target_account.id),
                "professional_id": str(professional.id),
                "unit_slug": unit_slug,
            }
        )

    return {"validRows": valid_rows, "errors": errors}


def commit_onboarding_import(*, account_id, actor_user_id, rows: list[dict]) -> dict:
    created: list[dict] = []
    errors: list[dict] = []
    try:
        for row in rows:
            existing = StudentProfile.query.filter_by(
                account_id=row["target_account_id"], email=row["email"], archived_at=None
            ).first()
            if existing:
                errors.append({"line": row.get("line"), "message": f"e-mail '{row['email']}' já cadastrado nesta unidade"})
                continue
            student = StudentProfile(
                account_id=row["target_account_id"],
                primary_professional_id=row["professional_id"],
                full_name=row["full_name"],
                email=row["email"],
                phone=row.get("phone"),
                status="new",
                adherence_score=50,
                adherence_trend="stable",
                last_signal_summary="Importado via onboarding em lote",
            )
            db.session.add(student)
            db.session.flush()
            created.append({"line": row.get("line"), "studentId": str(student.id), "fullName": student.full_name})

        if errors:
            db.session.rollback()
            return {"status": "rolled_back", "created": [], "errors": errors}

        create_audit_log(
            account_id=account_id,
            actor_user_id=actor_user_id,
            entity_type="account",
            entity_id=account_id,
            action="bulk_onboarding_import",
            new_values={"createdCount": len(created)},
        )
        db.session.commit()
        return {"status": "completed", "created": created, "errors": []}
    except Exception:
        db.session.rollback()
        raise
