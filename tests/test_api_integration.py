from __future__ import annotations

from io import BytesIO

from app.extensions import db
from app.auth.models import User
from app.insights.models import AIInsight
from app.reports.models import GeneratedReport
from app.students.models import StudentProfile
from app.workouts.models import WorkoutPlan
from app.jobs.tasks import extract_student_file_job, generate_student_report_job
from app.files.models import StudentFile


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_auth_me_and_workspace_overview(client, seeded_data, auth_headers):
    me = _ok(client.get("/api/v1/auth/me", headers=auth_headers))
    assert me["user"]["email"] == "owner@fitcopilot.dev"
    assert me["account"]["slug"] == "fit-copilot"

    overview = _ok(client.get("/api/v1/workspace/overview", headers=auth_headers))
    assert overview["headline"]["title"]
    assert isinstance(overview["priorities"], list)


def test_professional_password_reset_with_otp(client, seeded_data):
    requested = _ok(
        client.post("/api/v1/auth/password-reset/request", json={"email": "owner@fitcopilot.dev"}),
        202,
    )
    assert requested["status"] == "accepted"
    assert requested["debugCode"]

    updated = _ok(
        client.post(
            "/api/v1/auth/password-reset/verify",
            json={"email": "owner@fitcopilot.dev", "code": requested["debugCode"], "new_password": "nova1234"},
        )
    )
    assert updated["status"] == "password_updated"

    login = _ok(client.post("/api/v1/auth/login", json={"email": "owner@fitcopilot.dev", "password": "nova1234"}))
    assert login["token"]


def test_students_list_panel_create_and_archive(client, auth_headers):
    listing = _ok(client.get("/api/v1/students", headers=auth_headers))
    assert len(listing["items"]) >= 1
    student_id = listing["items"][0]["id"]

    panel = _ok(client.get(f"/api/v1/students/{student_id}/panel", headers=auth_headers))
    assert panel["header"]["name"]
    assert "today" in panel

    created = _ok(
        client.post(
            "/api/v1/students",
            headers=auth_headers,
            json={"full_name": "Novo Aluno", "email": "novo.aluno@fitcopilot.dev", "phone": "+5511999992000", "main_objective_text": "Condicionamento"},
        ),
        201,
    )
    created_id = created["student"]["id"]

    archived = _ok(client.post(f"/api/v1/students/{created_id}/archive", headers=auth_headers))
    assert archived["status"] == "archived"

    with client.application.app_context():
        archived_student = db.session.get(StudentProfile, created_id)
        assert archived_student is not None
        assert archived_student.email == "novo.aluno@fitcopilot.dev"
        assert archived_student.user_id is None
        assert archived_student.archived_at is not None


