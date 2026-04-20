from __future__ import annotations

from flask import Blueprint, request

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.files.models import StudentFile
from app.files.services import create_student_file, serialize_file
from app.jobs.tasks import extract_student_file_job
from app.students.services import require_student


files_bp = Blueprint("files", __name__)


@files_bp.get("/students/<uuid:student_id>/files")
@require_auth()
def list_student_files(student_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    items = (
        StudentFile.query.filter_by(student_id=student_id, deleted_at=None)
        .order_by(StudentFile.uploaded_at.desc())
        .all()
    )
    return success_response({"items": [serialize_file(item) for item in items]})


@files_bp.post("/students/<uuid:student_id>/files")
@require_auth({"owner", "professional", "admin"})
def upload_student_file(student_id):
    auth = current_auth()
    title = request.form.get("title", "Arquivo")
    category = request.form.get("file_category", "attachment")
    upload = request.files.get("file")
    if upload is None:
        return success_response({"message": "Arquivo ausente"}, 400)
    student_file = create_student_file(
        account_id=auth.account_id,
        student_id=student_id,
        actor_user_id=auth.user.id,
        title=title,
        file_category=category,
        uploaded_file=upload,
    )
    extract_student_file_job.delay(str(student_file.id))
    return success_response({"file": serialize_file(student_file)}, 201)


@files_bp.get("/students/<uuid:student_id>/files/<uuid:file_id>")
@require_auth()
def get_student_file(student_id, file_id):
    auth = current_auth()
    require_student(auth.account_id, student_id)
    item = StudentFile.query.filter_by(id=file_id, student_id=student_id, deleted_at=None).first_or_404()
    return success_response({"file": serialize_file(item)})
