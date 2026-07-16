from __future__ import annotations

from io import BytesIO

import requests

from app.extensions import db
from app.auth.models import User
from app.insights.models import AIInsight
from app.physical.models import PhysicalAssessment
from app.reports.models import GeneratedReport
from app.students.models import StudentProfile
from app.workouts.models import WorkoutPlan
from app.jobs.tasks import extract_student_file_job, generate_student_report_job
from app.files.models import StudentFile
from app.whatsapp.models import OutboundMessageDispatch


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


def test_core_register_uses_signup_then_login(flask_app, monkeypatch):
    from app.auth.core_auth_service import core_auth_service
    from app.integrations.core_client import core_client

    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(*, method, path, json=None, **kwargs):
        calls.append((method, path, json))
        if path == "/auth/login/":
            return {"access": "core-access", "refresh": "core-refresh"}
        return {"user": {"id": 12}}

    monkeypatch.setattr(core_client, "request", fake_request)

    with flask_app.app_context():
        payload = core_auth_service.register(
            full_name="Profissional Core",
            email="core@fitcopilot.dev",
            password="abcd1234",
            phone="+5537999999999",
        )

    assert payload["access"] == "core-access"
    assert calls == [
        ("POST", "/auth/signup/", {"email": "core@fitcopilot.dev", "password": "abcd1234"}),
        ("POST", "/auth/login/", {"email": "core@fitcopilot.dev", "password": "abcd1234"}),
    ]


def test_legacy_local_login_provisions_core_membership(client, flask_app, seeded_data, monkeypatch):
    from app.auth.core_auth_service import core_auth_service

    flask_app.config["CORE_API_URL"] = "http://core.test/api/v1"

    response = requests.Response()
    response.status_code = 403
    login_error = requests.HTTPError(response=response)

    monkeypatch.setattr(core_auth_service, "login", lambda **kwargs: (_ for _ in ()).throw(login_error))
    monkeypatch.setattr(
        core_auth_service,
        "register",
        lambda **kwargs: {
            "access": "core-access",
            "refresh": "core-refresh",
            "organizations": [{"id": "core-org-legacy", "name": "Legacy Core"}],
        },
    )

    payload = _ok(client.post("/api/v1/auth/login", json={"email": "owner@fitcopilot.dev", "password": "abcd1234"}))

    assert payload["core"]["hasCoreSession"] is True
    assert payload["core"]["externalOrgId"] == "core-org-legacy"


def test_core_referral_stats_are_normalized_for_frontend(flask_app, monkeypatch):
    from app.integrations.core_client import core_client
    from app.referral.services import referral_gateway

    flask_app.config["CORE_API_URL"] = "http://core.test/api/v1"
    monkeypatch.setattr(
        core_client,
        "request",
        lambda **kwargs: {
            "referral_code": "CORE123",
            "referral_link": "https://core.test?ref=CORE123",
            "total_active_referrals": 2,
            "total_commission_generated": "40.00",
            "total_commission_pending": "20.00",
        },
    )

    with flask_app.app_context():
        stats = referral_gateway.get_stats(token="core-access")

    assert stats["referral_url"] == "https://core.test?ref=CORE123"
    assert stats["active_referrals"] == 2
    assert stats["monthly_credit_brl"] == "20.00"
    assert stats["recent_conversions"] == []


