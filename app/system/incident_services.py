from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.system.incident_models import OperationalIncident


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def list_incidents(*, account_id) -> list[OperationalIncident]:
    return (
        OperationalIncident.query.filter_by(account_id=account_id)
        .order_by(OperationalIncident.started_at.desc())
        .all()
    )


def create_incident(*, account_id, title: str, severity: str = "minor", notes: str | None = None) -> OperationalIncident:
    incident = OperationalIncident(
        account_id=account_id,
        title=title,
        severity=severity,
        status="open",
        notes=notes,
        started_at=utcnow(),
    )
    db.session.add(incident)
    db.session.commit()
    return incident


def resolve_incident(*, account_id, incident_id) -> OperationalIncident:
    incident = OperationalIncident.query.filter_by(id=incident_id, account_id=account_id).first()
    if incident is None:
        return None
    incident.status = "resolved"
    incident.resolved_at = utcnow()
    db.session.commit()
    return incident


def serialize_incident(incident: OperationalIncident) -> dict:
    return {
        "id": str(incident.id),
        "title": incident.title,
        "severity": incident.severity,
        "status": incident.status,
        "notes": incident.notes,
        "startedAt": incident.started_at.isoformat(),
        "resolvedAt": incident.resolved_at.isoformat() if incident.resolved_at else None,
    }
