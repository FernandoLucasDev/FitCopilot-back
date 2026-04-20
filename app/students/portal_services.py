from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

from flask_jwt_extended import create_access_token, get_jwt_identity, verify_jwt_in_request
from werkzeug.exceptions import Unauthorized

from app.common.api import ApiError
from app.common.utils.time import ensure_aware
from app.extensions import db
from app.integrations.core_email import core_email_gateway
from app.students.models import StudentProfile
from app.students.panel_service import get_student_panel
from app.students.portal_models import StudentLoginChallenge
from app.workouts.services import serialize_workout_plan


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def request_student_otp(*, email: str, requested_by_ip: str | None = None) -> dict:
    student = StudentProfile.query.filter(StudentProfile.email == email, StudentProfile.archived_at.is_(None)).first()
    if student is None:
        return {"status": "accepted"}

    code = _generate_code()
    challenge = StudentLoginChallenge(
        student_id=student.id,
        email=email,
        otp_code_hash=_hash_code(code),
        delivery_status="pending",
        expires_at=utcnow() + timedelta(minutes=10),
        requested_by_ip=requested_by_ip,
    )
    db.session.add(challenge)
    db.session.flush()

    account = student.account
    owner_user = account.users[0] if account and account.users else None
    if owner_user and owner_user.core_access_token:
        try:
            core_email_gateway.send_html_email(
                access_token=owner_user.core_access_token,
                to_email=email,
                subject="Seu código de acesso FitCopilot",
                html_content=_build_student_otp_html(student.full_name, code),
            )
            challenge.delivery_status = "sent"
        except Exception:
            challenge.delivery_status = "failed"
    else:
        challenge.delivery_status = "sent"
    db.session.commit()
    return {"status": "accepted", "expiresInSeconds": 600, "debugCode": code if challenge.delivery_status != "sent" else None}


def verify_student_otp(*, email: str, code: str) -> dict:
    student = StudentProfile.query.filter(StudentProfile.email == email, StudentProfile.archived_at.is_(None)).first()
    if student is None:
        raise ApiError("Código inválido", HTTPStatus.UNAUTHORIZED)
    challenge = (
        StudentLoginChallenge.query.filter_by(email=email, consumed_at=None)
        .order_by(StudentLoginChallenge.created_at.desc())
        .first()
    )
    if challenge is None or ensure_aware(challenge.expires_at) < utcnow() or challenge.otp_code_hash != _hash_code(code):
        raise ApiError("Código inválido ou expirado", HTTPStatus.UNAUTHORIZED)
    challenge.verified_at = utcnow()
    challenge.consumed_at = utcnow()
    token = create_access_token(identity=f"student:{student.id}", additional_claims={"role": "student", "student_id": str(student.id)})
    db.session.commit()
    return {"token": token, "student": build_student_portal_payload(student)}


def require_student_session() -> StudentProfile:
    try:
        verify_jwt_in_request()
    except Exception as exc:
        raise ApiError("Sessão do aluno inválida", HTTPStatus.UNAUTHORIZED) from exc
    identity = get_jwt_identity()
    if not str(identity).startswith("student:"):
        raise ApiError("Sessão do aluno inválida", HTTPStatus.UNAUTHORIZED)
    student_id = str(identity).split(":", 1)[1]
    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        raise ApiError("Aluno não encontrado", HTTPStatus.NOT_FOUND)
    return student


def build_student_portal_payload(student: StudentProfile) -> dict:
    panel = get_student_panel(student)
    return {
        "student": {
            "id": str(student.id),
            "name": student.full_name,
            "email": student.email,
            "goal": panel["header"]["goal"],
        },
        "workout": panel["workout"],
        "files": panel["files"],
        "reports": panel["reports"],
    }


def _build_student_otp_html(student_name: str, code: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;background:#0f172a;color:#e2e8f0;padding:24px;border-radius:16px;">
      <h2 style="margin:0 0 8px 0;">Seu código FitCopilot</h2>
      <p style="margin:0 0 18px 0;">Olá {student_name.split()[0]}, use o código abaixo para entrar na sua área do aluno.</p>
      <div style="font-size:32px;letter-spacing:8px;font-weight:700;background:#111827;border-radius:12px;padding:16px;text-align:center;">{code}</div>
      <p style="margin-top:18px;font-size:12px;color:#94a3b8;">Esse código expira em 10 minutos.</p>
    </div>
    """
