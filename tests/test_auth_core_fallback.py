from __future__ import annotations

from http import HTTPStatus

import requests


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def test_login_falls_back_to_local_session_when_core_returns_bad_request(client, flask_app, seeded_data, monkeypatch):
    from app.auth.core_auth_service import core_auth_service
    from app.extensions import db

    flask_app.config["CORE_API_URL"] = "http://core.test/api/v1"
    seeded_data["owner"].core_access_token = None
    seeded_data["owner"].core_refresh_token = None
    db.session.commit()

    response = requests.Response()
    response.status_code = HTTPStatus.BAD_REQUEST
    login_error = requests.HTTPError(response=response)

    register_called = False

    def fail_register(**kwargs):
        nonlocal register_called
        register_called = True
        raise AssertionError("Core signup should not run when login returned a non-recoverable bad request.")

    monkeypatch.setattr(core_auth_service, "login", lambda **kwargs: (_ for _ in ()).throw(login_error))
    monkeypatch.setattr(core_auth_service, "register", fail_register)

    payload = _ok(client.post("/api/v1/auth/login", json={"email": "owner@fitcopilot.dev", "password": "abcd1234"}))

    assert payload["token"]
    assert payload["user"]["email"] == "owner@fitcopilot.dev"
    assert payload["core"]["hasCoreSession"] is False
    assert register_called is False
