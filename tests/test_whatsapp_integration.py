from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.jobs.tasks import process_inbound_whatsapp_message_job
from app.students.models import StudentDailySignal
from app.whatsapp.models import InboundMessageRecord, OutboundMessageDispatch
from app.whatsapp.services import check_pending_workout_sessions, perform_dispatch
from app.workouts.models import WorkoutSession


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_whatsapp_onboarding_and_manual_dispatch(client, auth_headers, seeded_data, monkeypatch):
    from app.jobs import tasks
    from app.integrations import core_messaging_client as client_module

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        client_module.core_messaging_client,
        "send_interactive_message",
        lambda **kwargs: {"public_id": "core-msg-1", "channel_account_id": "wa-acc-1"},
    )

    student_id = str(seeded_data["student"].id)
    response = _ok(client.post(f"/api/v1/students/{student_id}/whatsapp/onboard", headers=auth_headers), 202)
    dispatch_id = response["dispatch"]["id"]

    with client.application.app_context():
        dispatch = db.session.get(OutboundMessageDispatch, dispatch_id)
        assert dispatch is not None
        assert dispatch.message_category == "onboarding"
        assert dispatch.local_status == "queued"
        body = dispatch.payload_json["interactive"]["body"]
        assert "Área do aluno" in body or "área do aluno" in body
        assert seeded_data["student"].email in body

        perform_dispatch(dispatch_id)
        dispatch = db.session.get(OutboundMessageDispatch, dispatch_id)
        assert dispatch is not None
        assert dispatch.local_status == "sent"
        assert dispatch.core_message_public_id == "core-msg-1"


def test_whatsapp_checkin_and_inbound_processing(client, auth_headers, seeded_data, monkeypatch):
    from app.jobs import tasks
    from app.integrations import core_messaging_client as client_module

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.process_inbound_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        client_module.core_messaging_client,
        "send_interactive_message",
        lambda **kwargs: {"public_id": "core-msg-2", "channel_account_id": "wa-acc-2"},
    )
    monkeypatch.setattr(
        client_module.core_messaging_client,
        "send_text_message",
        lambda **kwargs: {"public_id": "core-msg-3", "channel_account_id": "wa-acc-2"},
    )

    student_id = str(seeded_data["student"].id)
    checkin = _ok(client.post(f"/api/v1/students/{student_id}/whatsapp/send-checkin", headers=auth_headers), 202)
    assert checkin["dispatch"]["status"] == "queued"

    inbound = _ok(
        client.post(
            f"/api/v1/students/{student_id}/whatsapp/inbound",
            headers=auth_headers,
            json={"message_type": "text", "text_body": "Sim"},
        ),
        202,
    )

    with client.application.app_context():
        process_inbound_whatsapp_message_job(inbound["inbound"]["id"])
        record = db.session.get(InboundMessageRecord, inbound["inbound"]["id"])
        assert record is not None
        assert record.processing_status == "completed"
        signal = (
            StudentDailySignal.query.filter_by(student_id=student_id, source="whatsapp")
            .order_by(StudentDailySignal.created_at.desc())
            .first()
        )
        assert signal is not None
        assert signal.title == "Aluno confirmou que vai treinar hoje"


def test_whatsapp_workout_delivery_sends_student_portal_link(client, auth_headers, seeded_data, monkeypatch):
    from app.jobs import tasks

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    client.application.config["STUDENT_PORTAL_URL"] = "http://127.0.0.1:3000/aluno"
    student_id = str(seeded_data["student"].id)

    response = _ok(client.post(f"/api/v1/students/{student_id}/whatsapp/send-workout", headers=auth_headers), 202)

    with client.application.app_context():
        dispatch = db.session.get(OutboundMessageDispatch, response["dispatch"]["id"])
        assert dispatch is not None
        assert dispatch.message_category == "workout_delivery"
        assert dispatch.payload_json["message_type"] == "text"
        body = dispatch.payload_json["text"]["body"]
        assert "http://127.0.0.1:3000/aluno" in body
        assert "registrar a carga de cada exercício" in body



def test_pending_workout_session_sends_completion_check_after_two_hours(client, seeded_data, monkeypatch):
    from app.jobs import tasks

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    with client.application.app_context():
        session = WorkoutSession(
            student_id=seeded_data["student"].id,
            plan_id=seeded_data["plan"].id,
            session_date=date.today(),
            status="pending",
            notes="Treino iniciado.",
        )
        db.session.add(session)
        db.session.commit()
        session.created_at = datetime.now(timezone.utc) - timedelta(hours=2, minutes=5)
        db.session.commit()

        result = check_pending_workout_sessions(now=datetime.now(timezone.utc))
        assert result["prompted"] >= 1
        dispatch = OutboundMessageDispatch.query.filter_by(
            related_entity_type="workout_session",
            related_entity_id=str(session.id),
            message_category="workout_completion_check",
        ).first()
        assert dispatch is not None
        body = dispatch.payload_json["text"]["body"]
        assert "terminou?" in body
        assert "sim" in body.lower()


