from __future__ import annotations

from flask import Blueprint, current_app

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.system.ops import build_ops_snapshot


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
