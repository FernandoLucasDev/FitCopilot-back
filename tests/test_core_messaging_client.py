from app.integrations.core_messaging_client import CoreMessagingClient


def _capture(monkeypatch):
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return {"public_id": "msg-test"}

    monkeypatch.setattr("app.integrations.core_messaging_client.core_client.request", fake_request)
    return captured


def test_text_message_uses_current_core_contract(monkeypatch):
    captured = _capture(monkeypatch)

    CoreMessagingClient().send_text_message(
        token="token",
        to_phone="5537996620448",
        body="Teste",
        idempotency_key="idem-1",
        external_reference="ref-1",
        requested_by_service="fitcopilot-backend",
    )

    assert captured["path"] == "/communication/messages/send/"
    assert captured["json"]["to_phone"] == "5537996620448"
    assert captured["json"]["text"] == "Teste"
    assert "to" not in captured["json"]


def test_template_message_flattens_template_fields_for_core(monkeypatch):
    captured = _capture(monkeypatch)

    CoreMessagingClient().send_template_message(
        token="token",
        to_phone="5537996620448",
        template_name="hello_world",
        language_code="en_US",
        components=[
            {"type": "body", "parameters": [{"type": "text", "text": "Fernando"}]},
            {"type": "button", "sub_type": "url", "index": "0", "parameters": []},
        ],
        idempotency_key="idem-2",
        external_reference="ref-2",
        requested_by_service="fitcopilot-backend",
    )

    payload = captured["json"]
    assert payload["template_name"] == "hello_world"
    assert payload["language_code"] == "en_US"
    assert payload["template_params"]["body"][0]["text"] == "Fernando"
    assert payload["template_params"]["buttons"][0]["sub_type"] == "url"
    assert "template" not in payload
