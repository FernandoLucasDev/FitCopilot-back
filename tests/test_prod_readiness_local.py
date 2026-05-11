from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.jobs.models import BackgroundJob
from app.whatsapp.models import OutboundMessageDispatch


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_system_status_requires_owner_and_ops_reports_failures(client, auth_headers, seeded_data):
    unauthenticated = client.get("/api/v1/system/status")
    assert unauthenticated.status_code == 401

    with client.application.app_context():
        db.session.add(
            BackgroundJob(
                account_id=seeded_data["account"].id,
                student_id=seeded_data["student"].id,
                job_type="send_whatsapp_message_job",
                reference_type="whatsapp_dispatch",
                reference_id=seeded_data["student"].id,
                status="failed",
                payload_json={"dispatch_id": "local"},
                error_message="Evolution API disconnected",
            )
        )
        db.session.add(
            OutboundMessageDispatch(
                account_id=seeded_data["account"].id,
                student_id=seeded_data["student"].id,
                message_category="daily_checkin",
                idempotency_key="ops-test-dispatch",
                external_reference="ops-test-dispatch",
                requested_by_service="pytest",
                local_status="failed",
                payload_json={"text": {"body": "Teste"}},
            )
        )
        db.session.commit()

    status = _ok(client.get("/api/v1/system/status", headers=auth_headers))
    assert status["services"]["database"] == "configured"
    assert status["services"]["redis"] == "configured"

    ops = _ok(client.get("/api/v1/system/ops", headers=auth_headers))
    assert ops["health"] == "attention"
    assert ops["jobs"]["byStatus"]["failed"] == 1
    assert ops["whatsapp"]["dispatchesByStatus"]["failed"] == 1
    assert ops["jobs"]["recentFailures"][0]["error"] == "Evolution API disconnected"


def test_ops_marks_stalled_jobs_as_critical(client, auth_headers, seeded_data):
    with client.application.app_context():
        old_job = BackgroundJob(
            account_id=seeded_data["account"].id,
            student_id=seeded_data["student"].id,
            job_type="extract_student_file_job",
            reference_type="student_file",
            reference_id=seeded_data["student"].id,
            status="queued",
            payload_json={},
        )
        db.session.add(old_job)
        db.session.flush()
        old_job.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.session.commit()

    ops = _ok(client.get("/api/v1/system/ops", headers=auth_headers))
    assert ops["health"] == "critical"
    assert ops["jobs"]["stalled24h"] == 1


def test_student_otp_rate_limit(client, flask_app, seeded_data):
    flask_app.config["OTP_RATE_LIMIT_PER_HOUR"] = 2
    payload = {"email": seeded_data["student"].email}
    headers = {"X-Forwarded-For": "203.0.113.10"}

    assert client.post("/api/v1/student-auth/request-otp", json=payload, headers=headers).status_code == 202
    assert client.post("/api/v1/student-auth/request-otp", json=payload, headers=headers).status_code == 202
    limited = client.post("/api/v1/student-auth/request-otp", json=payload, headers=headers)
    assert limited.status_code == 429
    assert "Muitas tentativas" in limited.get_json()["error"]["message"]


def test_professional_password_reset_rate_limit(client, flask_app, seeded_data):
    flask_app.config["PASSWORD_RESET_RATE_LIMIT_PER_HOUR"] = 1
    payload = {"email": seeded_data["owner"].email}
    headers = {"X-Forwarded-For": "203.0.113.11"}

    assert client.post("/api/v1/auth/password-reset/request", json=payload, headers=headers).status_code == 202
    limited = client.post("/api/v1/auth/password-reset/request", json=payload, headers=headers)
    assert limited.status_code == 429


def test_internal_bot_rate_limit(client, flask_app):
    flask_app.config["BOT_RATE_LIMIT_PER_MINUTE"] = 1
    headers = {"X-Bot-Secret": flask_app.config["BOT_INTERNAL_SECRET"], "X-Forwarded-For": "203.0.113.12"}
    payload = {"phoneNumber": "+5537996620448", "messageType": "text", "text": "oi", "phase": "idle"}

    assert client.post("/api/v1/internal/bot/whatsapp/respond", json=payload, headers=headers).status_code == 200
    limited = client.post("/api/v1/internal/bot/whatsapp/respond", json=payload, headers=headers)
    assert limited.status_code == 429