def test_file_upload_and_report_creation(client, auth_headers, monkeypatch, seeded_data):
    from app.files import routes as file_routes
    from app.reports import routes as report_routes

    monkeypatch.setattr(file_routes.extract_student_file_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(report_routes.generate_student_report_job, "delay", lambda *args, **kwargs: None)

    student_id = str(seeded_data["student"].id)

    upload = _ok(
        client.post(
            f"/api/v1/students/{student_id}/files",
            headers=auth_headers,
            data={
                "title": "Nova avaliacao",
                "file_category": "physical_evaluation",
                "file": (BytesIO(b"avaliacao local"), "avaliacao.txt"),
            },
            content_type="multipart/form-data",
        ),
        201,
    )
    assert upload["file"]["title"] == "Nova avaliacao"

    report = _ok(
        client.post(
            f"/api/v1/students/{student_id}/reports",
            headers=auth_headers,
            json={"report_type": "weekly_summary"},
        ),
        201,
    )
    assert report["report"]["status"] == "pending"

    listed_reports = _ok(client.get(f"/api/v1/students/{student_id}/reports", headers=auth_headers))
    assert len(listed_reports["items"]) >= 1

    with client.application.app_context():
        assert GeneratedReport.query.filter_by(student_id=student_id).count() >= 1


def test_file_processing_report_generation_and_storage_download(client, auth_headers, monkeypatch, seeded_data):
    from app.files import routes as file_routes
    from app.reports import routes as report_routes

    monkeypatch.setattr(file_routes.extract_student_file_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(report_routes.generate_student_report_job, "delay", lambda *args, **kwargs: None)

    student_id = str(seeded_data["student"].id)
    uploaded = _ok(
        client.post(
            f"/api/v1/students/{student_id}/files",
            headers=auth_headers,
            data={
                "title": "Plano alimentar",
                "file_category": "nutrition_evaluation",
                "file": (BytesIO(b"cafe da manha\nalmoco\njantar"), "plano.txt"),
            },
            content_type="multipart/form-data",
        ),
        201,
    )["file"]

    with client.application.app_context():
        extract_student_file_job(str(uploaded["id"]))
        student_file = db.session.get(StudentFile, uploaded["id"])
        assert student_file is not None
        assert student_file.extraction_status == "completed"
        assert student_file.ai_summary

    stored_response = client.get(f"/api/v1/system/storage/{uploaded['url'].split('/api/v1/system/storage/', 1)[1]}")
    assert stored_response.status_code == 200
    assert stored_response.data

    report = _ok(
        client.post(
            f"/api/v1/students/{student_id}/reports",
            headers=auth_headers,
            json={"report_type": "progress_summary"},
        ),
        201,
    )["report"]

    with client.application.app_context():
        generate_student_report_job(report["id"])
        generated = db.session.get(GeneratedReport, report["id"])
        assert generated is not None
        assert generated.status == "completed"
        assert generated.summary_text
        assert generated.file_url


def test_workout_plan_creation_and_activation(client, auth_headers, seeded_data):
    student_id = str(seeded_data["student"].id)

    created = _ok(
        client.post(
            f"/api/v1/students/{student_id}/workout-plans",
            headers=auth_headers,
            json={
                "title": "Lower B",
                "objective": "Hipertrofia",
                "days": [
                    {
                        "label": "Treino B",
                        "order_index": 1,
                        "exercises": [
                            {"exercise_name": "Agachamento", "order_index": 1, "sets_count": 4, "reps_text": "4 x 6"}
                        ],
                    }
                ],
            },
        ),
        201,
    )
    plan_id = created["workoutPlan"]["id"]

    activated = _ok(client.post(f"/api/v1/workout-plans/{plan_id}/activate", headers=auth_headers))
    assert activated["workoutPlan"]["status"] == "active"

    with client.application.app_context():
      active_count = WorkoutPlan.query.filter_by(student_id=student_id, status="active").count()
      assert active_count == 1


def test_student_otp_flow(client, seeded_data):
    email = seeded_data["student"].email
    student_id = str(seeded_data["student"].id)

    with client.application.app_context():
        student = db.session.get(StudentProfile, student_id)
        assert student is not None
        assert student.user_id is None

    requested = _ok(client.post("/api/v1/student-auth/request-otp", json={"email": email}), 202)
    assert requested["status"] in {"accepted", "sent"}
    code = requested["debugCode"]

    verified = _ok(client.post("/api/v1/student-auth/verify-otp", json={"email": email, "code": code}))
    assert verified["token"]

    portal = _ok(client.get("/api/v1/student-portal/me", headers={"Authorization": f"Bearer {verified['token']}"}))
    assert portal["student"]["name"] == seeded_data["student"].full_name
    assert "workoutHistory" in portal
    assert "progress" in portal

    with client.application.app_context():
        student = db.session.get(StudentProfile, student_id)
        assert student is not None
        assert student.user_id is not None
        user = db.session.get(User, student.user_id)
        assert user is not None
        assert user.role == "student"
        assert user.email == email


def test_student_portal_can_register_workout_session(client, seeded_data):
    email = seeded_data["student"].email
    requested = _ok(client.post("/api/v1/student-auth/request-otp", json={"email": email}), 202)
    token = _ok(
        client.post("/api/v1/student-auth/verify-otp", json={"email": email, "code": requested["debugCode"]})
    )["token"]

    created = _ok(
        client.post(
            "/api/v1/student-portal/workout-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "plan_id": str(seeded_data["plan"].id),
                "status": "completed",
                "notes": "Treino concluido com boa energia.",
                "exercises": [
                    {
                        "exercise_name": "Supino reto",
                        "sets_completed": 4,
                        "reps_completed": "4 x 8",
                        "notes": "Carga ajustada",
                    }
                ],
            },
        ),
        201,
    )
    assert created["session"]["status"] == "completed"
    assert created["portal"]["workoutHistory"][0]["status"] == "completed"


def test_apply_insight_and_reflect_status(client, auth_headers, seeded_data):
    insight_id = str(seeded_data["insight"].id)

    applied = _ok(client.post(f"/api/v1/insights/{insight_id}/apply", headers=auth_headers))
    assert applied["insight"]["status"] == "applied"

    with client.application.app_context():
        insight = db.session.get(AIInsight, insight_id)
        assert insight is not None
        assert insight.status == "applied"
