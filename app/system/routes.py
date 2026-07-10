from __future__ import annotations

from http import HTTPStatus

from flask import Blueprint, Response, current_app

from app.accounts.enterprise_services import get_network_dashboard, is_network
from app.accounts.schemas import UpdateContractInput
from app.accounts.services import update_enterprise_contract
from app.common.api import ApiError, success_response
from app.common.request import parse_json
from app.common.security.auth import current_auth, require_auth
from app.system.incident_services import create_incident, list_incidents, resolve_incident, serialize_incident
from app.system.ops import build_ops_snapshot
from app.system.schemas import CreateIncidentInput


system_bp = Blueprint("system", __name__)


@system_bp.get("/health")
def health_check():
    return success_response({"status": "ok"})


@system_bp.get("/system/status")
@require_auth({"owner", "admin"})
def system_status():
    return success_response(
        {
            "status": "ok",
            "services": {
                "database": "configured" if current_app.config.get("SQLALCHEMY_DATABASE_URI") else "missing",
                "redis": "configured" if current_app.config.get("REDIS_URL") else "missing",
                "storage": current_app.config["STORAGE_PROVIDER"],
                "ai": current_app.config["AI_PROVIDER"],
            },
        }
    )


@system_bp.get("/system/ops")
@require_auth({"owner", "admin"})
def system_ops():
    auth = current_auth()
    return success_response(build_ops_snapshot(account_id=auth.account_id))


@system_bp.get("/system/enterprise")
@require_auth({"owner", "admin"})
def system_enterprise():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    payload = {
        "accountId": str(account.id),
        "accountType": account.account_type,
        "contract": account.enterprise_contract_json or {},
        "ops": build_ops_snapshot(account_id=account.id),
        "networkDashboard": get_network_dashboard(account.id) if is_network(account) else None,
    }
    return success_response(payload)


@system_bp.patch("/system/enterprise/contract")
@require_auth({"owner", "admin"})
def update_system_enterprise_contract():
    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    payload = parse_json(UpdateContractInput)
    account = update_enterprise_contract(account=account, actor_user_id=auth.user.id, data=payload)
    return success_response({"contract": account.enterprise_contract_json or {}})


@system_bp.get("/system/enterprise/monthly-report")
@require_auth({"owner", "admin"})
def system_enterprise_monthly_report():
    from app.jobs.tasks import generate_network_monthly_report

    auth = current_auth()
    account = auth.user.account
    if account is None:
        raise ApiError("Conta não encontrada", HTTPStatus.NOT_FOUND)
    if not is_network(account):
        raise ApiError("Relatório mensal disponível apenas para contas de rede", HTTPStatus.BAD_REQUEST)
    pdf_bytes = generate_network_monthly_report(account.id)
    return Response(pdf_bytes, mimetype="application/pdf")


@system_bp.get("/system/incidents")
@require_auth({"owner", "admin"})
def get_system_incidents():
    auth = current_auth()
    items = [serialize_incident(incident) for incident in list_incidents(account_id=auth.account_id)]
    return success_response({"items": items})


@system_bp.post("/system/incidents")
@require_auth({"owner", "admin"})
def post_system_incident():
    auth = current_auth()
    payload = parse_json(CreateIncidentInput)
    incident = create_incident(account_id=auth.account_id, title=payload.title, severity=payload.severity, notes=payload.notes)
    return success_response({"incident": serialize_incident(incident)}, HTTPStatus.CREATED)


@system_bp.post("/system/incidents/<incident_id>/resolve")
@require_auth({"owner", "admin"})
def resolve_system_incident(incident_id: str):
    auth = current_auth()
    incident = resolve_incident(account_id=auth.account_id, incident_id=incident_id)
    if incident is None:
        raise ApiError("Incidente não encontrado", HTTPStatus.NOT_FOUND)
    return success_response({"incident": serialize_incident(incident)})
