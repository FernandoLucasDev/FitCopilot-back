from __future__ import annotations

import hashlib
import random
import re
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from types import SimpleNamespace

from flask import current_app
from flask_jwt_extended import create_access_token, get_jwt_identity, verify_jwt_in_request

from app.accounts.enterprise_services import resolve_effective_config
from app.accounts.models import Account
from app.common.api import ApiError
from app.common.utils.time import ensure_aware
from app.extensions import db
from app.integrations.core_email import core_email_gateway
from app.students.models import StudentProfile
from app.students.panel_service import get_student_panel
from app.students.portal_models import StudentLoginChallenge
from app.students.services import ensure_student_portal_user
from app.workouts.services import create_workout_session, get_active_workout_for_student, list_student_sessions, summarize_workout_consistency, update_workout_session


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
                subject="Seu codigo de acesso FitCopilot",
                html_content=_build_student_otp_html(student.full_name, code),
            )
            challenge.delivery_status = "sent"
        except Exception:
            challenge.delivery_status = "failed"
    else:
        challenge.delivery_status = "debug"
    db.session.commit()
    expose_debug_code = current_app.config.get("OTP_DEBUG_CODES_ENABLED") or challenge.delivery_status != "sent"
    return {"status": "accepted", "expiresInSeconds": 600, "debugCode": code if expose_debug_code else None}


def verify_student_otp(*, email: str, code: str) -> dict:
    student = StudentProfile.query.filter(StudentProfile.email == email, StudentProfile.archived_at.is_(None)).first()
    if student is None:
        raise ApiError("Codigo invalido", HTTPStatus.UNAUTHORIZED)
    challenge = (
        StudentLoginChallenge.query.filter_by(email=email, consumed_at=None)
        .order_by(StudentLoginChallenge.created_at.desc())
        .first()
    )
    if challenge is None or ensure_aware(challenge.expires_at) < utcnow() or challenge.otp_code_hash != _hash_code(code):
        raise ApiError("Codigo invalido ou expirado", HTTPStatus.UNAUTHORIZED)

    user = ensure_student_portal_user(student)
    challenge.verified_at = utcnow()
    challenge.consumed_at = utcnow()
    user.last_login_at = utcnow()

    token = create_access_token(identity=f"student:{student.id}", additional_claims={"role": "student", "student_id": str(student.id)})
    db.session.commit()
    return {"token": token, "student": build_student_portal_payload(student)}


def require_student_session() -> StudentProfile:
    try:
        verify_jwt_in_request()
    except Exception as exc:
        raise ApiError("Sessao do aluno invalida", HTTPStatus.UNAUTHORIZED) from exc
    identity = get_jwt_identity()
    if not str(identity).startswith("student:"):
        raise ApiError("Sessao do aluno invalida", HTTPStatus.UNAUTHORIZED)
    student_id = str(identity).split(":", 1)[1]
    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        raise ApiError("Aluno nao encontrado", HTTPStatus.NOT_FOUND)
    return student


def build_student_portal_payload(student: StudentProfile) -> dict:
    panel = get_student_panel(student)
    consistency = panel.get("workoutConsistency") or summarize_workout_consistency(student)
    sessions = panel.get("workoutHistory") or list_student_sessions(account_id=student.account_id, student_id=student.id)[:6]
    active_workout = get_active_workout_for_student(student.id)
    last_session = sessions[0] if sessions else None
    progress = _build_student_progress(consistency=consistency, sessions=sessions)
    day_reading = _build_student_day_reading(consistency=consistency)
    account = Account.query.filter_by(id=student.account_id).first()
    brand = resolve_effective_config(account, "brand_config") if account else {}
    return {
        "student": {
            "id": str(student.id),
            "name": student.full_name,
            "email": student.email,
            "goal": panel["header"]["goal"],
        },
        "brand": brand,
        "brandName": account.name if account else None,
        "workout": panel["workout"],
        "workoutConsistency": consistency,
        "workoutHistory": sessions,
        "exerciseHistory": _build_exercise_history(sessions=sessions),
        "lastSession": last_session,
        "progress": progress,
        "dayReading": day_reading,
        "availableWorkoutDays": [
            {"id": day["id"], "label": day["label"], "exerciseCount": len(day["exercises"])}
            for day in (panel["workout"]["days"] if panel.get("workout") and panel["workout"] else [])
        ]
        if panel.get("workout")
        else [],
        "activeWorkoutPlanId": str(active_workout.id) if active_workout else None,
        "files": panel["files"],
        "reports": panel["reports"],
        "nutritionPlan": (panel.get("nutrition") or {}).get("plan"),
        "wearable": panel.get("wearable"),
    }


