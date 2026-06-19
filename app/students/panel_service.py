from __future__ import annotations

from datetime import date

from app.files.models import StudentFile
from app.insights.models import AIInsight
from app.messaging.models import SuggestedMessage
from app.extensions import db
from app.operations.services import (
    build_student_timeline,
    evaluate_retention_automation,
    latest_operational_score,
    recompute_and_persist_score,
)
from app.physical.services import latest_physical_progress, list_physical_assessments, serialize_assessment
from app.reports.models import GeneratedReport
from app.students.models import StudentDailySignal
from app.students.services import (
    compute_student_score,
    get_active_workout,
    get_latest_summary,
    get_recent_interactions,
    get_recent_signals,
    get_student_access_status,
    serialize_student_list_item,
)
from app.whatsapp.services import list_student_whatsapp_suggestions, list_whatsapp_history, student_whatsapp_status
from app.workouts.services import list_student_sessions, list_student_workout_plans, serialize_workout_plan, summarize_workout_consistency


def get_student_panel(student) -> dict:
    score = compute_student_score(student)
    recompute_and_persist_score(student)
    operational_score = latest_operational_score(student)
    automation = evaluate_retention_automation(student)
    db.session.commit()
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
    workout_consistency = summarize_workout_consistency(student)
    workout_plans = list_student_workout_plans(account_id=student.account_id, student_id=student.id)
    sessions = list_student_sessions(account_id=student.account_id, student_id=student.id)[:6]
    physical_assessments = list_physical_assessments(account_id=student.account_id, student_id=student.id)[:6]
    physical_progress = latest_physical_progress(student.id)
    whatsapp_status = student_whatsapp_status(student)
    whatsapp_history = list_whatsapp_history(student)

    suggested_message_text = (
        latest_summary.suggested_message_text
        if latest_summary and latest_summary.suggested_message_text
        else (messages[0].edited_message_text or messages[0].message_text if messages else "")
    )
    list_item = serialize_student_list_item(student)
    today_signals = (
        StudentDailySignal.query.filter_by(student_id=student.id, signal_date=date.today())
        .order_by(StudentDailySignal.created_at.desc())
        .all()
    )
    today_meals = [
        {
            "label": signal.body or signal.title,
            "time": signal.created_at.strftime("%H:%M"),
            "ok": signal.signal_type == "meal",
            "estimatedCalories": (signal.payload_json or {}).get("estimated_calories"),
            "calorieRange": (signal.payload_json or {}).get("calorie_range"),
            "proteinGrams": (signal.payload_json or {}).get("protein_grams"),
            "carbsGrams": (signal.payload_json or {}).get("carbs_grams"),
            "fatsGrams": (signal.payload_json or {}).get("fats_grams"),
        }
        for signal in today_signals
        if signal.signal_type in {"meal", "workout"}
    ][:4]
    today_metrics = _build_today_metrics(today_meals, today_signals)
    today_reading = _build_today_reading(today_metrics, latest_summary.ai_reading_text if latest_summary else None)
    today_impact = _build_today_impact(today_metrics, latest_summary.suggested_adjustment_text if latest_summary else None, workout_consistency)
    smart_insight = today_reading if today_metrics["mealsCount"] > 0 else (latest_summary.ai_reading_text if latest_summary else score.insight)
    smart_overall = (
        _build_today_overall(today_metrics)
        if today_metrics["mealsCount"] > 0
        else (latest_summary.overall_summary_text if latest_summary else student.last_signal_summary)
    )
    return {
        "header": {
            "id": str(student.id),
            "name": student.full_name,
            "initials": list_item["initials"],
            "goal": list_item["goal"],
            "status": list_item["status"],
            "email": student.email,
            "portalAccessStatus": get_student_access_status(student),
            "lastContact": list_item["lastContact"],
        },
        "score": {
            "value": operational_score["score"],
            "trend": {"stable": "flat", "up": "up", "down": "down"}.get(operational_score["trend"], "flat"),
            "label": operational_score["reason"] or score.insight,
            "adherence": operational_score["score"],
        },
        "operationalScore": operational_score,
        "retentionAutomation": _serialize_automation_decision(automation),
        "smartSummary": {
            "insight": smart_insight,
            "overall": smart_overall,
        },
        "today": {
            "meals": today_meals,
            "metrics": today_metrics,
            "lastSignal": _build_today_last_signal(today_signals, student.last_signal_summary),
            "plannedWorkout": workout.title if workout else "Sem ficha ativa",
            "aiReading": today_reading,
            "impact": today_impact,
        },
        "suggestionOfDay": {
            "title": insights[0].title if insights else "Fazer follow-up leve",
            "body": insights[0].body if insights else "Valide como o aluno esta e ajuste o plano conforme a resposta.",
            "message": suggested_message_text,
        },
        "data": {
            "notes": student.notes,
            "email": student.email,
            "portalAccessStatus": get_student_access_status(student),
            "birthDate": student.birth_date.isoformat() if student.birth_date else None,
            "sex": student.sex,
            "heightCm": float(student.height_cm) if student.height_cm is not None else None,
            "currentWeightKg": float(student.current_weight_kg) if student.current_weight_kg is not None else None,
        },
        "workout": serialize_workout_plan(workout),
        "workoutPlans": workout_plans,
        "workoutHistory": sessions,
        "workoutConsistency": workout_consistency,
        "physicalAssessments": [serialize_assessment(item) for item in physical_assessments],
        "physicalProgress": physical_progress,
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
        "whatsapp": {
            "status": whatsapp_status,
            "history": whatsapp_history,
            "suggestions": list_student_whatsapp_suggestions(student),
        },
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
        "timeline": build_student_timeline(student),
    }


