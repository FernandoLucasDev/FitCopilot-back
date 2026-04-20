from __future__ import annotations

from flask import Blueprint

from app.common.api import success_response
from app.common.security.auth import current_auth, require_auth
from app.overview.services import get_workspace_overview


overview_bp = Blueprint("overview", __name__)


@overview_bp.get("/workspace/overview")
@require_auth()
def workspace_overview():
    auth = current_auth()
    return success_response(get_workspace_overview(auth.account_id))
