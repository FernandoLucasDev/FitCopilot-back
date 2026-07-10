from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.extensions import db
from app.wearables.models import WearableConnection, WearableDataPoint
from app.wearables.services import sync_student_wearable_data


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def _student_token(client, email: str) -> str:
    requested = _ok(client.post("/api/v1/student-auth/request-otp", json={"email": email}), 202)
    return _ok(client.post("/api/v1/student-auth/verify-otp", json={"email": email, "code": requested["debugCode"]}))["token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_wearable_connect_returns_fake_authorize_url(client, seeded_data):
    token = _student_token(client, seeded_data["student"].email)
    data = _ok(client.post("/api/v1/student-portal/wearable/connect", headers=_headers(token), json={}))
    assert "authorizeUrl" in data
    assert "/api/v1/wearable/strava/callback" in data["authorizeUrl"]
    assert "state=" in data["authorizeUrl"]


def test_wearable_full_connect_flow_via_callback(client, flask_app, seeded_data):
    token = _student_token(client, seeded_data["student"].email)
    connect_data = _ok(client.post("/api/v1/student-portal/wearable/connect", headers=_headers(token), json={}))
    parsed = urlparse(connect_data["authorizeUrl"])
    query = parse_qs(parsed.query)
    code = query["code"][0]
    state = query["state"][0]

    callback_response = client.get(f"/api/v1/wearable/strava/callback?code={code}&state={state}", follow_redirects=False)
    assert callback_response.status_code == 302
    assert "wearable=connected" in callback_response.headers["Location"]

    summary = _ok(client.get("/api/v1/student-portal/wearable/summary", headers=_headers(token)))
    assert summary["connected"] is True
    assert summary["source"] == "strava"
    assert summary["lastSyncedAt"] is not None

    with flask_app.app_context():
        connection = WearableConnection.query.filter_by(student_id=seeded_data["student"].id).first()
        assert connection is not None
        assert connection.revoked_at is None
        points = WearableDataPoint.query.filter_by(student_id=seeded_data["student"].id).all()
        assert len(points) > 0


def test_wearable_callback_with_invalid_state_redirects_error_and_creates_nothing(client, flask_app, seeded_data):
    response = client.get("/api/v1/wearable/strava/callback?code=fake-bogus&state=does-not-exist", follow_redirects=False)
    assert response.status_code == 302
    assert "wearable=error" in response.headers["Location"]

    with flask_app.app_context():
        assert WearableConnection.query.filter_by(student_id=seeded_data["student"].id).count() == 0


def test_wearable_disconnect(client, flask_app, seeded_data):
    token = _student_token(client, seeded_data["student"].email)
    connect_data = _ok(client.post("/api/v1/student-portal/wearable/connect", headers=_headers(token), json={}))
    parsed = urlparse(connect_data["authorizeUrl"])
    query = parse_qs(parsed.query)
    client.get(f"/api/v1/wearable/strava/callback?code={query['code'][0]}&state={query['state'][0]}")

    disconnect_data = _ok(client.post("/api/v1/student-portal/wearable/disconnect", headers=_headers(token), json={}))
    assert disconnect_data["connected"] is False

    summary = _ok(client.get("/api/v1/student-portal/wearable/summary", headers=_headers(token)))
    assert summary["connected"] is False

    with flask_app.app_context():
        connection = WearableConnection.query.filter_by(student_id=seeded_data["student"].id).first()
        assert connection is not None
        assert connection.revoked_at is not None


def test_health_score_valid_without_wearable_data(flask_app, seeded_data):
    from app.operations.services import calculate_student_health_score

    with flask_app.app_context():
        result = calculate_student_health_score(seeded_data["student"])
        assert 0 <= result.score <= 100
        assert result.factors["wearable_active_minutes_avg"] is None


def test_health_score_reflects_wearable_activity(client, flask_app, seeded_data):
    from app.operations.services import calculate_student_health_score

    token = _student_token(client, seeded_data["student"].email)
    connect_data = _ok(client.post("/api/v1/student-portal/wearable/connect", headers=_headers(token), json={}))
    parsed = urlparse(connect_data["authorizeUrl"])
    query = parse_qs(parsed.query)
    client.get(f"/api/v1/wearable/strava/callback?code={query['code'][0]}&state={query['state'][0]}")

    with flask_app.app_context():
        result = calculate_student_health_score(seeded_data["student"])
        assert result.factors["wearable_active_minutes_avg"] is not None
        assert any("wearable" in reason for reason in result.reason.split("; "))


def test_wearable_sync_is_idempotent(client, flask_app, seeded_data):
    token = _student_token(client, seeded_data["student"].email)
    connect_data = _ok(client.post("/api/v1/student-portal/wearable/connect", headers=_headers(token), json={}))
    parsed = urlparse(connect_data["authorizeUrl"])
    query = parse_qs(parsed.query)
    client.get(f"/api/v1/wearable/strava/callback?code={query['code'][0]}&state={query['state'][0]}")

    with flask_app.app_context():
        connection = WearableConnection.query.filter_by(student_id=seeded_data["student"].id).first()
        first_count = WearableDataPoint.query.filter_by(student_id=seeded_data["student"].id).count()
        assert first_count > 0

        result = sync_student_wearable_data(connection)
        assert result["status"] == "ok"
        assert result["created"] == 0

        second_count = WearableDataPoint.query.filter_by(student_id=seeded_data["student"].id).count()
        assert second_count == first_count


def _make_connection(flask_app, seeded_data, *, connected_days_ago: int = 0):
    from datetime import timedelta

    from app.common.security.crypto import encrypt_secret
    from app.wearables.services import utcnow

    with flask_app.app_context():
        connection = WearableConnection(
            student_id=seeded_data["student"].id,
            account_id=seeded_data["account"].id,
            source="strava",
            access_token_encrypted=encrypt_secret("fake-access-test"),
            connected_at=utcnow() - timedelta(days=connected_days_ago),
        )
        db.session.add(connection)
        db.session.commit()
        return connection.id


def _add_data_point(flask_app, seeded_data, *, metric_type: str, value: float, days_ago: int, external_id: str):
    from datetime import timedelta

    from app.wearables.services import utcnow

    with flask_app.app_context():
        db.session.add(
            WearableDataPoint(
                student_id=seeded_data["student"].id,
                account_id=seeded_data["account"].id,
                source="strava",
                metric_type=metric_type,
                value=value,
                unit="minutes" if metric_type == "active_minutes" else "hours",
                recorded_at=utcnow() - timedelta(days=days_ago),
                synced_at=utcnow(),
                external_id=external_id,
                payload_json={},
            )
        )
        db.session.commit()


def test_wearable_alert_activity_drop(flask_app, seeded_data):
    from app.wearables.alerts import evaluate_wearable_alerts

    _make_connection(flask_app, seeded_data, connected_days_ago=20)
    for day in range(5, 17):
        _add_data_point(flask_app, seeded_data, metric_type="active_minutes", value=45, days_ago=day, external_id=f"baseline-{day}")
    for day in range(0, 3):
        _add_data_point(flask_app, seeded_data, metric_type="active_minutes", value=5, days_ago=day, external_id=f"recent-{day}")

    with flask_app.app_context():
        student = seeded_data["student"]
        decision = evaluate_wearable_alerts(student)
        assert decision is not None
        assert decision.rule_type == "wearable_activity_drop"


def test_wearable_alert_inactivity(flask_app, seeded_data):
    from app.wearables.alerts import evaluate_wearable_alerts

    _make_connection(flask_app, seeded_data, connected_days_ago=10)

    with flask_app.app_context():
        student = seeded_data["student"]
        decision = evaluate_wearable_alerts(student)
        assert decision is not None
        assert decision.rule_type == "wearable_inactivity"


def test_wearable_alert_cooldown_prevents_duplicate(flask_app, seeded_data):
    from app.wearables.alerts import evaluate_wearable_alerts

    _make_connection(flask_app, seeded_data, connected_days_ago=10)

    with flask_app.app_context():
        student = seeded_data["student"]
        first = evaluate_wearable_alerts(student)
        assert first is not None
        second = evaluate_wearable_alerts(student)
        assert second is None


def test_wearable_no_alert_without_connection(flask_app, seeded_data):
    from app.wearables.alerts import evaluate_wearable_alerts

    with flask_app.app_context():
        assert evaluate_wearable_alerts(seeded_data["student"]) is None