def _build_workout_action(consistency: dict) -> str:
    if consistency["skippedCount"] >= 2:
        return "Queda de consistencia detectada. Vale reduzir friccao e fazer reengajamento hoje."
    if consistency["completedCount"] >= 3:
        return "Boa consistencia na semana. Mantenha progressao simples e feedback positivo."
    return "Acompanhar a proxima sessao e validar se a ficha continua aderente a rotina."


def _build_today_metrics(today_meals: list[dict], today_signals: list[StudentDailySignal]) -> dict:
    meal_items = [item for item in today_meals if item["ok"]]
    calories_min = 0
    calories_max = 0
    protein = 0
    carbs = 0
    fats = 0

    for meal in meal_items:
        calorie_range = meal.get("calorieRange") or {}
        estimated = meal.get("estimatedCalories")
        min_value = calorie_range.get("min") if isinstance(calorie_range, dict) else None
        max_value = calorie_range.get("max") if isinstance(calorie_range, dict) else None
        if min_value is not None and max_value is not None:
            calories_min += int(min_value)
            calories_max += int(max_value)
        elif estimated is not None:
            calories_min += int(estimated)
            calories_max += int(estimated)
        protein += int(meal.get("proteinGrams") or 0)
        carbs += int(meal.get("carbsGrams") or 0)
        fats += int(meal.get("fatsGrams") or 0)

    workouts_count = sum(1 for signal in today_signals if signal.signal_type == "workout")
    return {
        "mealsCount": len(meal_items),
        "signalsCount": len(today_signals),
        "workoutsCount": workouts_count,
        "caloriesMin": calories_min if calories_min else None,
        "caloriesMax": calories_max if calories_max else None,
        "proteinGrams": protein if protein else None,
        "carbsGrams": carbs if carbs else None,
        "fatsGrams": fats if fats else None,
    }


def _format_calories(metrics: dict) -> str:
    min_value = metrics.get("caloriesMin")
    max_value = metrics.get("caloriesMax")
    if not min_value and not max_value:
        return "sem estimativa calorica"
    if min_value == max_value:
        return f"{min_value} kcal"
    return f"{min_value}-{max_value} kcal"


def _build_today_reading(metrics: dict, fallback: str | None) -> str:
    if metrics["mealsCount"] <= 0:
        return fallback or "Sem refeicoes registradas hoje."

    macros = []
    if metrics.get("proteinGrams"):
        macros.append(f"{metrics['proteinGrams']}g de proteina")
    if metrics.get("carbsGrams"):
        macros.append(f"{metrics['carbsGrams']}g de carboidratos")
    if metrics.get("fatsGrams"):
        macros.append(f"{metrics['fatsGrams']}g de gordura")
    macro_text = "; ".join(macros)
    return (
        f"{metrics['mealsCount']} refeicoes registradas hoje, com cerca de {_format_calories(metrics)}."
        + (f" Estimativa de macros: {macro_text}." if macro_text else "")
    )


def _build_today_overall(metrics: dict) -> str:
    return (
        f"{metrics['mealsCount']} refeicoes hoje; {_format_calories(metrics)}"
        + (f"; {metrics['proteinGrams']}g de proteina" if metrics.get("proteinGrams") else "")
    )


def _build_today_impact(metrics: dict, fallback: str | None, consistency: dict) -> str:
    if metrics["mealsCount"] <= 0:
        return fallback or _build_workout_action(consistency)
    protein = metrics.get("proteinGrams") or 0
    carbs = metrics.get("carbsGrams") or 0
    fats = metrics.get("fatsGrams") or 0
    if protein and protein < 90:
        return "Reforcar proteina nas proximas refeicoes pode melhorar saciedade e recuperacao."
    if carbs and carbs < 120:
        return "Se ainda houver treino ou baixa energia, incluir carboidrato simples e uma fonte de proteina ajuda."
    if fats and fats > 80:
        return "Boa hora para reduzir frituras e priorizar uma refeicao mais leve no restante do dia."
    return "Dia alimentar registrado. Manter hidratacao e fechar o dia com uma refeicao simples e proteica."


def _build_today_last_signal(today_signals: list[StudentDailySignal], fallback: str | None) -> str:
    if not today_signals:
        return fallback or "Sem sinais hoje"
    latest = today_signals[0]
    if latest.signal_type == "meal":
        return f"refeicao registrada as {latest.created_at.strftime('%H:%M')}"
    if latest.signal_type == "workout":
        return f"treino registrado as {latest.created_at.strftime('%H:%M')}"
    return latest.title or fallback or "Sinal registrado hoje"


def _serialize_automation_decision(decision) -> dict | None:
    if decision is None:
        return None
    return {
        "id": str(decision.id),
        "ruleType": decision.rule_type,
        "status": decision.status,
        "priority": decision.priority,
        "reason": decision.reason,
        "suggestedAction": decision.suggested_action,
        "suppressedUntil": decision.suppressed_until.isoformat() if decision.suppressed_until else None,
        "payload": decision.payload_json,
    }
