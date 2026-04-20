from __future__ import annotations

from flask import Blueprint, current_app

from app.common.api import success_response


system_bp = Blueprint("system", __name__)


@system_bp.get("/health")
def health_check():
    return success_response({"status": "ok"})


@system_bp.get("/system/status")
def system_status():
    return success_response(
        {
            "status": "ok",
            "services": {
                "database": current_app.config["SQLALCHEMY_DATABASE_URI"],
                "redis": current_app.config["REDIS_URL"],
                "storage": current_app.config["STORAGE_PROVIDER"],
                "ai": current_app.config["AI_PROVIDER"],
            },
        }
    )
