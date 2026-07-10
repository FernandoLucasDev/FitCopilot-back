from __future__ import annotations

import io
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from app.accounts.models import Account, AccountMembership, ProfessionalProfile
from app.auth.models import User
from app.extensions import db
from app.students.models import StudentProfile


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def _headers(token: str, org_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-ORG-ID"] = org_id
    return headers


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _create_user(account: Account, *, email: str, full_name: str) -> User:
    user = User(
        account_id=account.id,
        role="owner",
        full_name=full_name,
        email=email,
        password_hash=generate_password_hash("abcd1234"),
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _login_token(client, email: str) -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "abcd1234"})
    return response.get_json()["data"]["token"]


def _build_network(flask_app):
    with flask_app.app_context():
        network = Account(name="Rede QA", slug="rede-qa", email="rede-qa@fitcopilot.dev", account_type="network")
        db.session.add(network)
        db.session.flush()

        unit1 = Account(name="Unidade 1", slug="unidade-1", email="unidade1@fitcopilot.dev", account_type="unit", parent_account_id=network.id)
        unit2 = Account(name="Unidade 2", slug="unidade-2", email="unidade2@fitcopilot.dev", account_type="unit", parent_account_id=network.id)
        db.session.add_all([unit1, unit2])
        db.session.flush()

        network_owner = _create_user(network, email="owner@rede-qa.dev", full_name="Dono Rede")
        db.session.add(
            AccountMembership(account_id=network.id, user_id=network_owner.id, role="NETWORK_OWNER", status="ACTIVE", joined_at=_utcnow())
        )

        unit_manager = _create_user(unit1, email="gestor@unidade1.dev", full_name="Gestor Unidade 1")
        db.session.add(
            AccountMembership(account_id=unit1.id, user_id=unit_manager.id, role="UNIT_MANAGER", status="ACTIVE", joined_at=_utcnow())
        )

        prof_user = _create_user(unit1, email="prof@unidade1.dev", full_name="Profissional Unidade 1")
        prof_profile = ProfessionalProfile(user_id=prof_user.id, account_id=unit1.id, professional_type="personal_trainer", onboarding_completed=True)
        db.session.add(prof_profile)
        db.session.add(
            AccountMembership(account_id=unit1.id, user_id=prof_user.id, role="PROFESSIONAL", status="ACTIVE", joined_at=_utcnow())
        )
        db.session.flush()

        other_prof_user = _create_user(unit1, email="outroprof@unidade1.dev", full_name="Outro Profissional")
        other_prof_profile = ProfessionalProfile(
            user_id=other_prof_user.id, account_id=unit1.id, professional_type="personal_trainer", onboarding_completed=True
        )
        db.session.add(other_prof_profile)
        db.session.flush()

        db.session.add_all(
            [
                StudentProfile(account_id=unit1.id, primary_professional_id=prof_profile.id, full_name="Aluno A", status="active"),
                StudentProfile(account_id=unit1.id, primary_professional_id=prof_profile.id, full_name="Aluno B", status="active"),
                StudentProfile(account_id=unit1.id, primary_professional_id=other_prof_profile.id, full_name="Aluno C", status="active"),
            ]
        )

        prof_user2 = _create_user(unit2, email="prof@unidade2.dev", full_name="Profissional Unidade 2")
        prof_profile2 = ProfessionalProfile(
            user_id=prof_user2.id, account_id=unit2.id, professional_type="personal_trainer", onboarding_completed=True
        )
        db.session.add(prof_profile2)
        db.session.flush()
        db.session.add(StudentProfile(account_id=unit2.id, primary_professional_id=prof_profile2.id, full_name="Aluno D", status="active"))

        db.session.commit()
        return {
            "network_id": str(network.id),
            "unit1_id": str(unit1.id),
            "unit2_id": str(unit2.id),
            "network_owner_email": network_owner.email,
            "unit_manager_email": unit_manager.email,
            "prof_email": prof_user.email,
        }


