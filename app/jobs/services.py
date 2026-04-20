from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.extensions import db
from app.jobs.models import AuditLog, BackgroundJob


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_audit_log(
    *,
    account_id,
    actor_user_id,
    entity_type: str,
    entity_id,
    action: str,
    old_values: dict | None = None,
    new_values: dict | None = None,
) -> AuditLog:
    log = AuditLog(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_values_json=old_values,
        new_values_json=new_values,
        created_at=utcnow(),
    )
    db.session.add(log)
    return log


def create_background_job(
    *,
    job_type: str,
    status: str,
    payload: dict,
    account_id=None,
    student_id=None,
    reference_type: str | None = None,
    reference_id=None,
) -> BackgroundJob:
    job = BackgroundJob(
        account_id=account_id,
        student_id=student_id,
        job_type=job_type,
        status=status,
        payload_json=payload,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db.session.add(job)
    db.session.flush()
    return job


def finish_background_job(job: BackgroundJob, *, status: str, result: dict | None = None, error_message: str | None = None) -> None:
    job.status = status
    job.result_json = result
    job.error_message = error_message
    job.completed_at = utcnow() if status in {"completed", "failed"} else None
