from __future__ import annotations

from datetime import date, datetime, timezone
from http import HTTPStatus

from flask import current_app

from app.common.api import ApiError
from app.extensions import db
from app.jobs.services import create_audit_log, create_background_job
from app.reports.models import GeneratedReport
from app.students.models import StudentDailySummary
from app.students.services import require_student


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_report_request(*, account_id, student_id, actor_user_id, report_type: str, period_start: date | None, period_end: date | None) -> GeneratedReport:
    require_student(account_id, student_id)
    report = GeneratedReport(
        account_id=account_id,
        student_id=student_id,
        requested_by_user_id=actor_user_id,
        report_type=report_type,
        status="pending",
        period_start=period_start,
        period_end=period_end,
    )
    db.session.add(report)
    db.session.flush()
    create_background_job(
        job_type="generate_student_report_job",
        status="queued",
        payload={"report_id": str(report.id)},
        account_id=account_id,
        student_id=student_id,
        reference_type="generated_report",
        reference_id=report.id,
    )
    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="generated_report",
        entity_id=report.id,
        action="requested",
        new_values={"report_type": report.report_type},
    )
    db.session.commit()
    return report


def require_report(account_id, report_id) -> GeneratedReport:
    report = GeneratedReport.query.filter_by(id=report_id, account_id=account_id).first()
    if report is None:
        raise ApiError("Relatório não encontrado", HTTPStatus.NOT_FOUND)
    return report


def serialize_report(report: GeneratedReport) -> dict:
    return {
        "id": str(report.id),
        "reportType": report.report_type,
        "status": report.status,
        "periodStart": report.period_start.isoformat() if report.period_start else None,
        "periodEnd": report.period_end.isoformat() if report.period_end else None,
        "summaryText": report.summary_text,
        "fileUrl": report.file_url,
        "createdAt": report.created_at.isoformat(),
    }