def test_professional_only_sees_own_students_within_unit(client, flask_app):
    seed = _build_network(flask_app)
    prof_token = _login_token(client, seed["prof_email"])

    response = client.get("/api/v1/students", headers=_headers(prof_token, org_id=seed["unit1_id"]))
    items = _ok(response)["items"]
    names = {item["name"] for item in items}
    assert names == {"Aluno A", "Aluno B"}


def test_unit_manager_sees_all_students_in_unit(client, flask_app):
    seed = _build_network(flask_app)
    manager_token = _login_token(client, seed["unit_manager_email"])

    response = client.get("/api/v1/students", headers=_headers(manager_token, org_id=seed["unit1_id"]))
    items = _ok(response)["items"]
    names = {item["name"] for item in items}
    assert names == {"Aluno A", "Aluno B", "Aluno C"}


def test_professional_scope_filter_does_not_apply_without_org_header(client, flask_app):
    seed = _build_network(flask_app)
    prof_token = _login_token(client, seed["prof_email"])

    # Sem X-ORG-ID, o account_id cai no user.account_id (unit1) mas member_role fica None,
    # então o filtro por profissional não é aplicado — comportamento padrão preservado.
    response = client.get("/api/v1/students", headers=_headers(prof_token))
    items = _ok(response)["items"]
    names = {item["name"] for item in items}
    assert names == {"Aluno A", "Aluno B", "Aluno C"}


def test_network_owner_sees_full_network_dashboard(client, flask_app):
    seed = _build_network(flask_app)
    owner_token = _login_token(client, seed["network_owner_email"])

    response = client.get("/api/v1/account/network/dashboard", headers=_headers(owner_token, org_id=seed["network_id"]))
    data = _ok(response)
    assert data["unitsCount"] == 2
    assert data["studentsTotal"] == 4
    unit_ids = {unit["unitId"] for unit in data["units"]}
    assert unit_ids == {seed["unit1_id"], seed["unit2_id"]}


def test_network_owner_dashboard_works_without_org_header(client, flask_app):
    seed = _build_network(flask_app)
    owner_token = _login_token(client, seed["network_owner_email"])

    # user.account_id do dono da rede já é a própria network — não precisa de X-ORG-ID.
    response = client.get("/api/v1/account/network/dashboard", headers=_headers(owner_token))
    data = _ok(response)
    assert data["unitsCount"] == 2


def test_unit_manager_cannot_access_network_dashboard(client, flask_app):
    seed = _build_network(flask_app)
    manager_token = _login_token(client, seed["unit_manager_email"])

    response = client.get("/api/v1/account/network/dashboard", headers=_headers(manager_token, org_id=seed["unit1_id"]))
    assert response.status_code == 403


def test_brand_config_inherits_from_network_and_unit_overrides(client, flask_app):
    seed = _build_network(flask_app)

    with flask_app.app_context():
        from app.accounts.enterprise_services import resolve_effective_config

        network = Account.query.filter_by(id=seed["network_id"]).first()
        unit1 = Account.query.filter_by(id=seed["unit1_id"]).first()

        network.brand_config = {"primaryColor": "#111111", "botName": "Rede Bot"}
        db.session.commit()

        inherited = resolve_effective_config(unit1, "brand_config")
        assert inherited == {"primaryColor": "#111111", "botName": "Rede Bot"}

        unit1.brand_config = {"primaryColor": "#222222"}
        db.session.commit()

        overridden = resolve_effective_config(unit1, "brand_config")
        assert overridden == {"primaryColor": "#222222", "botName": "Rede Bot"}


def test_existing_studio_account_is_unaffected_by_unit_scoping(client, seeded_data, auth_headers):
    # Conta padrão (studio) sem X-ORG-ID continua vendo todos os alunos, sem filtro por profissional.
    response = client.get("/api/v1/students", headers=auth_headers)
    data = _ok(response)
    assert len(data["items"]) >= 1


def _csv_file(text: str):
    return (io.BytesIO(text.encode("utf-8")), "import.csv")


