from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.extensions import db
from app.files.models import StudentFile
from app.jobs.models import BackgroundJob
from app.students.models import StudentProfile
from app.whatsapp.models import InboundMessageRecord, OutboundMessageDispatch, WhatsAppSession


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _count_by_status(model, column, account_id=None) -> dict[str, int]:
    query = db.session.query(column, func.count()).group_by(column)
    if account_id is not None and hasattr(model, "account_id"):
        query = query.filter(model.account_id == account_id)
    return {str(status): int(count) for status, count in query.all()}


def build_ops_snapshot(*, account_id=None) -> dict:
    cutoff = utcnow() - timedelta(hours=24)
    jobs_query = BackgroundJob.query
    dispatch_query = OutboundMessageDispatch.query
    inbound_query = InboundMessageRecord.query
    file_query = StudentFile.query
    session_query = WhatsAppSession.query
    student_query = StudentProfile.query.filter(StudentProfile.archived_at.is_(None))
    if account_id is not None:
        jobs_query = jobs_query.filter(BackgroundJob.account_id == account_id)
        dispatch_query = dispatch_query.filter(OutboundMessageDispatch.account_id == account_id)
        inbound_query = inbound_query.filter(InboundMessageRecord.account_id == account_id)
        file_query = file_query.filter(StudentFile.account_id == account_id)
        session_query = session_query.filter(WhatsAppSession.account_id == account_id)
        student_query = student_query.filter(StudentProfile.account_id == account_id)

    failed_jobs = jobs_query.filter(BackgroundJob.status == "failed").order_by(BackgroundJob.updated_at.desc()).limit(10).all()
    failed_dispatches = dispatch_query.filter(OutboundMessageDispatch.local_status == "failed").order_by(OutboundMessageDispatch.updated_at.desc()).limit(10).all()
    stalled_jobs = jobs_query.filter(BackgroundJob.status.in_(["queued", "processing"]), BackgroundJob.created_at < cutoff).count()

    students = student_query.all()
    students_without_phone = len([student for student in students if not student.phone])
    students_without_professional = len([student for student in students if not student.primary_professional_id])

    return {
        "generatedAt": utcnow().isoformat(),
        "health": _derive_health(
            failed_jobs=jobs_query.filter(BackgroundJob.status == "failed").count(),
            failed_dispatches=dispatch_query.filter(OutboundMessageDispatch.local_status == "failed").count(),
            stalled_jobs=stalled_jobs,
        ),
        "jobs": {
            "byStatus": _count_by_status(BackgroundJob, BackgroundJob.status, account_id),
            "stalled24h": stalled_jobs,
            "recentFailures": [
                {
                    "id": str(item.id),
                    "type": item.job_type,
                    "error": item.error_message,
                    "createdAt": item.created_at.isoformat(),
                    "updatedAt": item.updated_at.isoformat(),
                }
                for item in failed_jobs
            ],
        },
        "whatsapp": {
            "dispatchesByStatus": _count_by_status(OutboundMessageDispatch, OutboundMessageDispatch.local_status, account_id),
            "inboundByStatus": _count_by_status(InboundMessageRecord, InboundMessageRecord.processing_status, account_id),
            "activeSessions": session_query.filter(WhatsAppSession.status == "active").count(),
            "recentFailures": [
                {
                    "id": str(item.id),
                    "studentId": str(item.student_id),
                    "category": item.message_category,
                    "updatedAt": item.updated_at.isoformat(),
                }
                for item in failed_dispatches
            ],
        },
        "files": {
            "byExtractionStatus": _count_by_status(StudentFile, StudentFile.extraction_status, account_id),
            "failedExtractions": file_query.filter(StudentFile.extraction_status == "failed").count(),
        },
        "students": {
            "active": len(students),
            "withoutPhone": students_without_phone,
            "withoutProfessional": students_without_professional,
        },
    }


def _derive_health(*, failed_jobs: int, failed_dispatches: int, stalled_jobs: int) -> str:
    if stalled_jobs or failed_jobs >= 5 or failed_dispatches >= 5:
        return "critical"
    if failed_jobs or failed_dispatches:
        return "attention"
    return "ok"
