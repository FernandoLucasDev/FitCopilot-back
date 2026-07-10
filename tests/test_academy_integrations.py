from __future__ import annotations

from app.events.models import EventType, StudentEvent
from app.extensions import db
from app.integrations.academy.models import AcademyWebhookLog, ExternalSystemMapping


WEBHOOK_SECRET = "fitcopilot-academy-dev-secret"


def _webhook_url(provider: str, account_id: str) -> str:
    return f"/api/v1/integrations/academy/{provider}/webhook/{account_id}"


def _payload(external_student_id: str, external_event_id: str, event_type: str = EventType.ACADEMY_CHECKIN_DETECTED) -> dict:
    return {
        "external_student_id": external_student_id,
        "external_event_id": external_event_id,
        "event_type": event_type,
        "occurred_at": "2026-07-12T10:00:00+00:00",
    }


def test_webhook_rejects_missing_or_wrong_secret(client, seeded_data):
    response = client.post(_webhook_url("generic", str(seeded_data["account"].id)), json=_payload("ext-1", "evt-1"))
    assert response.status_code == 401

    response = client.post(
        _webhook_url("generic", str(seeded_data["account"].id)),
        json=_payload("ext-1", "evt-1"),
        headers={"X-Academy-Webhook-Secret": "wrong-secret"},
    )
    assert response.status_code == 401


def test_webhook_unknown_account_returns_404(client):
    response = client.post(
        _webhook_url("generic", "00000000-0000-0000-0000-000000000000"),
        json=_payload("ext-1", "evt-1"),
        headers={"X-Academy-Webhook-Secret": WEBHOOK_SECRET},
    )
    assert response.status_code == 404


def test_webhook_without_mapping_marks_unmapped_and_does_not_error(client, flask_app, seeded_data):
    account = seeded_data["account"]
    response = client.post(
        _webhook_url("generic", str(account.id)),
        json=_payload("unknown-external-id", "evt-unmapped-1"),
        headers={"X-Academy-Webhook-Secret": WEBHOOK_SECRET},
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "unmapped"
    assert data["eventId"] is None

    with flask_app.app_context():
        log = AcademyWebhookLog.query.filter_by(provider="generic", external_event_id="evt-unmapped-1").first()
        assert log is not None
        assert log.status == "unmapped"
        assert StudentEvent.query.filter_by(account_id=account.id).count() == 0


def test_webhook_with_mapping_creates_student_event(client, flask_app, seeded_data):
    account = seeded_data["account"]
    student = seeded_data["student"]

    with flask_app.app_context():
        db.session.add(
            ExternalSystemMapping(
                account_id=account.id,
                provider="generic",
                external_student_id="ext-42",
                student_id=student.id,
            )
        )
        db.session.commit()

    response = client.post(
        _webhook_url("generic", str(account.id)),
        json=_payload("ext-42", "evt-mapped-1"),
        headers={"X-Academy-Webhook-Secret": WEBHOOK_SECRET},
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["status"] == "processed"
    assert data["eventId"] is not None

    with flask_app.app_context():
        events = StudentEvent.query.filter_by(account_id=account.id, event_type=EventType.ACADEMY_CHECKIN_DETECTED).all()
        assert len(events) == 1
        assert str(events[0].student_id) == str(student.id)
        assert events[0].source == "academy"


def test_webhook_repeated_external_event_id_does_not_duplicate(client, flask_app, seeded_data):
    account = seeded_data["account"]
    student = seeded_data["student"]

    with flask_app.app_context():
        db.session.add(
            ExternalSystemMapping(
                account_id=account.id,
                provider="generic",
                external_student_id="ext-99",
                student_id=student.id,
            )
        )
        db.session.commit()

    payload = _payload("ext-99", "evt-repeat-1")
    first = client.post(
        _webhook_url("generic", str(account.id)),
        json=payload,
        headers={"X-Academy-Webhook-Secret": WEBHOOK_SECRET},
    )
    assert first.get_json()["data"]["status"] == "processed"

    second = client.post(
        _webhook_url("generic", str(account.id)),
        json=payload,
        headers={"X-Academy-Webhook-Secret": WEBHOOK_SECRET},
    )
    assert second.status_code == 200
    assert second.get_json()["data"]["status"] == "duplicate"

    with flask_app.app_context():
        events = StudentEvent.query.filter_by(account_id=account.id, event_type=EventType.ACADEMY_CHECKIN_DETECTED).all()
        assert len(events) == 1
        logs = AcademyWebhookLog.query.filter_by(provider="generic", external_event_id="evt-repeat-1").all()
        assert len(logs) == 1