def test_onboarding_preview_accepts_valid_rows(client, seeded_data, auth_headers):
    csv_text = "full_name,email,phone\nMaria Nova,maria.nova@fitcopilot.dev,+5511999990001\nCarlos Novo,carlos.novo@fitcopilot.dev,\n"
    response = client.post(
        "/api/v1/account/onboarding/preview",
        headers=auth_headers,
        data={"file": _csv_file(csv_text)},
        content_type="multipart/form-data",
    )
    data = _ok(response)
    assert len(data["validRows"]) == 2
    assert data["errors"] == []


def test_onboarding_preview_rejects_duplicate_email_in_file(client, seeded_data, auth_headers):
    csv_text = "full_name,email\nMaria Nova,maria.dup@fitcopilot.dev\nMaria Repetida,maria.dup@fitcopilot.dev\n"
    response = client.post(
        "/api/v1/account/onboarding/preview",
        headers=auth_headers,
        data={"file": _csv_file(csv_text)},
        content_type="multipart/form-data",
    )
    data = _ok(response)
    assert len(data["validRows"]) == 1
    assert len(data["errors"]) == 1
    assert "duplicado" in data["errors"][0]["message"]


def test_onboarding_preview_rejects_existing_student_email(client, seeded_data, auth_headers):
    csv_text = "full_name,email\nJoao Duplicado,joao@fitcopilot.dev\n"
    response = client.post(
        "/api/v1/account/onboarding/preview",
        headers=auth_headers,
        data={"file": _csv_file(csv_text)},
        content_type="multipart/form-data",
    )
    data = _ok(response)
    assert data["validRows"] == []
    assert "já cadastrado" in data["errors"][0]["message"]


def test_onboarding_commit_creates_students(client, seeded_data, auth_headers):
    csv_text = "full_name,email\nMaria Commit,maria.commit@fitcopilot.dev\n"
    preview = _ok(
        client.post(
            "/api/v1/account/onboarding/preview",
            headers=auth_headers,
            data={"file": _csv_file(csv_text)},
            content_type="multipart/form-data",
        )
    )
    response = client.post("/api/v1/account/onboarding/commit", headers=auth_headers, json={"rows": preview["validRows"]})
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["status"] == "completed"
    assert len(data["created"]) == 1

    with client.application.app_context():
        assert StudentProfile.query.filter_by(email="maria.commit@fitcopilot.dev").count() == 1


def test_onboarding_commit_rolls_back_whole_batch_on_race_duplicate(client, seeded_data, auth_headers):
    csv_text = "full_name,email\nAluno Um,aluno.um@fitcopilot.dev\nAluno Dois,aluno.dois@fitcopilot.dev\n"
    preview = _ok(
        client.post(
            "/api/v1/account/onboarding/preview",
            headers=auth_headers,
            data={"file": _csv_file(csv_text)},
            content_type="multipart/form-data",
        )
    )
    assert len(preview["validRows"]) == 2

    # Simula corrida: outro fluxo cria "aluno.dois" entre o preview e o commit.
    with client.application.app_context():
        row = preview["validRows"][1]
        db.session.add(
            StudentProfile(
                account_id=row["target_account_id"],
                primary_professional_id=row["professional_id"],
                full_name="Já Existe",
                email="aluno.dois@fitcopilot.dev",
                status="active",
            )
        )
        db.session.commit()

    response = client.post("/api/v1/account/onboarding/commit", headers=auth_headers, json={"rows": preview["validRows"]})
    assert response.status_code == 409
    data = response.get_json()["data"]
    assert data["status"] == "rolled_back"

    with client.application.app_context():
        # "aluno.um" não deve ter sido criado — o lote inteiro foi revertido.
        assert StudentProfile.query.filter_by(email="aluno.um@fitcopilot.dev").count() == 0


