from __future__ import annotations

from datetime import datetime, timezone

from app.insights.models import AIInsight
from app.operations.services import latest_operational_score
from app.students.models import StudentInteraction, StudentProfile
from app.students.services import compute_student_score, serialize_student_list_item


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _triage_student(result, operational: dict) -> tuple[int, list[str]]:
    """Pontuacao rule-based de prioridade + motivos explicitos por aluno.
    Reaproveita o score operacional/compute_student_score ja calculados no overview
    (nao faz query nova) — mantem a triagem O(N) na carteira."""
    reasons: list[str] = []
    score = 0
    status = operational.get("status")
    if status == "risk":
        score += 40
        reasons.append("Risco alto")
    elif status == "cooling":
        score += 25
        reasons.append("Esfriando")
    elif status == "attention":
        score += 15
        reasons.append("Requer atenção")
    if operational.get("trend") == "down":
        score += 15
        reasons.append("Tendência de queda")
    op_score = operational.get("score")
    if isinstance(op_score, (int, float)) and op_score < 50:
        score += 20
        reasons.append(f"Score {int(op_score)}")
    if getattr(result, "status", None) == "no_signal":
        score += 25
        reasons.append("Sem sinal recente")
    return score, reasons


def get_workspace_overview(account_id) -> dict:
    students = StudentProfile.query.filter_by(account_id=account_id).all()
    scored = [
        (student, compute_student_score(student), latest_operational_score(student))
        for student in students
        if student.status != "archived"
    ]
    # Triagem da carteira (ranking rule-based, custo-zero): pontua cada aluno por
    # sinais de risco ja calculados e ordena por prioridade, com motivos explicitos.
    triaged = []
    for student, result, operational in scored:
        priority_score, reasons = _triage_student(result, operational)
        if priority_score <= 0:
            continue
        triaged.append((student, result, operational, priority_score, reasons))
    triaged.sort(key=lambda item: item[3], reverse=True)
    priorities_data = triaged[:8]
    suggestions = (
        AIInsight.query.filter_by(account_id=account_id, status="open")
        .order_by(AIInsight.priority.desc(), AIInsight.created_at.desc())
        .limit(5)
        .all()
    )
    recent_activity = (
        StudentInteraction.query.filter_by(account_id=account_id)
        .order_by(StudentInteraction.interaction_at.desc())
        .limit(8)
        .all()
    )
    now = utcnow()
    headline = "Seu resumo do dia está pronto."
    if priorities_data:
        headline = f"{len(priorities_data)} aluno(s) precisam de atenção hoje."
    return {
        "headline": {
            "title": "Resumo do dia",
            "subtitle": headline,
            "dateLabel": now.strftime("%A, %d/%m"),
        },
        "priorities": [
            {
                "studentId": str(student.id),
                "studentName": student.full_name,
                "reason": result.insight,
                "impact": "Risco operacional de perda de aderência" if result.score < 60 else "Acompanhar de perto hoje",
                "cta": "message" if result.status == "no_signal" else "open",
                "priorityScore": priority_score,
                "reasons": reasons,
            }
            for student, result, operational, priority_score, reasons in priorities_data
        ],
        "aiSuggestions": [
            {
                "id": str(item.id),
                "studentId": str(item.student_id) if item.student_id else None,
                "text": item.title,
                "priority": item.priority,
            }
            for item in suggestions
        ],
        "recentActivity": [
            {
                "studentId": str(item.student_id),
                "text": item.title,
                "interpret": item.body or "",
                "when": item.interaction_at.isoformat(),
            }
            for item in recent_activity
        ],
        "studentsNeedingAttention": [serialize_student_list_item(student) for student, _, _, _, _ in priorities_data],
        "metrics": {
            "studentsCount": len(students),
            "attentionCount": len([1 for _, _, operational in scored if operational["status"] in {"attention", "cooling", "risk"}]),
            "healthyCount": len([1 for _, _, operational in scored if operational["status"] == "ok"]),
        },
    }