def test_students_list_panel_create_and_archive(client, auth_headers, monkeypatch):
    from app.jobs import tasks
    from app.reports import routes as report_routes

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(report_routes.generate_student_report_job, "delay", lambda *args, **kwargs: None)

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

    updated = _ok(
        client.patch(
            f"/api/v1/students/{created_id}",
            headers=auth_headers,
            json={
                "full_name": "Novo Aluno Editado",
                "email": "novo.editado@fitcopilot.dev",
                "phone": "+5511999992999",
                "main_objective_text": "Hipertrofia",
                "notes": "Prefere treinar cedo.",
            },
        )
    )
    assert updated["student"]["header"]["name"] == "Novo Aluno Editado"
    assert updated["student"]["data"]["notes"] == "Prefere treinar cedo."

    archived = _ok(client.post(f"/api/v1/students/{created_id}/archive", headers=auth_headers))
    assert archived["status"] == "archived"

    active_listing = _ok(client.get("/api/v1/students", headers=auth_headers))
    assert created_id not in {item["id"] for item in active_listing["items"]}

    with client.application.app_context():
        archived_student = db.session.get(StudentProfile, created_id)
        assert archived_student is not None
        assert archived_student.email == "novo.editado@fitcopilot.dev"
        assert archived_student.user_id is None
        assert archived_student.archived_at is not None
        dispatch = OutboundMessageDispatch.query.filter_by(student_id=created_id, message_category="onboarding").first()
        assert dispatch is not None
        message = dispatch.payload_json["interactive"]["body"]
        assert "Agente Fit" in message
        assert "foto ou descrição das refeições" in message
        assert "treinei hoje" in message
        assert "Sua área do aluno" in message
        assert "novo.aluno@fitcopilot.dev" in message

    removable = _ok(
        client.post(
            "/api/v1/students",
            headers=auth_headers,
            json={"full_name": "Aluno Removivel", "email": "remover@fitcopilot.dev", "phone": "+5511999992888"},
        ),
        201,
    )
    removable_id = removable["student"]["id"]

    workout = _ok(
        client.post(
            "/api/v1/workouts",
            headers=auth_headers,
            json={
                "student_id": removable_id,
                "title": "Ficha removivel",
                "objective": "QA",
                "days": [
                    {
                        "label": "Treino A",
                        "order_index": 1,
                        "exercises": [
                            {
                                "exercise_name": "Puxada QA",
                                "order_index": 1,
                                "sets_count": 4,
                                "reps_text": "S1 12-15 | S2 10-12 | S3 8-10",
                                "rest_seconds": 90,
                            }
                        ],
                    }
                ],
            },
        ),
        201,
    )
    _ok(
        client.post(
            f"/api/v1/students/{removable_id}/assign-workout",
            headers=auth_headers,
            json={"plan_id": workout["workoutPlan"]["id"]},
        ),
        201,
    )
    _ok(
        client.post(
            f"/api/v1/students/{removable_id}/physical-assessments",
            headers=auth_headers,
            json={
                "title": "Avaliação removivel",
                "assessment_date": "2026-07-07",
                "weight_kg": 82.5,
                "height_cm": 178,
                "body_fat_percentage": 18,
                "waist_cm": 84,
                "hip_cm": 98,
            },
        ),
        201,
    )
    _ok(
        client.post(
            f"/api/v1/students/{removable_id}/reports",
            headers=auth_headers,
            json={"report_type": "complete"},
        ),
        201,
    )

    deleted = _ok(client.delete(f"/api/v1/students/{removable_id}", headers=auth_headers))
    assert deleted["status"] == "deleted"

    with client.application.app_context():
        assert db.session.get(StudentProfile, removable_id) is None
        assert OutboundMessageDispatch.query.filter_by(student_id=removable_id).count() == 0
        assert WorkoutPlan.query.filter_by(student_id=removable_id).count() == 0
        assert PhysicalAssessment.query.filter_by(student_id=removable_id).count() == 0
        assert GeneratedReport.query.filter_by(student_id=removable_id).count() == 0


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
        assert generated.storage_key.endswith(".pdf")

    report_response = client.get(f"/api/v1/system/storage/{generated.storage_key}")
    assert report_response.status_code == 200
    assert report_response.content_type == "application/pdf"
    assert report_response.data.startswith(b"%PDF")

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

    second = _ok(
        client.post(
            f"/api/v1/students/{student_id}/workout-plans",
            headers=auth_headers,
            json={
                "title": "Upper C",
                "objective": "Forca",
                "days": [
                    {
                        "label": "Treino C",
                        "order_index": 1,
                        "exercises": [
                            {"exercise_name": "Supino reto", "order_index": 1, "sets_count": 4, "reps_text": "4 x 8"}
                        ],
                    }
                ],
            },
        ),
        201,
    )
    second_id = second["workoutPlan"]["id"]
    _ok(client.post(f"/api/v1/students/{student_id}/assign-workout", headers=auth_headers, json={"plan_id": second_id}), 201)

    plans = _ok(client.get(f"/api/v1/students/{student_id}/workout-plans", headers=auth_headers))
    titles = {item["title"]: item["status"] for item in plans["items"]}
    assert "Lower B" in titles
    assert titles["Upper C"] == "active"
    assert titles["Lower B"] != "archived"

    archived = _ok(client.post(f"/api/v1/workout-plans/{second_id}/archive", headers=auth_headers))
    assert archived["workoutPlan"]["status"] == "archived"

    visible_plans = _ok(client.get(f"/api/v1/students/{student_id}/workout-plans", headers=auth_headers))
    assert second_id not in {item["id"] for item in visible_plans["items"]}

    with client.application.app_context():
        archived_plan = db.session.get(WorkoutPlan, second_id)
        assert archived_plan is not None
        assert archived_plan.archived_at is not None
        assert archived_plan.status == "archived"


