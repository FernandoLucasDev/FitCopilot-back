from __future__ import annotations


def test_core_client_uses_configured_host_header(flask_app, monkeypatch):
    from app.integrations.core_client import core_client

    captured = {}

    class Response:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return Response()

    flask_app.config["CORE_API_URL"] = "http://px_core_app:8000/api/v1"
    flask_app.config["CORE_HOST_HEADER"] = "core.dreamcorelab.cloud"
    monkeypatch.setattr("app.integrations.core_client.requests.request", fake_request)

    with flask_app.app_context():
        core_client.request(method="GET", path="/billing/plans/", token="core-token")

    assert captured["url"] == "http://px_core_app:8000/api/v1/billing/plans/"
    assert captured["headers"]["Host"] == "core.dreamcorelab.cloud"
    assert captured["headers"]["Authorization"] == "Bearer core-token"


def test_core_email_gateway_uses_configured_host_header(flask_app, monkeypatch):
    from app.integrations.core_email import core_email_gateway

    captured = {}

    class Response:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    flask_app.config["CORE_API_URL"] = "http://px_core_app:8000/api/v1"
    flask_app.config["CORE_HOST_HEADER"] = "core.dreamcorelab.cloud"
    monkeypatch.setattr("app.integrations.core_email.requests.post", fake_post)

    with flask_app.app_context():
        core_email_gateway.send_html_email(
            access_token="core-token",
            to_email="aluno@fitcopilot.dev",
            subject="Teste",
            html_content="<p>Teste</p>",
        )

    assert captured["url"] == "http://px_core_app:8000/api/v1/communication/email/send/"
    assert captured["headers"]["Host"] == "core.dreamcorelab.cloud"
    assert captured["headers"]["Authorization"] == "Bearer core-token"
