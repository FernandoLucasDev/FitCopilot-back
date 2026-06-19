from __future__ import annotations

from uuid import uuid4

from app.accounts.models import Account
from app.auth.models import User
from app.extensions import db
from app.students.models import StudentProfile


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def _register(client, *, email: str, full_name: str, account_name: str):
    return _ok(
        client.post(
            "/api/v1/auth/register",
            json={
                "account_name": account_name,
                "account_email": email,
                "full_name": full_name,
                "email": email,
                "password": "Teste123!",
                "professional_type": "personal_trainer",
            },
        ),
        201,
    )


def _headers(token: str, org_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-ORG-ID"] = org_id
    return headers


def test_academy_invites_professional_and_scopes_workspace_data(client):
    suffix = uuid4().hex[:8]
    owner_email = f"qa.academia.owner.{suffix}@fitcopilot.dev"
    trainer_email = f"qa.personal.convidado.{suffix}@fitcopilot.dev"
    viewer_email = f"qa.viewer.convidado.{suffix}@fitcopilot.dev"

    owner = _register(
        client,
        email=owner_email,
        full_name="Gestora Academia QA",
        account_name="Conta Pessoal Gestora QA",
    )
    owner_token = owner["token"]

    created_org = _ok(
        client.post(
            "/api/v1/orgs",
            headers=_headers(owner_token),
            json={"name": f"Academia QA E2E {suffix}"},
        ),
        201,
    )["organization"]
    academy_org_id = created_org["id"]

    invite = _ok(
        client.post(
            f"/api/v1/orgs/{academy_org_id}/invite",
            headers=_headers(owner_token, academy_org_id),
            json={"email": trainer_email, "role": "TRAINER"},
        ),
        201,
    )["invite"]
    assert invite["email"] == trainer_email
    assert invite["token"]
    assert invite["emailDeliveryStatus"] == "not_configured"

    resolved = _ok(client.get(f"/api/v1/orgs/invites/resolve?token={invite['token']}"))["invite"]
    assert resolved["organizationName"] == created_org["name"]
    assert resolved["role"] == "TRAINER"
    assert resolved["hasAccount"] is False

    trainer = _register(
        client,
        email=trainer_email,
        full_name="Personal Convidado QA",
        account_name="Workspace Pessoal Personal QA",
    )
    trainer_token = trainer["token"]

    accepted = _ok(
        client.post(
            "/api/v1/orgs/invites/accept",
            headers=_headers(trainer_token),
            json={"token": invite["token"]},
        )
    )
    assert accepted["organizationId"] == academy_org_id

    trainer_orgs = _ok(client.get("/api/v1/orgs/mine", headers=_headers(trainer_token)))["items"]
    trainer_org_names = {item["name"] for item in trainer_orgs}
    assert "Workspace Pessoal Personal QA" in trainer_org_names
    assert created_org["name"] in trainer_org_names

    member_rows = _ok(client.get(f"/api/v1/orgs/{academy_org_id}/members", headers=_headers(owner_token, academy_org_id)))["items"]
    trainer_member = next(item for item in member_rows if item["email"] == trainer_email)
    assert trainer_member["status"] == "ACTIVE"
    assert trainer_member["role"] == "TRAINER"

    academy_student = _ok(
        client.post(
            "/api/v1/students",
            headers=_headers(owner_token, academy_org_id),
            json={
                "full_name": "Aluno Academia E2E",
                "email": f"aluno.academia.{suffix}@fitcopilot.dev",
                "phone": "+5537996620448",
                "main_objective_text": "Hipertrofia",
            },
        ),
        201,
    )["student"]
    academy_student_id = academy_student["id"]

    trainer_academy_students = _ok(client.get("/api/v1/students", headers=_headers(trainer_token, academy_org_id)))["items"]
    assert academy_student_id in {item["id"] for item in trainer_academy_students}

    trainer_personal_students = _ok(client.get("/api/v1/students", headers=_headers(trainer_token)))["items"]
    assert academy_student_id not in {item["id"] for item in trainer_personal_students}

    personal_student = _ok(
        client.post(
            "/api/v1/students",
            headers=_headers(trainer_token),
            json={
                "full_name": "Aluno Pessoal E2E",
                "email": f"aluno.pessoal.{suffix}@fitcopilot.dev",
                "phone": "+5537996620448",
            },
        ),
        201,
    )["student"]
    assert personal_student["id"] not in {item["id"] for item in trainer_academy_students}

    viewer_invite = _ok(
        client.post(
            f"/api/v1/orgs/{academy_org_id}/invite",
            headers=_headers(owner_token, academy_org_id),
            json={"email": viewer_email, "role": "VIEWER"},
        ),
        201,
    )["invite"]
    viewer = _register(
        client,
        email=viewer_email,
        full_name="Leitora Academia QA",
        account_name="Workspace Pessoal Viewer QA",
    )
    viewer_token = viewer["token"]
    _ok(
        client.post(
            "/api/v1/orgs/invites/accept",
            headers=_headers(viewer_token),
            json={"token": viewer_invite["token"]},
        )
    )

    viewer_read = _ok(client.get("/api/v1/students", headers=_headers(viewer_token, academy_org_id)))["items"]
    assert academy_student_id in {item["id"] for item in viewer_read}

    denied = client.post(
        "/api/v1/students",
        headers=_headers(viewer_token, academy_org_id),
        json={"full_name": "Criacao Bloqueada", "email": f"bloqueado.{suffix}@fitcopilot.dev"},
    )
    assert denied.status_code == 403

    with client.application.app_context():
        academy_student_model = StudentProfile.query.filter_by(id=academy_student_id).first()
        assert academy_student_model is not None
        academy_account = Account.query.filter_by(id=academy_student_model.account_id).first()
        assert academy_account is not None
        assert str(academy_account.external_org_id) == academy_org_id


def test_local_academy_invite_sends_email_through_core_gateway(client, flask_app, monkeypatch):
    suffix = uuid4().hex[:8]
    owner_email = f"qa.email.owner.{suffix}@fitcopilot.dev"
    trainer_email = f"qa.email.personal.{suffix}@fitcopilot.dev"
    sent: dict[str, object] = {}

    owner = _register(
        client,
        email=owner_email,
        full_name="Gestora Email QA",
        account_name="Academia Email QA",
    )
    owner_token = owner["token"]
    account_id = owner["account"]["id"]

    with flask_app.app_context():
        owner_user = User.query.filter_by(email=owner_email).first()
        assert owner_user is not None
        owner_user.core_access_token = "core-access-token"
        db.session.commit()
        flask_app.config["CORE_API_URL"] = "http://core.test/api/v1"
        flask_app.config["FRONTEND_URL"] = "http://127.0.0.1:3000"

    def fake_send_html_email(**kwargs):
        sent.update(kwargs)
        return {"id": "email-log-1", "delivery_status": "queued"}

    from app.orgs import services as org_services

    monkeypatch.setattr(org_services.core_email_gateway, "send_html_email", fake_send_html_email)

    invite = _ok(
        client.post(
            f"/api/v1/orgs/{account_id}/invite",
            headers=_headers(owner_token, account_id),
            json={"email": trainer_email, "role": "TRAINER"},
        ),
        201,
    )["invite"]

    assert invite["emailDeliveryStatus"] == "sent"
    assert sent["access_token"] == "core-access-token"
    assert sent["to_email"] == trainer_email
    assert "Convite para a academia Academia Email QA" in sent["subject"]
    assert "http://127.0.0.1:3000/accept-invite?token=" in sent["html_content"]
