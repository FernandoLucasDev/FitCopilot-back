from __future__ import annotations

from flask import Blueprint, request

from app.accounts.enterprise_services import resolve_professional_scope_filter
from app.common.api import success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth
from app.students.panel_service import get_student_panel
from app.students.schemas import CreateStudentInput, UpdateStudentInput
from app.students.services import (
    archive_student,
    create_student,
    delete_student,
    list_students_for_workspace,
    require_student,
    update_student,
)


students_bp = Blueprint("students", __name__)


@students_bp.get("/students")
@require_auth({"owner", "professional", "admin"})
def list_students():
    auth = current_auth()
    return success_response(
        {
            "items": list_students_for_workspace(
                account_id=auth.account_id,
                search=request.args.get("search"),
                status=request.args.get("status"),
                primary_professional_id=resolve_professional_scope_filter(auth),
            )
        }
    )


@students_bp.post("/students")
@require_auth({"owner", "professional", "admin"})
def create_student_endpoint():
    auth = current_auth()
    payload = parse_json(CreateStudentInput)
    student = create_student(
        account_id=auth.account_id,
        professional_id=auth.user.professional_profile.id,
        actor_user_id=auth.user.id,
        data=payload,
    )
    return success_response({"student": get_student_panel(student)["header"]}, 201)


@students_bp.get("/students/<uuid:student_id>")
@require_auth()
def get_student(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response({"student": get_student_panel(student)})


@students_bp.patch("/students/<uuid:student_id>")
@require_auth({"owner", "professional", "admin"})
def patch_student(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    payload = parse_json(UpdateStudentInput)
    student = update_student(student=student, actor_user_id=auth.user.id, data=payload)
    return success_response({"student": get_student_panel(student)})


@students_bp.post("/students/<uuid:student_id>/archive")
@require_auth({"owner", "professional", "admin"})
def archive_student_endpoint(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    archive_student(student=student, actor_user_id=auth.user.id)
    return success_response({"id": str(student.id), "status": student.status})


@students_bp.delete("/students/<uuid:student_id>")
@require_auth({"owner", "professional", "admin"})
def delete_student_endpoint(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    deleted_id = str(student.id)
    delete_student(student=student, actor_user_id=auth.user.id)
    return success_response({"id": deleted_id, "status": "deleted"})


@students_bp.get("/students/<uuid:student_id>/panel")
@require_auth()
def student_panel(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    return success_response(get_student_panel(student))


@students_bp.get("/students/<uuid:student_id>/interactions")
@require_auth()
def list_interactions(student_id):
    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    panel = get_student_panel(student)
    return success_response({"items": panel["interactions"]})


@students_bp.post("/students/<uuid:student_id>/interactions")
@require_auth({"owner", "professional", "admin"})
def create_interaction(student_id):
    from datetime import datetime, timezone

    from app.extensions import db
    from app.students.models import StudentInteraction

    auth = current_auth()
    student = require_student(auth.account_id, student_id)
    payload = request.get_json() or {}
    interaction = StudentInteraction(
        account_id=auth.account_id,
        student_id=student.id,
        interaction_type=payload.get("interaction_type", "manual_note"),
        channel=payload.get("channel", "manual"),
        title=payload["title"],
        body=payload.get("body"),
        created_by_user_id=auth.user.id,
        interaction_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(interaction)
    db.session.commit()
    return success_response({"id": str(interaction.id)}, 201)
