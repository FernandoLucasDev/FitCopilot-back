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
