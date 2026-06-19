from __future__ import annotations

from datetime import date

from app.files.models import StudentFile
from app.messaging.models import SuggestedMessage
from app.reports.models import GeneratedReport


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_remaining_system_billing_ai_and_student_surface(client, auth_headers, seeded_data):
    student_id = str(seeded_data["student"].id)

    assert _ok(client.get("/api/v1/health"))["status"] == "ok"
    assert _ok(client.get("/api/v1/system/status", headers=auth_headers))["status"] == "ok"
    assert _ok(client.get("/api/v1/system/ops", headers=auth_headers))["generatedAt"]
    assert _ok(client.get("/api/v1/billing/entitlements/me", headers=auth_headers))["plan_code"] == "FREE"
    assert _ok(client.get("/api/v1/billing/checkout-config", headers=auth_headers))["mode"] == "mock"
    assert _ok(client.get(f"/api/v1/students/{student_id}", headers=auth_headers))["student"]["header"]["name"]

    created_interaction = _ok(
        client.post(
            f"/api/v1/students/{student_id}/interactions",
            headers=auth_headers,
            json={"title": "QA smoke", "body": "Endpoint surface", "interaction_type": "manual_note"},
        ),
        201,
    )
    assert created_interaction["id"]
    assert len(_ok(client.get(f"/api/v1/students/{student_id}/interactions", headers=auth_headers))["items"]) >= 1

    ai_context = {
        "studentId": student_id,
        "student_name": seeded_data["student"].full_name,
        "filename": "qa.txt",
        "content": "Sinais QA para sumarizacao.",
        "signals": [{"title": "Sinal de QA"}],
        "interactions": [],
    }
    for endpoint in ("overview", "student-day", "message-suggestion", "file-summary", "progress-report"):
        result = _ok(client.post(f"/api/v1/ai/{endpoint}", headers=auth_headers, json=ai_context))
        assert result["status"]


def test_remaining_files_reports_workouts_insights_and_messages_surface(client, auth_headers, seeded_data):
    student_id = str(seeded_data["student"].id)
    plan_id = str(seeded_data["plan"].id)

    assert len(_ok(client.get(f"/api/v1/students/{student_id}/files", headers=auth_headers))["items"]) >= 1
    file_id = str(StudentFile.query.filter_by(student_id=student_id).first().id)
    assert _ok(client.get(f"/api/v1/students/{student_id}/files/{file_id}", headers=auth_headers))["file"]["id"] == file_id

    assert len(_ok(client.get(f"/api/v1/students/{student_id}/reports", headers=auth_headers))["items"]) >= 1
    report_id = str(GeneratedReport.query.filter_by(student_id=student_id).first().id)
    assert _ok(client.get(f"/api/v1/reports/{report_id}", headers=auth_headers))["report"]["id"] == report_id

    assert len(_ok(client.get("/api/v1/workouts", headers=auth_headers))["items"]) >= 1
    assert _ok(client.get(f"/api/v1/students/{student_id}/active-workout", headers=auth_headers))["workoutPlan"]["id"] == plan_id
    assert _ok(client.get(f"/api/v1/students/{student_id}/workout-plan", headers=auth_headers))["workoutPlan"]["id"] == plan_id
    assert len(_ok(client.get(f"/api/v1/students/{student_id}/workout-plans", headers=auth_headers))["items"]) >= 1
    updated_plan = _ok(
        client.patch(
            f"/api/v1/workout-plans/{plan_id}",
            headers=auth_headers,
            json={"notes": "QA endpoint update"},
        )
    )
    assert updated_plan["workoutPlan"]["description"] == "QA endpoint update"
    session = _ok(
        client.post(
            "/api/v1/workout-sessions",
            headers=auth_headers,
            json={
                "student_id": student_id,
                "plan_id": plan_id,
                "date": date.today().isoformat(),
                "status": "completed",
                "exercises": [{"exercise_name": "Supino reto", "sets_completed": 4, "reps_completed": "4 x 8"}],
            },
        ),
        201,
    )
    assert session["session"]["status"] == "completed"
    assert len(_ok(client.get(f"/api/v1/students/{student_id}/sessions", headers=auth_headers))["items"]) >= 1

    assert len(_ok(client.get(f"/api/v1/students/{student_id}/insights", headers=auth_headers))["items"]) >= 1
    dismissed = _ok(client.post(f"/api/v1/insights/{seeded_data['insight'].id}/dismiss", headers=auth_headers))
    assert dismissed["insight"]["status"] == "dismissed"

    message = SuggestedMessage.query.filter_by(student_id=student_id).first()
    assert message is not None
    suggestion_id = str(message.id)
    assert len(_ok(client.get(f"/api/v1/students/{student_id}/suggested-messages", headers=auth_headers))["items"]) >= 1
    assert _ok(client.post(f"/api/v1/suggested-messages/{suggestion_id}/copy", headers=auth_headers))["message"]["status"] == "copied"
    assert _ok(
        client.post(
            f"/api/v1/suggested-messages/{suggestion_id}/edit",
            headers=auth_headers,
            json={"edited_message_text": "Mensagem QA editada"},
        )
    )["message"]["status"] == "edited"
    assert _ok(client.post(f"/api/v1/suggested-messages/{suggestion_id}/dismiss", headers=auth_headers))["message"]["status"] == "dismissed"


