from __future__ import annotations

from flask import Blueprint, request

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.physical.services import (
    create_physical_assessment,
    create_physical_assessment_from_document,
    list_physical_assessments,
    require_assessment,
    send_physical_assessment_whatsapp_summary,
    serialize_assessment,
)


physical_bp = Blueprint("physical", __name__)


@physical_bp.get("/students/<uuid:student_id>/physical-assessments")
@require_auth({"owner", "professional", "admin"})
def list_student_physical_assessments(student_id):
    auth = current_auth()
    items = list_physical_assessments(account_id=auth.account_id, student_id=student_id)
    return success_response({"items": [serialize_assessment(item) for item in items]})


@physical_bp.post("/students/<uuid:student_id>/physical-assessments")
@require_auth({"owner", "professional", "admin"})
def create_student_physical_assessment(student_id):
    auth = current_auth()
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        data = request.form.to_dict()
        files = {key: request.files.get(key) for key in request.files.keys()}
        assessment_file = request.files.get("assessment_file")
        if assessment_file is not None:
            assessment = create_physical_assessment_from_document(
                account_id=auth.account_id,
                student_id=student_id,
                actor_user_id=auth.user.id,
                data=data,
                uploaded_file=assessment_file,
            )
            return success_response({"assessment": serialize_assessment(assessment)}, 201)
    else:
        data = request.get_json(silent=False) or {}
        files = {}
    assessment = create_physical_assessment(
        account_id=auth.account_id,
        student_id=student_id,
        actor_user_id=auth.user.id,
        data=data,
        files=files,
    )
    return success_response({"assessment": serialize_assessment(assessment)}, 201)


@physical_bp.get("/students/<uuid:student_id>/physical-assessments/<uuid:assessment_id>")
@require_auth({"owner", "professional", "admin"})
def get_student_physical_assessment(student_id, assessment_id):
    auth = current_auth()
    assessment = require_assessment(auth.account_id, student_id, assessment_id)
    return success_response({"assessment": serialize_assessment(assessment)})


@physical_bp.post("/students/<uuid:student_id>/physical-assessments/<uuid:assessment_id>/send-whatsapp-summary")
@require_auth({"owner", "professional", "admin"})
def send_student_physical_assessment_summary(student_id, assessment_id):
    auth = current_auth()
    dispatch = send_physical_assessment_whatsapp_summary(
        account_id=auth.account_id,
        student_id=student_id,
        assessment_id=assessment_id,
        actor_user_id=auth.user.id,
    )
    return success_response({"dispatch": {"id": str(dispatch.id), "status": dispatch.local_status}}, 202)
