from __future__ import annotations

from app.files.models import StudentFile
from app.insights.models import AIInsight
from app.messaging.models import SuggestedMessage
from app.reports.models import GeneratedReport
from app.students.services import (
    compute_student_score,
    get_active_workout,
    get_latest_summary,
    get_recent_interactions,
    get_recent_signals,
    serialize_student_list_item,
)
from app.workouts.services import serialize_workout_plan


def get_student_panel(student) -> dict:
    score = compute_student_score(student)
    latest_summary = get_latest_summary(student.id)
    workout = get_active_workout(student.id)
    files = (
        StudentFile.query.filter_by(student_id=student.id, deleted_at=None)
        .order_by(StudentFile.uploaded_at.desc())
        .limit(10)
        .all()
    )
    insights = (
        AIInsight.query.filter_by(student_id=student.id)
        .order_by(AIInsight.created_at.desc())
        .limit(5)
        .all()
    )
    messages = (
        SuggestedMessage.query.filter_by(student_id=student.id)
        .order_by(SuggestedMessage.created_at.desc())
        .limit(5)
        .all()
    )
    interactions = get_recent_interactions(student.id)
    signals = get_recent_signals(student.id)
    reports = (
        GeneratedReport.query.filter_by(student_id=student.id)
        .order_by(GeneratedReport.created_at.desc())
        .limit(5)
        .all()
    )

    suggested_message_text = (
        latest_summary.suggested_message_text
        if latest_summary and latest_summary.suggested_message_text
        else (messages[0].edited_message_text or messages[0].message_text if messages else "")
    )
    list_item = serialize_student_list_item(student)
    today_meals = [
        {
            "label": signal.title,
            "time": signal.created_at.strftime("%H:%M"),
            "ok": signal.signal_type == "meal",
        }
        for signal in signals
        if signal.signal_type in {"meal", "workout"}
    ][:4]
    return {
        "header": {
            "id": str(student.id),
            "name": student.full_name,
            "initials": list_item["initials"],
            "goal": list_item["goal"],
            "status": list_item["status"],
            "lastContact": list_item["lastContact"],
        },
        "score": {
            "value": score.score,
            "trend": {"stable": "flat", "up": "up", "down": "down"}.get(score.trend, "flat"),
            "label": score.insight,
            "adherence": score.score,
        },
        "smartSummary": {
            "insight": latest_summary.ai_reading_text if latest_summary else score.insight,
            "overall": latest_summary.overall_summary_text if latest_summary else student.last_signal_summary,
        },
        "today": {
            "meals": today_meals,
            "lastSignal": student.last_signal_summary or "Sem sinais hoje",
            "plannedWorkout": workout.title if workout else "Sem ficha ativa",
            "aiReading": latest_summary.ai_reading_text if latest_summary else score.insight,
            "impact": latest_summary.suggested_adjustment_text if latest_summary else "Manter acompanhamento humano com leitura pragmática.",
        },
        "suggestionOfDay": {
            "title": insights[0].title if insights else "Fazer follow-up leve",
            "body": insights[0].body if insights else "Valide como o aluno está e ajuste o plano conforme a resposta.",
            "message": suggested_message_text,
        },
        "data": {
            "notes": student.notes,
            "birthDate": student.birth_date.isoformat() if student.birth_date else None,
            "sex": student.sex,
            "heightCm": float(student.height_cm) if student.height_cm is not None else None,
            "currentWeightKg": float(student.current_weight_kg) if student.current_weight_kg is not None else None,
        },
        "workout": serialize_workout_plan(workout),
        "files": [
            {
                "id": str(item.id),
                "type": item.file_category,
                "name": item.title,
                "date": item.uploaded_at.isoformat(),
                "status": item.extraction_status,
                "summary": item.ai_summary,
                "url": item.file_url,
            }
            for item in files
        ],
        "suggestedMessages": [
            {
                "id": str(item.id),
                "category": item.message_category,
                "text": item.edited_message_text or item.message_text,
                "status": item.status,
                "subjectHint": item.subject_hint,
            }
            for item in messages
        ],
        "interactions": [
            {
                "id": str(item.id),
                "kind": item.interaction_type,
                "label": item.title,
                "when": item.interaction_at.isoformat(),
                "body": item.body,
            }
            for item in interactions
        ],
        "reports": [
            {
                "id": str(item.id),
                "type": item.report_type,
                "status": item.status,
                "summaryText": item.summary_text,
                "fileUrl": item.file_url,
                "createdAt": item.created_at.isoformat(),
            }
            for item in reports
        ],
        "history": [
            {
                "id": str(item.id),
                "kind": item.signal_type,
                "label": item.title,
                "when": item.created_at.isoformat(),
                "body": item.body,
            }
            for item in signals
        ],
    }
