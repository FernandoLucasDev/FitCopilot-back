from __future__ import annotations

import requests


def _raise_core_400(**kwargs):
    response = requests.Response()
    response.status_code = 400
    response.url = "http://px_core_app:8000/api/v1/billing/plans/"
    raise requests.HTTPError("400 Client Error: Bad Request", response=response)


def test_billing_read_endpoints_fallback_when_core_token_is_missing(client, auth_headers, seeded_data, flask_app):
    flask_app.config["CORE_API_URL"] = "http://px_core_app:8000/api/v1"
    seeded_data["owner"].core_access_token = None
    seeded_data["account"].current_plan_code = "PRO"

    plans = client.get("/api/v1/billing/plans", headers=auth_headers)
    subscription = client.get("/api/v1/billing/subscriptions/me", headers=auth_headers)
    entitlements = client.get("/api/v1/billing/entitlements/me", headers=auth_headers)

    assert plans.status_code == 200, plans.get_data(as_text=True)
    assert subscription.status_code == 200, subscription.get_data(as_text=True)
    assert entitlements.status_code == 200, entitlements.get_data(as_text=True)
    assert subscription.get_json()["data"]["planCode"] == "PRO"
    assert entitlements.get_json()["data"]["plan_code"] == "PRO"


def test_billing_read_endpoints_fallback_when_core_returns_400(client, auth_headers, seeded_data, flask_app, monkeypatch):
    from app.billing import services

    flask_app.config["CORE_API_URL"] = "http://px_core_app:8000/api/v1"
    seeded_data["account"].current_plan_code = "ACADEMIA"
    monkeypatch.setattr(services.core_client, "request", _raise_core_400)

    plans = client.get("/api/v1/billing/plans", headers=auth_headers)
    subscription = client.get("/api/v1/billing/subscriptions/me", headers=auth_headers)
    entitlements = client.get("/api/v1/billing/entitlements/me", headers=auth_headers)

    assert plans.status_code == 200, plans.get_data(as_text=True)
    assert subscription.status_code == 200, subscription.get_data(as_text=True)
    assert entitlements.status_code == 200, entitlements.get_data(as_text=True)
    assert {item["code"] for item in plans.get_json()["data"]["items"]} >= {"FREE", "PRO", "ELITE", "ACADEMIA"}
    assert subscription.get_json()["data"]["planCode"] == "ACADEMIA"
    assert entitlements.get_json()["data"]["plan_code"] == "ACADEMIA"


def test_billing_plans_normalize_aliases_and_hide_invalid_variants(client, auth_headers, seeded_data, flask_app, monkeypatch):
    from app.billing import services

    flask_app.config["CORE_API_URL"] = "http://px_core_app:8000/api/v1"

    def _fake_core_request(**kwargs):
        if kwargs["path"] == "/billing/plans/":
            return [
                {"code": "FREE", "name": "Starter", "price_cents": 0, "currency": "BRL", "interval": "month", "stripe_price_id": "price_free"},
                {"code": "PRO_TESTE_199", "name": "Pro Teste", "price_cents": 199, "currency": "BRL", "interval": "month", "stripe_price_id": "price_test"},
                {"code": "PRO", "name": "Pro", "price_cents": 8900, "currency": "BRL", "interval": "month", "stripe_price_id": "price_pro"},
                {"code": "PREMIUM", "name": "Elite", "price_cents": 14900, "currency": "BRL", "interval": "month", "stripe_price_id": "price_elite"},
                {"code": "ACADEMIA", "name": "Academia", "price_cents": 34900, "currency": "BRL", "interval": "month", "stripe_price_id": "price_academia"},
                {"code": "ELITE", "name": "Elite quebrado", "price_cents": 14900, "currency": "BRL", "interval": "month", "stripe_price_id": ""},
            ]
        raise AssertionError(f"unexpected path {kwargs['path']}")

    monkeypatch.setattr(services.core_client, "request", _fake_core_request)

    response = client.get("/api/v1/billing/plans", headers=auth_headers)

    assert response.status_code == 200, response.get_data(as_text=True)
    items = response.get_json()["data"]["items"]
    assert [item["code"] for item in items] == ["FREE", "PRO", "ELITE", "ACADEMIA"]
    assert next(item for item in items if item["code"] == "PRO")["price"] == 89
    assert next(item for item in items if item["code"] == "ELITE")["price"] == 149


def test_billing_setup_intent_uses_org_first_paths_when_workspace_exists(client, auth_headers, seeded_data, flask_app, monkeypatch):
    from app.billing import services

    flask_app.config["CORE_API_URL"] = "http://px_core_app:8000/api/v1"
    seeded_data["account"].external_org_id = "org-fit-123"
    captured = []

    def _fake_core_request(**kwargs):
        captured.append((kwargs["method"], kwargs["path"], kwargs.get("json")))
        if kwargs["path"].endswith("/setup-intent/"):
            return {"client_secret": "seti_123_secret_456", "setup_intent_id": "seti_123"}
        if kwargs["path"].endswith("/setup-intent/confirm/"):
            return {"payment_method": {"brand": "visa", "last4": "4242"}}
        raise AssertionError(f"unexpected path {kwargs['path']}")

    monkeypatch.setattr(services.core_client, "request", _fake_core_request)

    create_response = client.post("/api/v1/billing/setup-intent", headers=auth_headers)
    confirm_response = client.post(
        "/api/v1/billing/setup-intent/confirm",
        headers=auth_headers,
        json={"setup_intent_id": "seti_123"},
    )

    assert create_response.status_code == 200, create_response.get_data(as_text=True)
    assert confirm_response.status_code == 200, confirm_response.get_data(as_text=True)
    assert captured[0][1] == "/orgs/org-fit-123/setup-intent/"
    assert captured[1][1] == "/orgs/org-fit-123/setup-intent/confirm/"
    assert captured[1][2] == {"setup_intent_id": "seti_123"}
