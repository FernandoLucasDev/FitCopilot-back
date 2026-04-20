from __future__ import annotations

from typing import Any

import requests
from flask import current_app


class CoreClient:
    def _base_url(self) -> str:
        base = current_app.config.get("CORE_API_URL")
        if not base:
            raise RuntimeError("CORE_API_URL não configurado")
        return str(base).rstrip("/")

    def _app_header(self) -> str:
        return str(
            current_app.config.get("APP_ID")
            or current_app.config.get("APPID")
            or current_app.config.get("APP_SLUG")
            or ""
        )

    def _headers(self, *, token: str | None = None, org_id: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-App-ID": self._app_header(),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if org_id:
            headers["X-ORG-ID"] = str(org_id)
        return headers

    def request(
        self,
        *,
        method: str,
        path: str,
        token: str | None = None,
        json: dict[str, Any] | None = None,
        org_id: str | None = None,
    ) -> Any:
        response = requests.request(
            method=method,
            url=f"{self._base_url()}{path}",
            headers=self._headers(token=token, org_id=org_id),
            json=json,
            timeout=float(current_app.config.get("CORE_TIMEOUT_SECONDS", 15)),
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.text:
            return None
        return response.json()


core_client = CoreClient()