def test_student_otp_flow(client, seeded_data, monkeypatch):
    from app.jobs import tasks

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    email = seeded_data["student"].email
    student_id = str(seeded_data["student"].id)

    with client.application.app_context():
        student = db.session.get(StudentProfile, student_id)
        assert student is not None
        assert student.user_id is None

    requested = _ok(client.post("/api/v1/student-auth/request-otp", json={"email": email}), 202)
    assert requested["status"] in {"accepted", "sent"}
    assert requested["deliveryChannel"] == "whatsapp"
    code = requested["debugCode"]

    with client.application.app_context():
        dispatch = OutboundMessageDispatch.query.filter_by(student_id=student_id, message_category="student_otp").first()
        assert dispatch is not None
        assert code in dispatch.payload_json["text"]["body"]

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
                        "reps_completed": "4 x 8 · carga 42.5kg",
                        "notes": "Carga ajustada",
                    }
                ],
            },
        ),
        201,
    )
    assert created["session"]["status"] == "completed"
    assert created["portal"]["workoutHistory"][0]["status"] == "completed"
    latest = created["portal"]["exerciseHistory"]["Supino reto"][0]
    assert latest["weightKg"] == 42.5
    assert latest["setsCompleted"] == 4


def test_student_portal_can_start_and_finish_same_workout_session(client, seeded_data):
    email = seeded_data["student"].email
    requested = _ok(client.post("/api/v1/student-auth/request-otp", json={"email": email}), 202)
    token = _ok(
        client.post("/api/v1/student-auth/verify-otp", json={"email": email, "code": requested["debugCode"]})
    )["token"]

    started = _ok(
        client.post(
            "/api/v1/student-portal/workout-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "plan_id": str(seeded_data["plan"].id),
                "status": "pending",
                "notes": "Treino iniciado pelo aluno.",
                "exercises": [],
            },
        ),
        201,
    )
    session_id = started["session"]["id"]
    assert started["session"]["status"] == "pending"

    completed = _ok(
        client.post(
            "/api/v1/student-portal/workout-sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "session_id": session_id,
                "plan_id": str(seeded_data["plan"].id),
                "status": "completed",
                "notes": "Treino finalizado pelo aluno.",
                "exercises": [
                    {
                        "exercise_name": "Supino reto",
                        "sets_completed": 4,
                        "reps_completed": "4 x 8 carga 45kg",
                    }
                ],
            },
        ),
        201,
    )
    assert completed["session"]["id"] == session_id
    assert completed["session"]["status"] == "completed"
    assert completed["portal"]["workoutHistory"][0]["id"] == session_id
    assert completed["portal"]["exerciseHistory"]["Supino reto"][0]["weightKg"] == 45


def test_physical_assessment_can_be_created_from_uploaded_document(client, auth_headers, seeded_data):
    student_id = str(seeded_data["student"].id)
    uploaded = _ok(
        client.post(
            f"/api/v1/students/{student_id}/physical-assessments",
            headers=auth_headers,
            data={
                "title": "Avaliação importada",
                "assessment_file": (
                    BytesIO(
                        b"Peso: 82.5 kg\nAltura: 178 cm\nGordura corporal: 18%\nCintura: 84 cm\nAbdomen: 88 cm\nQuadril: 100 cm"
                    ),
                    "avaliacao-fisica.txt",
                ),
            },
            content_type="multipart/form-data",
        ),
        201,
    )

    assessment = uploaded["assessment"]
    assert assessment["weightKg"] == 82.5
    assert assessment["heightCm"] == 178.0
    assert assessment["bodyFatPercentage"] == 18.0
    assert assessment["measurements"]["waistCm"] == 84.0


def test_apply_insight_and_reflect_status(client, auth_headers, seeded_data):
    insight_id = str(seeded_data["insight"].id)

    applied = _ok(client.post(f"/api/v1/insights/{insight_id}/apply", headers=auth_headers))
    assert applied["insight"]["status"] == "applied"

    with client.application.app_context():
        insight = db.session.get(AIInsight, insight_id)
        assert insight is not None
        assert insight.status == "applied"
