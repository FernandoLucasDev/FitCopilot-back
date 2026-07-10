from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus

from flask import current_app

from app.common.api import ApiError
from app.extensions import db
from app.files.models import StudentFile
from app.jobs.services import create_audit_log, create_background_job
from app.students.services import require_student


ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_student_file(*, account_id, student_id, actor_user_id, title: str, file_category: str, uploaded_file) -> StudentFile:
    student = require_student(account_id, student_id)
    if uploaded_file.mimetype not in ALLOWED_MIME_TYPES:
        raise ApiError("Tipo de arquivo não permitido", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    content = uploaded_file.read()
    if not content:
        raise ApiError("Arquivo vazio", HTTPStatus.BAD_REQUEST)
    if len(content) > current_app.config["MAX_CONTENT_LENGTH"]:
        raise ApiError("Arquivo excede o tamanho máximo permitido", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    storage = current_app.extensions["storage_provider"]
    stored = storage.save(f"accounts/{account_id}/students/{student_id}", uploaded_file.filename, content, uploaded_file.mimetype)

    student_file = StudentFile(
        account_id=account_id,
        student_id=student.id,
        uploaded_by_user_id=actor_user_id,
        file_category=file_category,
        title=title,
        original_filename=uploaded_file.filename,
        storage_key=stored.storage_key,
        file_url=stored.file_url,
        mime_type=stored.mime_type,
        file_size_bytes=stored.size,
        extraction_status="pending",
        uploaded_at=utcnow(),
    )
    db.session.add(student_file)
    db.session.flush()

    create_audit_log(
        account_id=account_id,
        actor_user_id=actor_user_id,
        entity_type="student_file",
        entity_id=student_file.id,
        action="uploaded",
        new_values={"title": student_file.title, "file_category": student_file.file_category},
    )
    create_background_job(
        job_type="extract_student_file_job",
        status="queued",
        payload={"student_file_id": str(student_file.id)},
        account_id=account_id,
        student_id=student_id,
        reference_type="student_file",
        reference_id=student_file.id,
    )
    db.session.commit()
    return student_file


def save_meal_photo(*, student, content: bytes, mime_type: str) -> StudentFile:
    storage = current_app.extensions["storage_provider"]
    filename = f"meal-{utcnow().strftime('%Y%m%d%H%M%S')}.{'png' if 'png' in mime_type else 'jpg'}"
    stored = storage.save(f"accounts/{student.account_id}/students/{student.id}/meals", filename, content, mime_type)

    uploader_id = student.primary_professional.user_id
    student_file = StudentFile(
        account_id=student.account_id,
        student_id=student.id,
        uploaded_by_user_id=uploader_id,
        file_category="meal_photo",
        title=f"Foto de refeição — {utcnow().strftime('%d/%m/%Y %H:%M')}",
        original_filename=filename,
        storage_key=stored.storage_key,
        file_url=stored.file_url,
        mime_type=stored.mime_type,
        file_size_bytes=stored.size,
        extraction_status="completed",
        uploaded_at=utcnow(),
    )
    db.session.add(student_file)
    db.session.flush()
    return student_file


def serialize_file(item: StudentFile) -> dict:
    return {
        "id": str(item.id),
        "title": item.title,
        "fileCategory": item.file_category,
        "originalFilename": item.original_filename,
        "mimeType": item.mime_type,
        "fileSizeBytes": item.file_size_bytes,
        "url": item.file_url,
        "extractionStatus": item.extraction_status,
        "aiSummary": item.ai_summary,
        "uploadedAt": item.uploaded_at.isoformat(),
    }
