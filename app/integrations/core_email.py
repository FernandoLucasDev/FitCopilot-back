from __future__ import annotations

import requests
from flask import current_app


class CoreEmailGateway:
    def _core_url(self) -> str:
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

    def _headers(self, *, access_token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-App-ID": self._app_header(),
        }
        host_header = current_app.config.get("CORE_HOST_HEADER")
        if host_header:
            headers["Host"] = str(host_header)
        return headers

    def send_html_email(self, *, access_token: str, to_email: str, subject: str, html_content: str) -> dict:
        response = requests.post(
            f"{self._core_url()}/communication/email/send/",
            json={"to_email": to_email, "subject": subject, "html_content": html_content},
            headers=self._headers(access_token=access_token),
            timeout=float(current_app.config.get("CORE_TIMEOUT_SECONDS", 15)),
        )
        response.raise_for_status()
        return response.json() if response.text else {}


core_email_gateway = CoreEmailGateway()