def test_onboarding_with_unit_slug_assigns_to_correct_unit(client, flask_app):
    seed = _build_network(flask_app)
    owner_token = _login_token(client, seed["network_owner_email"])
    headers = _headers(owner_token, org_id=seed["network_id"])

    csv_text = "full_name,email,unit_slug\nAluno Rede,aluno.rede@fitcopilot.dev,unidade-1\n"
    preview = _ok(
        client.post(
            "/api/v1/account/onboarding/preview",
            headers=headers,
            data={"file": _csv_file(csv_text)},
            content_type="multipart/form-data",
        )
    )
    assert len(preview["validRows"]) == 1
    assert preview["validRows"][0]["target_account_id"] == seed["unit1_id"]

    response = client.post("/api/v1/account/onboarding/commit", headers=headers, json={"rows": preview["validRows"]})
    assert response.status_code == 201

    with client.application.app_context():
        student = StudentProfile.query.filter_by(email="aluno.rede@fitcopilot.dev").first()
        assert student is not None
        assert str(student.account_id) == seed["unit1_id"]


def test_enterprise_contract_defaults_empty_and_can_be_updated(client, seeded_data, auth_headers):
    data = _ok(client.get("/api/v1/system/enterprise", headers=auth_headers))
    assert data["contract"] == {}
    assert data["accountType"] == "studio"
    assert data["networkDashboard"] is None

    response = client.patch(
        "/api/v1/system/enterprise/contract",
        headers=auth_headers,
        json={"sla_tier": "gold", "support_channel": "whatsapp", "account_manager_name": "Ana Gerente"},
    )
    updated = _ok(response)
    assert updated["contract"]["slaTier"] == "gold"
    assert updated["contract"]["supportChannel"] == "whatsapp"
    assert updated["contract"]["accountManagerName"] == "Ana Gerente"

    data_after = _ok(client.get("/api/v1/system/enterprise", headers=auth_headers))
    assert data_after["contract"]["slaTier"] == "gold"


def test_enterprise_dashboard_present_only_for_network_accounts(client, flask_app):
    seed = _build_network(flask_app)
    owner_token = _login_token(client, seed["network_owner_email"])
    headers = _headers(owner_token, org_id=seed["network_id"])

    data = _ok(client.get("/api/v1/system/enterprise", headers=headers))
    assert data["accountType"] == "network"
    assert data["networkDashboard"] is not None
    assert data["networkDashboard"]["unitsCount"] == 2


def test_incident_lifecycle_create_list_resolve(client, seeded_data, auth_headers):
    items = _ok(client.get("/api/v1/system/incidents", headers=auth_headers))["items"]
    assert items == []

    created = _ok(
        client.post(
            "/api/v1/system/incidents",
            headers=auth_headers,
            json={"title": "Instabilidade no envio de WhatsApp", "severity": "major"},
        ),
        status_code=201,
    )["incident"]
    assert created["status"] == "open"
    assert created["severity"] == "major"

    items = _ok(client.get("/api/v1/system/incidents", headers=auth_headers))["items"]
    assert len(items) == 1
    assert items[0]["id"] == created["id"]

    resolved = _ok(client.post(f"/api/v1/system/incidents/{created['id']}/resolve", headers=auth_headers))["incident"]
    assert resolved["status"] == "resolved"
    assert resolved["resolvedAt"] is not None


def test_incident_resolve_unknown_id_returns_404(client, seeded_data, auth_headers):
    response = client.post("/api/v1/system/incidents/00000000-0000-0000-0000-000000000000/resolve", headers=auth_headers)
    assert response.status_code == 404


def test_monthly_report_requires_network_account(client, seeded_data, auth_headers):
    response = client.get("/api/v1/system/enterprise/monthly-report", headers=auth_headers)
    assert response.status_code == 400


