from __future__ import annotations

from typing import Any

from app.integrations.core_client import core_client


class CoreMessagingClient:
    def send_message(
        self,
        *,
        token: str,
        payload: dict[str, Any],
        org_id: str | None = None,
    ) -> dict[str, Any]:
        return core_client.request(
            method="POST",
            path="/communication/messages/send/",
            token=token,
            json=payload,
            org_id=org_id,
        )

    def send_text_message(self, *, token: str, to_phone: str, body: str, idempotency_key: str, external_reference: str, requested_by_service: str, org_id: str | None = None) -> dict[str, Any]:
        return self.send_message(
            token=token,
            org_id=org_id,
            payload={
                "channel": "whatsapp",
                "message_type": "text",
                "to_phone": to_phone,
                "text": body,
                "idempotency_key": idempotency_key,
                "external_reference": external_reference,
                "requested_by_service": requested_by_service,
                "organization_id": org_id,
            },
        )

    def send_template_message(self, *, token: str, to_phone: str, template_name: str, language_code: str, idempotency_key: str, external_reference: str, requested_by_service: str, components: list[dict] | None = None, org_id: str | None = None) -> dict[str, Any]:
        template_params: dict[str, list[dict]] = {"header": [], "body": [], "buttons": []}
        for component in components or []:
            component_type = str(component.get("type") or "").lower()
            if component_type in {"header", "body"}:
                template_params[component_type].extend(component.get("parameters") or [])
            elif component_type == "button":
                template_params["buttons"].append(component)
        return self.send_message(
            token=token,
            org_id=org_id,
            payload={
                "channel": "whatsapp",
                "message_type": "template",
                "to_phone": to_phone,
                "template_name": template_name,
                "language_code": language_code,
                "template_params": template_params,
                "idempotency_key": idempotency_key,
                "external_reference": external_reference,
                "requested_by_service": requested_by_service,
                "organization_id": org_id,
            },
        )

    def send_interactive_message(self, *, token: str, to_phone: str, body: str, buttons: list[dict[str, str]], idempotency_key: str, external_reference: str, requested_by_service: str, org_id: str | None = None) -> dict[str, Any]:
        return self.send_message(
            token=token,
            org_id=org_id,
            payload={
                "channel": "whatsapp",
                "message_type": "interactive",
                "to_phone": to_phone,
                "interactive": {
                    "type": "button",
                    "body": {"text": body},
                    "action": {"buttons": buttons},
                },
                "idempotency_key": idempotency_key,
                "external_reference": external_reference,
                "requested_by_service": requested_by_service,
                "organization_id": org_id,
            },
        )

    def send_media_message(self, *, token: str, to_phone: str, media_url: str, media_type: str, caption: str | None, idempotency_key: str, external_reference: str, requested_by_service: str, org_id: str | None = None) -> dict[str, Any]:
        return self.send_message(
            token=token,
            org_id=org_id,
            payload={
                "channel": "whatsapp",
                "message_type": "media",
                "to_phone": to_phone,
                "media": {"type": media_type, "link": media_url, "caption": caption},
                "idempotency_key": idempotency_key,
                "external_reference": external_reference,
                "requested_by_service": requested_by_service,
                "organization_id": org_id,
            },
        )


core_messaging_client = CoreMessagingClient()
