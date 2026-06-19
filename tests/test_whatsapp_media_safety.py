from __future__ import annotations

import base64


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def _headers(app):
    return {"X-Bot-Secret": app.config["BOT_INTERNAL_SECRET"], "Content-Type": "application/json"}


def test_media_safety_blocks_invalid_media(client, flask_app):
    data = _ok(
        client.post(
            "/api/v1/internal/bot/whatsapp/media-safety",
            headers=_headers(flask_app),
            json={
                "phoneNumber": "5537996620448",
                "messageType": "image",
                "media": {"base64": "not-valid-base64", "mimeType": "image/jpeg"},
            },
        )
    )

    assert data["allowed"] is False
    assert data["category"] == "media_unavailable"
    assert "segurança" in data["userMessage"]


def test_media_safety_allows_safe_food_when_provider_approves(client, flask_app):
    from app.ai.base import MediaSafetyResult

    class Provider:
        def moderate_media(self, *, content: bytes, mime_type: str, context: dict):
            assert content == b"fake-image"
            assert mime_type == "image/jpeg"
            return MediaSafetyResult(
                allowed=True,
                category="safe_food",
                severity="allow",
                user_message="",
                confidence=0.93,
            )

    flask_app.extensions["ai_provider"] = Provider()
    encoded = base64.b64encode(b"fake-image").decode("ascii")

    data = _ok(
        client.post(
            "/api/v1/internal/bot/whatsapp/media-safety",
            headers=_headers(flask_app),
            json={
                "phoneNumber": "5537996620448",
                "messageType": "image",
                "caption": "cafe da manha",
                "media": {"base64": encoded, "mimeType": "image/jpeg"},
            },
        )
    )

    assert data["allowed"] is True
    assert data["category"] == "safe_food"
    assert data["confidence"] == 0.93


def test_media_safety_blocks_explicit_provider_category(client, flask_app):
    from app.ai.base import MediaSafetyResult

    class Provider:
        def moderate_media(self, *, content: bytes, mime_type: str, context: dict):
            return MediaSafetyResult(
                allowed=False,
                category="adult_nudity",
                severity="block",
                user_message="Não consigo analisar esse tipo de imagem por aqui.",
                confidence=0.98,
            )

    flask_app.extensions["ai_provider"] = Provider()
    encoded = base64.b64encode(b"fake-image").decode("ascii")

    data = _ok(
        client.post(
            "/api/v1/internal/bot/whatsapp/media-safety",
            headers=_headers(flask_app),
            json={
                "phoneNumber": "5537996620448",
                "messageType": "image",
                "media": {"base64": encoded, "mimeType": "image/jpeg"},
            },
        )
    )

    assert data["allowed"] is False
    assert data["category"] == "adult_nudity"
    assert data["severity"] == "block"
