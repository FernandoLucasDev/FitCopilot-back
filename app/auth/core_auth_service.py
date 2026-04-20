from __future__ import annotations

from typing import Any

import requests
from flask import current_app

from app.integrations.core_client import core_client


class CoreAuthService:
    def is_enabled(self) -> bool:
        return bool(current_app.config.get("CORE_API_URL"))

    def register(self, *, full_name: str, email: str, password: str, phone: str | None = None) -> dict[str, Any]:
        payload = {"full_name": full_name, "email": email, "password": password, "phone": phone}
        return core_client.request(method="POST", path="/auth/register/", json=payload)

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        payload = {"email": email, "password": password}
        return core_client.request(method="POST", path="/auth/login/", json=payload)

    def me(self, *, token: str) -> dict[str, Any]:
        return core_client.request(method="GET", path="/auth/me/", token=token)

    def refresh(self, *, refresh_token: str) -> dict[str, Any]:
        payload = {"refresh_token": refresh_token}
        return core_client.request(method="POST", path="/auth/refresh/", json=payload)


core_auth_service = CoreAuthService()
