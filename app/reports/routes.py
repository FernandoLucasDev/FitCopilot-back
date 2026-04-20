from __future__ import annotations

from datetime import date

from flask import Blueprint, request

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.jobs.tasks import generate_student_report_job
from app.reports.models import GeneratedReport
from app.reports.services import create_report_request, require_report, serialize_report
from app.students.services import require_student


reports_bp = Blueprint("reports", __name__)


@reports_bp.get("/students/<uuid:student_id>/reports")
@require_auth()
def list_reports(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    items = GeneratedReport.query.filter_by(account_id=auth.account_id, student_id=student_id).order_by(GeneratedReport.created_at.desc()).all()
    return success_response({"items": [serialize_report(item) for item in items]})


@reports_bp.post("/students/<uuid:student_id>/reports")
@require_auth({"owner", "professional", "admin"})
def create_report(student_id):
    auth = current_auth()
    payload = request.get_json() or {}
    report = create_report_request(
        account_id=auth.account_id,
        student_id=student_id,
        actor_user_id=auth.user.id,
        report_type=payload.get("report_type", "progress_summary"),
        period_start=date.fromisoformat(payload["period_start"]) if payload.get("period_start") else None,
        period_end=date.fromisoformat(payload["period_end"]) if payload.get("period_end") else None,
    )
    generate_student_report_job.delay(str(report.id))
    return success_response({"report": serialize_report(report)}, 201)


@reports_bp.get("/reports/<uuid:report_id>")
@require_auth()
def get_report(report_id):
    auth = current_auth()
    report = require_report(auth.account_id, report_id)
    return success_response({"report": serialize_report(report)})
