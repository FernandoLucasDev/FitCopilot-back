from __future__ import annotations

from datetime import datetime, timezone

from app.insights.models import AIInsight
from app.students.models import StudentInteraction, StudentProfile
from app.students.services import compute_student_score, serialize_student_list_item


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_workspace_overview(account_id) -> dict:
    students = StudentProfile.query.filter_by(account_id=account_id).all()
    scored = [(student, compute_student_score(student)) for student in students if student.status != "archived"]
    priorities = sorted(
        [item for item in scored if item[1].status in {"attention", "no_signal", "new"}],
        key=lambda item: item[1].score,
    )[:5]
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
    if priorities:
        headline = f"{len(priorities)} aluno(s) precisam de atenção hoje."
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
            }
            for student, result in priorities
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
        "studentsNeedingAttention": [serialize_student_list_item(student) for student, _ in priorities],
        "metrics": {
            "studentsCount": len(students),
            "attentionCount": len([1 for _, result in scored if result.status in {"attention", "no_signal"}]),
            "healthyCount": len([1 for _, result in scored if result.status == "active"]),
        },
    }