def test_positive_whatsapp_reply_completes_pending_workout_session(client, auth_headers, seeded_data, monkeypatch):
    from app.jobs import tasks
    from app.integrations import core_messaging_client as client_module

    monkeypatch.setattr(tasks.process_inbound_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        client_module.core_messaging_client,
        "send_text_message",
        lambda **kwargs: {"public_id": "core-msg-workout-done", "channel_account_id": "wa-acc-3"},
    )
    with client.application.app_context():
        session = WorkoutSession(
            student_id=seeded_data["student"].id,
            plan_id=seeded_data["plan"].id,
            session_date=date.today(),
            status="pending",
            notes="Treino iniciado.",
        )
        db.session.add(session)
        db.session.commit()
        session_id = str(session.id)

    student_id = str(seeded_data["student"].id)
    inbound = _ok(
        client.post(
            f"/api/v1/students/{student_id}/whatsapp/inbound",
            headers=auth_headers,
            json={"message_type": "text", "text_body": "sim"},
        ),
        202,
    )
    with client.application.app_context():
        process_inbound_whatsapp_message_job(inbound["inbound"]["id"])
        session = db.session.get(WorkoutSession, session_id)
        assert session is not None
        assert session.status == "completed"

def test_whatsapp_status_and_history_endpoints(client, auth_headers, seeded_data, monkeypatch):
    from app.jobs import tasks

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    student_id = str(seeded_data["student"].id)

    _ok(
        client.post(
            f"/api/v1/students/{student_id}/whatsapp/send-message",
            headers=auth_headers,
            json={"message_text": "Mensagem de teste"},
        ),
        202,
    )

    status = _ok(client.get(f"/api/v1/whatsapp/students/{student_id}/status", headers=auth_headers))
    assert status["channelStatus"] == "ready"
    assert isinstance(status["automations"], list)

    history = _ok(client.get(f"/api/v1/students/{student_id}/whatsapp/history", headers=auth_headers))
    assert len(history["outbound"]) >= 1
    with client.application.app_context():
        dispatch = (
            OutboundMessageDispatch.query.filter_by(student_id=student_id, message_category="manual_message")
            .order_by(OutboundMessageDispatch.created_at.desc())
            .first()
        )
        assert dispatch is not None
        body = dispatch.payload_json["text"]["body"]
        assert "Olá, Joao! Seu personal deixou um recado:" in body
        assert "Mensagem de teste" in body
        assert "Se quiser alinhar algum detalhe" in body


def test_end_of_day_report_uses_daily_meals_and_is_idempotent(client, auth_headers, seeded_data, monkeypatch):
    from app.jobs import tasks

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)

    student = seeded_data["student"]
    today = date.today()
    with client.application.app_context():
        db.session.add(
            StudentDailySignal(
                account_id=student.account_id,
                student_id=student.id,
                signal_date=today,
                signal_type="meal",
                source="whatsapp",
                title="Refeição registrada",
                body="Arroz, feijão, bife, salada e batata frita",
                payload_json={"estimated_calories": 820, "calorie_range": {"min": 700, "max": 950}},
                created_at=datetime.now(timezone.utc),
            )
        )
        db.session.add(
            StudentDailySignal(
                account_id=student.account_id,
                student_id=student.id,
                signal_date=today,
                signal_type="workout",
                source="whatsapp",
                title="Treino registrado",
                body="Treino concluído",
                payload_json={},
                created_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()

    student_id = str(student.id)
    first = _ok(client.post(f"/api/v1/students/{student_id}/whatsapp/send-daily-report", headers=auth_headers), 202)
    second = _ok(client.post(f"/api/v1/students/{student_id}/whatsapp/send-daily-report", headers=auth_headers), 202)
    assert first["dispatch"]["id"] == second["dispatch"]["id"]

    with client.application.app_context():
        dispatches = OutboundMessageDispatch.query.filter_by(student_id=student_id, message_category="daily_report").all()
        assert len(dispatches) == 1
        body = dispatches[0].payload_json["text"]["body"]
        assert "Fechamento do dia" in body
        assert "Hoje registrei 1 refeição." in body
        assert "Total estimado: entre 700 e 950 kcal." in body
        assert "Para amanhã:" in body