def _build_exercise_history(*, sessions: list[dict]) -> dict:
    history: dict[str, list[dict]] = {}
    for session in sessions:
        session_date = session.get("date")
        for exercise in session.get("exercises", []):
            name = str(exercise.get("exerciseName") or "").strip()
            if not name:
                continue
            history.setdefault(name, []).append(
                {
                    "sessionId": session.get("id"),
                    "date": session_date,
                    "status": session.get("status"),
                    "setsCompleted": exercise.get("setsCompleted"),
                    "repsCompleted": exercise.get("repsCompleted"),
                    "weightKg": _extract_weight_kg(exercise.get("repsCompleted"), exercise.get("notes")),
                    "notes": exercise.get("notes"),
                }
            )
    return {name: items[:5] for name, items in history.items()}


def _extract_weight_kg(*values) -> float | None:
    for value in values:
        if not value:
            continue
        match = re.search(r"(\d+(?:[,.]\d+)?)\s*kg\b", str(value), flags=re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def create_student_portal_session(*, student: StudentProfile, payload) -> dict:
    plan = get_active_workout_for_student(student.id)
    plan_id = payload.plan_id or (str(plan.id) if plan else None)
    if not plan_id:
        raise ApiError("Nenhuma ficha ativa encontrada para registrar a sessao.", HTTPStatus.CONFLICT)

    data = SimpleNamespace(
        student_id=str(student.id),
        plan_id=plan_id,
        date=datetime.fromisoformat(payload.date).date() if payload.date else utcnow().date(),
        status=payload.status,
        notes=payload.notes,
        exercises=payload.exercises,
    )
    if payload.session_id:
        session = update_workout_session(account_id=student.account_id, actor_user_id=None, session_id=payload.session_id, data=data)
    else:
        session = create_workout_session(account_id=student.account_id, actor_user_id=None, data=data)
    return {
        "session": {
            "id": str(session.id),
            "date": session.session_date.isoformat(),
            "status": session.status,
            "notes": session.notes,
        },
        "portal": build_student_portal_payload(student),
    }


def _build_student_progress(*, consistency: dict, sessions: list[dict]) -> dict:
    completed = consistency["completedCount"]
    streak = 0
    for session in sessions:
        if session["status"] != "completed":
            break
        streak += 1

    total_logged_sets = sum((exercise.get("setsCompleted") or 0) for session in sessions for exercise in session.get("exercises", []))
    return {
        "headline": f"Voce treinou {completed}x nesta semana",
        "secondary": (
            "Consistencia melhorando."
            if consistency["trend"] == "up"
            else "Queda de consistencia detectada."
            if consistency["trend"] == "down"
            else "Mantenha o ritmo da semana."
        ),
        "streak": streak,
        "totalLoggedSets": total_logged_sets,
    }


def _build_student_day_reading(*, consistency: dict) -> str:
    if consistency["trend"] == "up":
        return "Voce manteve consistencia nos ultimos treinos. Continue assim."
    if consistency["trend"] == "down":
        return "Voce ficou abaixo do ritmo ideal. Voltar hoje ajuda a manter o progresso."
    return "Seu ritmo esta estavel. Registrar o treino de hoje ajuda o acompanhamento a ficar mais preciso."


def _build_student_otp_html(student_name: str, code: str) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;background:#0f172a;color:#e2e8f0;padding:24px;border-radius:16px;">
      <h2 style="margin:0 0 8px 0;">Seu codigo FitCopilot</h2>
      <p style="margin:0 0 18px 0;">Ola {student_name.split()[0]}, use o codigo abaixo para entrar na sua area do aluno.</p>
      <div style="font-size:32px;letter-spacing:8px;font-weight:700;background:#111827;border-radius:12px;padding:16px;text-align:center;">{code}</div>
      <p style="margin-top:18px;font-size:12px;color:#94a3b8;">Esse codigo expira em 10 minutos.</p>
    </div>
    """
