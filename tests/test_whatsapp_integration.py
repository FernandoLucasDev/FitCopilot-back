from __future__ import annotations

from app.extensions import db
from app.jobs.tasks import process_inbound_whatsapp_message_job
from app.students.models import StudentDailySignal
from app.whatsapp.models import InboundMessageRecord, OutboundMessageDispatch
from app.whatsapp.services import perform_dispatch


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