def test_remaining_whatsapp_and_physical_surface(client, auth_headers, seeded_data, monkeypatch):
    from app.jobs import tasks

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    student_id = str(seeded_data["student"].id)
    message = SuggestedMessage.query.filter_by(student_id=student_id).first()
    assert message is not None
    suggestion_id = str(message.id)

    assert _ok(client.get("/api/v1/whatsapp/status", headers=auth_headers))["channel"] == "whatsapp"
    assert len(_ok(client.get(f"/api/v1/students/{student_id}/whatsapp/suggestions", headers=auth_headers))["items"]) >= 1
    assert _ok(
        client.post(
            f"/api/v1/students/{student_id}/whatsapp/suggestions/{suggestion_id}/edit",
            headers=auth_headers,
            json={"message_text": "Sugestao QA ajustada"},
        )
    )["suggestion"]["status"] == "edited"
    assert _ok(
        client.post(
            f"/api/v1/students/{student_id}/whatsapp/suggestions/{suggestion_id}/send",
            headers=auth_headers,
        ),
        202,
    )["dispatch"]["status"] == "queued"
    assert _ok(
        client.post(
            f"/api/v1/students/{student_id}/whatsapp/suggestions/{suggestion_id}/dismiss",
            headers=auth_headers,
        )
    )["suggestion"]["status"] == "dismissed"
    assert isinstance(_ok(client.get(f"/api/v1/students/{student_id}/whatsapp/automations", headers=auth_headers))["items"], list)
    assert isinstance(
        _ok(
            client.patch(
                f"/api/v1/students/{student_id}/whatsapp/automations",
                headers=auth_headers,
                json={"daily_checkin_active": True, "daily_checkin_hour": 8},
            )
        )["items"],
        list,
    )

    created = _ok(
        client.post(
            f"/api/v1/students/{student_id}/physical-assessments",
            headers=auth_headers,
            json={"title": "QA fisica", "assessment_date": "2026-05-21", "weight_kg": 80, "height_cm": 178},
        ),
        201,
    )["assessment"]
    assessment_id = created["id"]
    assert len(_ok(client.get(f"/api/v1/students/{student_id}/physical-assessments", headers=auth_headers))["items"]) >= 1
    assert (
        _ok(
            client.get(
                f"/api/v1/students/{student_id}/physical-assessments/{assessment_id}",
                headers=auth_headers,
            )
        )["assessment"]["id"]
        == assessment_id
    )
    assert _ok(
        client.post(
            f"/api/v1/students/{student_id}/physical-assessments/{assessment_id}/send-whatsapp-summary",
            headers=auth_headers,
        ),
        202,
    )["dispatch"]["status"] == "queued"