def test_monthly_report_generates_pdf_for_network(client, flask_app):
    seed = _build_network(flask_app)
    owner_token = _login_token(client, seed["network_owner_email"])
    headers = _headers(owner_token, org_id=seed["network_id"])

    response = client.get("/api/v1/system/enterprise/monthly-report", headers=headers)
    assert response.status_code == 200
    assert response.content_type == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_sync_core_membership_preserves_enterprise_role(flask_app, seeded_data):
    from app.orgs.services import sync_core_membership

    with flask_app.app_context():
        network = Account(name="Rede Sync QA", slug="rede-sync-qa", email="rede-sync-qa@fitcopilot.dev", account_type="network", external_org_id="core-org-network-qa")
        db.session.add(network)
        db.session.flush()

        owner = _create_user(network, email="owner-sync@rede-qa.dev", full_name="Dono Sync QA")
        db.session.add(
            AccountMembership(account_id=network.id, user_id=owner.id, role="NETWORK_OWNER", status="ACTIVE", joined_at=_utcnow())
        )
        db.session.commit()

        # Core nao conhece "NETWORK_OWNER" — reporta o papel generico "OWNER" pro org que criou pra esse usuario.
        core_row = {
            "organization": {"id": "core-org-network-qa", "name": "Rede Sync QA", "slug": "rede-sync-qa"},
            "role": "OWNER",
            "status": "ACTIVE",
        }
        membership = sync_core_membership(owner, core_row)
        db.session.commit()

        assert membership.role == "NETWORK_OWNER"


def test_sync_core_membership_still_updates_non_enterprise_role(flask_app, seeded_data):
    from app.orgs.services import sync_core_membership

    with flask_app.app_context():
        account = seeded_data["account"]
        account.external_org_id = "core-org-studio-qa"
        user = _create_user(account, email="staff-sync@fitcopilot.dev", full_name="Staff Sync QA")
        db.session.add(AccountMembership(account_id=account.id, user_id=user.id, role="TRAINER", status="ACTIVE", joined_at=_utcnow()))
        db.session.commit()

        core_row = {
            "organization": {"id": "core-org-studio-qa", "name": account.name, "slug": account.slug},
            "role": "ADMIN",
            "status": "ACTIVE",
        }
        membership = sync_core_membership(user, core_row)
        db.session.commit()

        assert membership.role == "ADMIN"


def test_network_owner_can_access_unit_workspace_without_direct_membership(client, flask_app):
    seed = _build_network(flask_app)
    owner_token = _login_token(client, seed["network_owner_email"])

    response = client.get("/api/v1/students", headers=_headers(owner_token, org_id=seed["unit1_id"]))
    items = _ok(response)["items"]
    names = {item["name"] for item in items}
    assert names == {"Aluno A", "Aluno B", "Aluno C"}


def test_stranger_still_gets_403_on_unit_workspace(client, flask_app, seeded_data):
    seed = _build_network(flask_app)
    stranger_token = _login_token(client, seeded_data["owner"].email)

    response = client.get("/api/v1/students", headers=_headers(stranger_token, org_id=seed["unit1_id"]))
    assert response.status_code == 403


def test_ensure_local_account_for_org_preserves_existing_name_and_slug(flask_app):
    from app.orgs.services import ensure_local_account_for_org

    with flask_app.app_context():
        unit = Account(
            name="Unidade Centro QA",
            slug="unidade-centro-qa",
            email="unidade-centro-qa@fitcopilot.dev",
            account_type="unit",
            external_org_id="core-org-unit-qa",
        )
        db.session.add(unit)
        db.session.commit()

        # Core reporta um nome/slug diferentes (auto-gerado no primeiro login de um membro) —
        # o Fit e a fonte de verdade pra contas que ja existem localmente, entao isso nao deve
        # sobrescrever o que o profissional configurou (slug e usado em onboarding CSV).
        resolved = ensure_local_account_for_org({"id": "core-org-unit-qa", "name": "Escritorio de gestor", "slug": "escritorio-de-gestor"})

        assert resolved.id == unit.id
        assert resolved.name == "Unidade Centro QA"
        assert resolved.slug == "unidade-centro-qa"


def test_ensure_local_account_for_org_creates_new_account_with_core_name(flask_app):
    from app.orgs.services import ensure_local_account_for_org

    with flask_app.app_context():
        account = ensure_local_account_for_org({"id": "core-org-brand-new-qa", "name": "Novo Workspace Core", "slug": "novo-workspace-core"})
        db.session.commit()

        assert account.name == "Novo Workspace Core"
        assert account.external_org_id == "core-org-brand-new-qa"
