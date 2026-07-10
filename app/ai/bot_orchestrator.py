from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from flask import current_app
from sqlalchemy.orm.attributes import flag_modified

from app.ai.local_agent import fitcopilot_agent
from app.extensions import db
from app.nutrition.services import evaluate_nutrition_automation, recompute_and_persist_food_score
from app.operations.services import emit_event, evaluate_retention_automation, recompute_and_persist_score
from app.students.models import StudentDailySignal, StudentDailySummary, StudentInteraction, StudentProfile
from app.workouts.services import (
    create_workout_session,
    get_active_workout_for_student,
    list_student_workout_plans,
    serialize_workout_plan,
    summarize_workout_consistency,
)


@dataclass
class BotReply:
    handled: bool
    reply_text: str
    next_phase: str | None = None
    metadata_patch: dict | None = None
    student_id: str | None = None
    student_name: str | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    if digits.startswith("55"):
        return digits
    if len(digits) in {10, 11}:
        return f"55{digits}"
    return digits


def phone_variants(phone: str | None) -> list[str]:
    normalized = normalize_phone(phone)
    if not normalized:
        return []
    variants = [normalized]
    if normalized.startswith("55") and len(normalized) == 13:
        area = normalized[2:4]
        local = normalized[4:]
        if local.startswith("9"):
            variants.append(f"55{area}{local[1:]}")
    elif normalized.startswith("55") and len(normalized) == 12:
        area = normalized[2:4]
        local = normalized[4:]
        variants.append(f"55{area}9{local}")
    return list(dict.fromkeys(variants))


def resolve_student_by_phone(phone: str | None) -> StudentProfile | None:
    variants = phone_variants(phone)
    if not variants:
        return None
    students = (
        StudentProfile.query.filter(StudentProfile.phone.isnot(None), StudentProfile.archived_at.is_(None))
        .order_by(StudentProfile.created_at.desc())
        .all()
    )
    for variant in variants:
        for student in students:
            if normalize_phone(student.phone) == variant:
                return student
    return None


def _latest_summary(student: StudentProfile) -> StudentDailySummary | None:
    return (
        StudentDailySummary.query.filter_by(student_id=student.id)
        .order_by(StudentDailySummary.summary_date.desc(), StudentDailySummary.updated_at.desc())
        .first()
    )


def _recent_interactions(student: StudentProfile) -> list[dict]:
    items = (
        StudentInteraction.query.filter_by(student_id=student.id)
        .order_by(StudentInteraction.interaction_at.desc())
        .limit(5)
        .all()
    )
    return [
        {
            "type": item.interaction_type,
            "title": item.title,
            "body": item.body,
            "at": item.interaction_at.isoformat(),
        }
        for item in items
    ]


def _active_workout_payload(student: StudentProfile) -> dict | None:
    plan = get_active_workout_for_student(student.id)
    return serialize_workout_plan(plan) if plan else None


def _student_workout_payloads(student: StudentProfile) -> list[dict]:
    plans = list_student_workout_plans(account_id=student.account_id, student_id=student.id)
    active = [plan for plan in plans if plan.get("status") == "active"]
    others = [plan for plan in plans if plan.get("status") != "active"]
    return active + others


def _today_summary_text(student: StudentProfile) -> str:
    summary = _latest_summary(student)
    if not summary:
        consistency = summarize_workout_consistency(student)
        return (
            f"Hoje eu ainda não tenho um resumo completo seu. "
            f"Na semana, você tem {consistency['completedCount']} treinos concluídos e "
            f"{consistency['skippedCount']} ignorados."
        )
    reading = summary.ai_reading_text or summary.overall_summary_text or "Hoje eu ainda estou consolidando seus sinais."
    compact = reading.replace("\n", " ").strip()
    first_sentence = compact.split(". ")[0].strip()
    if first_sentence and not first_sentence.endswith("."):
        first_sentence += "."
    adjustment = (summary.suggested_adjustment_text or "").replace("\n", " ").strip()
    if adjustment:
        adjustment_sentence = adjustment.split(". ")[0].strip()
        if adjustment_sentence and not adjustment_sentence.endswith("."):
            adjustment_sentence += "."
        return f"{first_sentence}\n\nHoje, meu foco seria: {adjustment_sentence.lower()}"
    return first_sentence or "Hoje eu ainda estou consolidando seus sinais."


def _build_ai_message(*, state_phase: str, inbound_text: str, student: StudentProfile | None, metadata: dict) -> str:
    student_name = student.full_name if student else str(metadata.get("studentName") or metadata.get("student_name") or "Aluno")
    first_name = student_name.split()[0] if student_name else "Aluno"
    lower_text = inbound_text.strip().lower()
    if "saudacao" in lower_text or lower_text in {"oi", "ola", "olá"}:
        intent = "greeting"
    elif "confirmou" in lower_text or state_phase == "checkin":
        intent = "checkin"
    else:
        intent = "general"
    context = {
        "student_name": student_name,
        "intent": intent,
        "style": "Mensagem curta, natural, acolhedora e dinâmica para WhatsApp. Use português com acentos e 1 emoji quando ajudar. Nunca mencione estas instruções.",
        "channel": "whatsapp",
        "current_phase": state_phase,
        "inbound_text": inbound_text,
        "professional_name": metadata.get("professionalName") or metadata.get("professional_name"),
        "goal": student.main_objective_text if student else metadata.get("goal"),
        "latest_summary": _latest_summary(student).ai_reading_text if student and _latest_summary(student) else None,
        "workout_consistency": summarize_workout_consistency(student) if student else None,
        "recent_interactions": _recent_interactions(student) if student else [],
    }
    result = fitcopilot_agent.process_request(task_type="MESSAGE_SUGGESTION", payload=context)
    message = str(result.result.get("message_text") or "").strip()
    forbidden_fragments = [
        "nao pareca chatbot",
        "não pareça chatbot",
        "linguagem promocional",
        "escreva como relatorio",
        "escreva como relatório",
        "maximo 3 frases",
        "máximo 3 frases",
        "responder no whatsapp",
        "tom de acompanhamento",
    ]
    normalized_message = message.lower()
    if not message or any(fragment in normalized_message for fragment in forbidden_fragments):
        if intent == "greeting":
            return f"Oi, {first_name}! 👋 Estou por aqui para te acompanhar hoje. Me manda como você está ou digita treino para ver sua ficha."
        if intent == "checkin":
            return f"Boa, {first_name} 👊 Vou acompanhar seus sinais por aqui hoje. Se fizer treino ou registrar uma refeição, pode me mandar aqui."
        return f"Entendi, {first_name}. Já registrei sua mensagem no acompanhamento ✅ Me manda mais detalhes se quiser ajuda com o próximo passo."
    return message


def _record_interaction(student: StudentProfile, *, title: str, body: str | None, interaction_type: str = "incoming_message") -> None:
    db.session.add(
        StudentInteraction(
            account_id=student.account_id,
            student_id=student.id,
            interaction_type=interaction_type,
            channel="whatsapp",
            title=title,
            body=body,
            created_by_user_id=None,
            interaction_at=utcnow(),
            created_at=utcnow(),
        )
    )


def _record_signal(student: StudentProfile, *, signal_type: str, title: str, body: str | None, payload: dict | None = None) -> None:
    db.session.add(
        StudentDailySignal(
            account_id=student.account_id,
            student_id=student.id,
            signal_date=date.today(),
            signal_type=signal_type,
            source="whatsapp",
            title=title,
            body=body,
            payload_json=payload or {},
            created_by_user_id=None,
            created_at=utcnow(),
        )
    )
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type={
            "workout": "workout_completed",
            "meal": "meal_logged",
            "absence": "absence_detected",
            "manual_note": "response_received",
        }.get(signal_type, "signal_recorded"),
        source="whatsapp_bot",
        title=title,
        body=body,
        severity="warning" if signal_type == "absence" else "info",
        payload=payload or {},
    )


def _sync_student_operations(student: StudentProfile) -> None:
    recompute_and_persist_score(student)
    evaluate_retention_automation(student)
    if getattr(student.account, "professional_vertical", None) == "nutricionista":
        recompute_and_persist_food_score(student)
        evaluate_nutrition_automation(student)


def _meal_signals_today(student: StudentProfile) -> list[StudentDailySignal]:
    return (
        StudentDailySignal.query.filter_by(student_id=student.id, signal_date=date.today(), signal_type="meal")
        .order_by(StudentDailySignal.created_at.asc())
        .all()
    )


def _daily_calories(student: StudentProfile) -> int:
    total = 0
    seen: set[str] = set()
    for signal in _meal_signals_today(student):
        key = _meal_dedupe_key(signal.body)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        calories = (signal.payload_json or {}).get("estimated_calories")
        if isinstance(calories, int):
            total += calories
    return total


def _daily_calorie_range(student: StudentProfile) -> tuple[int, int]:
    min_total = 0
    max_total = 0
    seen: set[str] = set()
    for signal in _meal_signals_today(student):
        key = _meal_dedupe_key(signal.body)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        payload = signal.payload_json or {}
        calories = payload.get("estimated_calories")
        calorie_range = payload.get("calorie_range") or {}
        min_calories = calorie_range.get("min") if isinstance(calorie_range, dict) else None
        max_calories = calorie_range.get("max") if isinstance(calorie_range, dict) else None
        if isinstance(min_calories, int) and isinstance(max_calories, int):
            min_total += min_calories
            max_total += max_calories
        elif isinstance(calories, int):
            min_total += calories
            max_total += calories
    return min_total, max_total


def _daily_macro_totals(student: StudentProfile) -> dict[str, int]:
    totals = {"protein": 0, "carbs": 0, "fats": 0}
    seen: set[str] = set()
    for signal in _meal_signals_today(student):
        key = _meal_dedupe_key(signal.body)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        payload = signal.payload_json or {}
        for payload_key, total_key in (
            ("protein_grams", "protein"),
            ("carbs_grams", "carbs"),
            ("fats_grams", "fats"),
        ):
            value = payload.get(payload_key)
            if isinstance(value, int):
                totals[total_key] += value
    return totals


def _meal_dedupe_key(text: str | None) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()
    return " ".join(normalized.split())


def _find_recent_duplicate_meal(student: StudentProfile, text: str) -> StudentDailySignal | None:
    key = _meal_dedupe_key(text)
    if not key:
        return None
    cutoff = utcnow() - timedelta(hours=2)
    for signal in _meal_signals_today(student):
        if _as_aware(signal.created_at) < cutoff:
            continue
        if _meal_dedupe_key(signal.body) == key:
            return signal
    return None


def _looks_like_meal_text(text: str) -> bool:
    keywords = [
        "cafe", "café", "almoco", "almoço", "janta", "jantar", "lanche", "whey", "banana",
        "arroz", "frango", "ovo", "pao", "pão", "carne", "macarrao", "macarrão", "iogurte",
        "refeicao", "refeição", "comi", "comendo", "salada", "pizza", "hamburg",
    ]
    lower = text.lower()
    return any(word in lower for word in keywords)


def _calorie_range_for_meal(text: str, estimated: int | None) -> tuple[int, int] | None:
    lower = text.lower()
    if (
        "arroz" in lower
        and "feij" in lower
        and ("bife" in lower or "carne" in lower or "steak" in lower)
        and ("batata" in lower or "frita" in lower)
    ):
        return (700, 950)
    if estimated is None:
        return None
    if estimated >= 650:
        margin = 120
        return (max(0, estimated - margin), estimated + margin)
    return None


def _format_calories(estimated: int | None, calorie_range: tuple[int, int] | None = None) -> str:
    if calorie_range:
        return f"entre {calorie_range[0]} e {calorie_range[1]} kcal"
    if estimated is None:
        return "sem estimativa calórica confiável"
    return f"cerca de {estimated} kcal"


def _format_total_calories(student: StudentProfile) -> str:
    min_total, max_total = _daily_calorie_range(student)
    if min_total <= 0 and max_total <= 0:
        return "Ainda não tenho um total calórico confiável do dia."
    if min_total == max_total:
        return f"Total estimado do dia até agora: {min_total} kcal."
    return f"Total estimado do dia até agora: entre {min_total} e {max_total} kcal."


def _format_calorie_target_progress(student: StudentProfile) -> str | None:
    target = student.daily_calorie_target
    if not target:
        return None
    min_total, max_total = _daily_calorie_range(student)
    consumed = max_total if max_total else min_total
    if not consumed:
        return None
    pct = round((consumed / target) * 100)
    return f"Você consumiu {pct}% da sua meta de {target} kcal hoje."


def _format_macros(protein: int | None, carbs: int | None, fats: int | None) -> str:
    parts = []
    if protein is not None:
        parts.append(f"{protein}g proteína")
    if carbs is not None:
        parts.append(f"{carbs}g carbo")
    if fats is not None:
        parts.append(f"{fats}g gordura")
    return "Macros estimados: " + " · ".join(parts) + "." if parts else "Macros ainda sem estimativa confiável."


def _daily_nutrition_guidance(student: StudentProfile, analysis) -> str:
    totals = _daily_macro_totals(student)
    tips: list[str] = []
    goal = (student.main_objective_text or "").lower()
    target = student.daily_calorie_target
    if target:
        min_total, max_total = _daily_calorie_range(student)
        consumed = max_total if max_total else min_total
        if consumed:
            remaining = target - consumed
            if remaining <= 0:
                tips.append("você já bateu a meta calórica de hoje — vale priorizar algo mais leve se ainda for comer")
            elif remaining <= target * 0.15:
                tips.append("está quase na meta calórica de hoje — considere fechar o dia com uma refeição mais leve")
    if totals["protein"] < 80:
        tips.append("tenta puxar mais proteína nas próximas refeições")
    if ("massa" in goal or "hipertrof" in goal) and totals["carbs"] < 160:
        tips.append("mantém uma boa fonte de carboidrato para sustentar treino e ganho de massa")
    if totals["fats"] > 80:
        tips.append("segura um pouco as frituras/gorduras no restante do dia")
    if not tips and totals["carbs"] < 90:
        tips.append("se ainda for treinar, coloca um carboidrato simples antes ou depois")
    if tips:
        return "Dica: " + "; ".join(tips[:2]) + "."
    return analysis.guidance_text


def _workout_choices(student: StudentProfile) -> list[dict]:
    plans = _student_workout_payloads(student)
    if not plans:
        return []
    choices: list[dict] = []
    index = 1
    for plan in plans:
        for day in plan.get("days", []):
            choices.append(
                {
                    "key": str(index),
                    "planId": str(plan["id"]),
                    "planTitle": str(plan["title"]),
                    "planStatus": str(plan.get("status") or "draft"),
                    "label": str(day["label"]),
                    "dayId": str(day["id"]),
                    "exerciseCount": len(day["exercises"]),
                }
            )
            index += 1
    return choices


def _format_workout_choices(student: StudentProfile) -> str:
    plans = _student_workout_payloads(student)
    if not plans:
        return "Hoje eu ainda não encontrei uma ficha sua. Me chama aqui que eu sinalizo isso para seu profissional ✅"
    choices = _workout_choices(student)
    days_by_id = {str(day["id"]): (plan, day) for plan in plans for day in plan.get("days", [])}
    lines = []
    for item in choices:
        plan_day = days_by_id.get(str(item["dayId"]))
        plan, day = plan_day if plan_day else ({}, None)
        exercises = day.get("exercises", []) if day else []
        exercise_names = [str(exercise.get("exerciseName") or "").strip() for exercise in exercises]
        exercise_names = [name for name in exercise_names if name]
        preview = ", ".join(exercise_names[:4])
        remaining_count = max(len(exercise_names) - 4, 0)
        suffix = f" + {remaining_count} mais" if remaining_count else ""
        plan_label = f"{plan.get('title', item['planTitle'])} · " if len(plans) > 1 else ""
        if preview:
            lines.append(f"{item['key']}. {plan_label}{item['label']} ({item['exerciseCount']} exercícios)\n   {preview}{suffix}")
        else:
            lines.append(f"{item['key']}. {plan_label}{item['label']} ({item['exerciseCount']} exercícios)")
    active_title = plans[0]["title"] if plans and plans[0].get("status") == "active" else "suas fichas"
    return (
        f"Hoje você tem {active_title} 💪\n\n"
        f"Me diz qual treino você vai fazer:\n" +
        "\n".join(lines) +
        "\n\nPode responder com o número ou com o nome do treino."
    )

    workout = _active_workout_payload(student)
    if not workout:
        return "Hoje eu ainda não encontrei uma ficha ativa sua. Me chama aqui que eu sinalizo isso para seu profissional ✅"
    choices = _workout_choices(student)
    lines = []
    days_by_id = {str(day["id"]): day for day in workout["days"]}
    for item in choices:
        day = days_by_id.get(str(item["dayId"]))
        exercises = day.get("exercises", []) if day else []
        exercise_names = [str(exercise.get("exerciseName") or "").strip() for exercise in exercises]
        exercise_names = [name for name in exercise_names if name]
        preview = ", ".join(exercise_names[:4])
        remaining_count = max(len(exercise_names) - 4, 0)
        suffix = f" + {remaining_count} mais" if remaining_count else ""
        if preview:
            lines.append(f"{item['key']}. {item['label']} ({item['exerciseCount']} exercícios)\n   {preview}{suffix}")
        else:
            lines.append(f"{item['key']}. {item['label']} ({item['exerciseCount']} exercícios)")
    return (
        f"Hoje você tem a ficha {workout['title']} 💪\n\n"
        f"Me diz qual treino você vai fazer:\n" +
        "\n".join(lines) +
        "\n\nPode responder com o número ou com o nome do treino."
    )


def _resolve_workout_choice(student: StudentProfile, text: str, metadata: dict) -> dict | None:
    plans = _student_workout_payloads(student)
    if not plans:
        return None
    choices = metadata.get("availableWorkouts") or _workout_choices(student)
    normalized = text.strip().lower()
    for item in choices:
        key = str(item.get("key", "")).lower()
        label = str(item.get("label", "")).lower()
        plan_title = str(item.get("planTitle", "")).lower()
        if normalized == key or normalized == label or normalized == label.replace("treino ", "") or normalized == plan_title:
            day_id = str(item.get("dayId"))
            for plan in plans:
                for day in plan.get("days", []):
                    if str(day["id"]) == day_id or str(day["label"]).lower() == label:
                        return {**day, "planId": str(plan["id"]), "planTitle": str(plan["title"])}
    return None

    workout = _active_workout_payload(student)
    if not workout:
        return None
    choices = metadata.get("availableWorkouts") or _workout_choices(student)
    normalized = text.strip().lower()
    for item in choices:
        key = str(item.get("key", "")).lower()
        label = str(item.get("label", "")).lower()
        if normalized == key or normalized == label or normalized == label.replace("treino ", ""):
            day_id = str(item.get("dayId"))
            for day in workout["days"]:
                if str(day["id"]) == day_id or str(day["label"]).lower() == label:
                    return day
    return None


def _selected_workout_insight(student: StudentProfile, day: dict) -> str:
    ai_provider = current_app.extensions["ai_provider"]
    daily_calories = _daily_calories(student)
    summary = _latest_summary(student)
    insight = ai_provider.generate_workout_insight(
        context={
            "student_name": student.full_name,
            "goal": student.main_objective_text,
            "workout_label": day["label"],
            "exercises": [item["exerciseName"] for item in day["exercises"][:6]],
            "daily_calories": daily_calories,
            "latest_summary": summary.ai_reading_text if summary else None,
            "workout_consistency": summarize_workout_consistency(student),
        }
    )
    return (
        f"{day['label']} escolhido ✅\n\n"
        f"{insight}\n\n"
        "Pode me mandar as cargas também. Ex: supino 40kg, remada 35kg. Pode ser tudo junto ou exercício por exercício."
    )


def _parse_workout_loads(text: str, day: dict | None) -> list[dict]:
    if not day:
        return []
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    logs: list[dict] = []
    for exercise in day.get("exercises", []):
        name = str(exercise.get("exerciseName") or "").strip()
        if not name:
            continue
        name_key = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
        tokens = [token for token in re.split(r"\s+", name_key) if len(token) >= 4]
        if not tokens or not any(token in normalized for token in tokens[:3]):
            continue
        match = re.search(rf"{re.escape(tokens[0])}[^,\n;]*?(\d+(?:[,.]\d+)?)\s*(kg|kgs|quilos?)", normalized)
        if match:
            value = match.group(1).replace(",", ".")
            logs.append(
                {
                    "exercise_name": name,
                    "sets_completed": exercise.get("setsCount"),
                    "reps_completed": exercise.get("repsText"),
                    "notes": f"Carga informada no WhatsApp: {value} kg",
                }
            )
    return logs


def _selected_workout_day_from_metadata(student: StudentProfile, metadata: dict) -> dict | None:
    day_id = str(metadata.get("selectedWorkoutDayId") or "")
    if not day_id:
        return None
    for plan in _student_workout_payloads(student):
        for day in plan.get("days", []):
            if str(day.get("id")) == day_id:
                return {**day, "planId": str(plan["id"]), "planTitle": str(plan["title"])}
    return None


def _register_workout_loads_from_whatsapp(student: StudentProfile, text: str, metadata: dict) -> tuple[bool, int]:
    day = _selected_workout_day_from_metadata(student, metadata)
    logs = _parse_workout_loads(text, day)
    plan_id = metadata.get("selectedWorkoutPlanId") or (day or {}).get("planId")
    if not day or not plan_id or not logs:
        return False, 0
    payload = SimpleNamespace(
        student_id=str(student.id),
        plan_id=str(plan_id),
        date=date.today(),
        status="completed",
        notes=f"Cargas registradas via WhatsApp para {day.get('label')}.",
        exercises=[SimpleNamespace(**item) for item in logs],
    )
    create_workout_session(account_id=student.account_id, actor_user_id=None, data=payload)
    return True, len(logs)


def _is_image_message(message_type: str) -> bool:
    return message_type in {"image", "imageMessage", "media"}


def _link_recent_meal_photo(student: StudentProfile):
    from app.files.models import StudentFile

    cutoff = utcnow() - timedelta(minutes=30)
    return (
        StudentFile.query.filter(
            StudentFile.student_id == student.id,
            StudentFile.file_category == "meal_photo",
            StudentFile.uploaded_at >= cutoff,
        )
        .order_by(StudentFile.uploaded_at.desc())
        .first()
    )


def _handle_meal(student: StudentProfile, text: str, *, message_type: str, metadata: dict | None = None) -> BotReply:
    metadata = metadata or {}
    if _is_image_message(message_type) and not text.strip():
        return BotReply(
            handled=True,
            reply_text="Recebi a foto da sua refeição 👀\n\nMe manda uma descrição curta do prato para eu estimar melhor. Ex.: arroz, frango, feijão e salada.",
            next_phase="checkin",
            metadata_patch={"pendingMealPhoto": True, "pendingMealPhotoAt": utcnow().isoformat()},
            student_id=str(student.id),
            student_name=student.full_name,
        )

    duplicate = _find_recent_duplicate_meal(student, text)
    if duplicate is not None:
        calorie_range = _calorie_range_for_meal(text, (duplicate.payload_json or {}).get("estimated_calories"))
        if calorie_range:
            payload = {**(duplicate.payload_json or {})}
            payload["estimated_calories"] = round((calorie_range[0] + calorie_range[1]) / 2)
            payload["calorie_range"] = {"min": calorie_range[0], "max": calorie_range[1]}
            duplicate.payload_json = payload
            flag_modified(duplicate, "payload_json")
            _sync_student_operations(student)
            db.session.commit()
        return BotReply(
            handled=True,
            reply_text=f"Essa refeição já estava registrada aqui ✅\n\n{_format_total_calories(student)}",
            next_phase="checkin",
            metadata_patch={"pendingMealPhoto": False, "pendingMealPhotoAt": None},
            student_id=str(student.id),
            student_name=student.full_name,
        )

    if False and _is_image_message(message_type) and not text.strip():
        return BotReply(
            handled=True,
            reply_text=(
                "Recebi sua foto 👀\n\n"
                "Pra eu estimar melhor essa refeição sem gastar IA à toa, me manda uma descrição curta do que tinha no prato."
            ),
            next_phase="checkin",
            student_id=str(student.id),
            student_name=student.full_name,
        )

    ai_provider = current_app.extensions["ai_provider"]
    analysis = ai_provider.analyze_meal(
        context={
            "student_name": student.full_name,
            "goal": student.main_objective_text,
            "meal_description": text,
            "latest_summary": _latest_summary(student).ai_reading_text if _latest_summary(student) else None,
        }
    )
    calorie_range = _calorie_range_for_meal(text, analysis.estimated_calories)
    estimated_calories = analysis.estimated_calories
    if calorie_range and estimated_calories is not None:
        estimated_calories = round((calorie_range[0] + calorie_range[1]) / 2)
    _record_interaction(student, title="Refeição recebida no WhatsApp", body=text)
    linked_photo = _link_recent_meal_photo(student)
    _record_signal(
        student,
        signal_type="meal",
        title="Refeição registrada pelo WhatsApp",
        body=text,
        payload={
            "estimated_calories": estimated_calories,
            "calorie_range": {"min": calorie_range[0], "max": calorie_range[1]} if calorie_range else None,
            "protein_grams": analysis.protein_grams,
            "carbs_grams": analysis.carbs_grams,
            "fats_grams": analysis.fats_grams,
            "message_type": message_type,
            "linked_photo": bool(metadata.get("pendingMealPhoto")) or linked_photo is not None,
            "items": analysis.items,
            "confidence": analysis.confidence,
            "photo_file_id": str(linked_photo.id) if linked_photo else None,
            "photo_url": linked_photo.file_url if linked_photo else None,
        },
    )
    _sync_student_operations(student)
    db.session.commit()
    macros_text = _format_macros(analysis.protein_grams, analysis.carbs_grams, analysis.fats_grams)
    guidance_text = _daily_nutrition_guidance(student, analysis)
    target_progress_text = _format_calorie_target_progress(student)
    reply = (
        f"Registrei sua refeição com {_format_calories(estimated_calories, calorie_range)} 🍽️\n"
        f"{_format_total_calories(student)}\n"
        + (f"{target_progress_text}\n" if target_progress_text else "")
        + f"\n{macros_text}\n"
        f"{guidance_text}"
    )
    return BotReply(
        handled=True,
        reply_text=reply,
        next_phase="checkin",
        metadata_patch={"pendingMealPhoto": False, "pendingMealPhotoAt": None},
        student_id=str(student.id),
        student_name=student.full_name,
    )


def reply_for_whatsapp(*, phone_number: str | None, text: str | None, message_type: str, state_phase: str, metadata: dict | None = None) -> BotReply:
    metadata = metadata or {}
    normalized_text = (text or "").strip()
    normalized_lower = normalized_text.lower()
    student = resolve_student_by_phone(phone_number)

    if state_phase == "idle" and normalized_lower == "sim":
        return BotReply(
            handled=True,
            reply_text=_build_ai_message(
                state_phase="checkin",
                inbound_text="Aluno confirmou que quer começar hoje.",
                student=student,
                metadata=metadata,
            ),
            next_phase="checkin",
            metadata_patch={"onboardingStatus": "confirmed"},
            student_id=str(student.id) if student else None,
            student_name=student.full_name if student else None,
        )

    if state_phase == "idle" and normalized_lower == "mais tarde":
        return BotReply(
            handled=True,
            reply_text="Sem problema 🙂 Quando fizer sentido para você, me chama por aqui e a gente continua de forma leve.",
            next_phase="idle",
            metadata_patch={"onboardingStatus": "deferred"},
            student_id=str(student.id) if student else None,
            student_name=student.full_name if student else None,
        )

    if not student:
        return BotReply(
            handled=True,
            reply_text=_build_ai_message(
                state_phase=state_phase,
                inbound_text=normalized_text or "mensagem vazia",
                student=None,
                metadata=metadata,
            ),
            next_phase=state_phase,
        )

    if normalized_lower in {"oi", "ola", "olá"}:
        return BotReply(
            handled=True,
            reply_text=_build_ai_message(
                state_phase=state_phase,
                inbound_text="Aluno iniciou conversa com uma saudação no WhatsApp.",
                student=student,
                metadata=metadata,
            ),
            next_phase=state_phase,
            student_id=str(student.id),
            student_name=student.full_name,
        )

    if normalized_lower in {"hoje", "status"}:
        return BotReply(
            handled=True,
            reply_text=_today_summary_text(student),
            next_phase="checkin",
            student_id=str(student.id),
            student_name=student.full_name,
        )

    if normalized_lower == "treino" or normalized_lower == "ficha":
        choices = _workout_choices(student)
        return BotReply(
            handled=True,
            reply_text=_format_workout_choices(student),
            next_phase="treino_ativo",
            metadata_patch={"availableWorkouts": choices, "awaitingWorkoutChoice": True},
            student_id=str(student.id),
            student_name=student.full_name,
        )

    if metadata.get("awaitingWorkoutChoice"):
        day = _resolve_workout_choice(student, normalized_text, metadata)
        if day:
            _record_interaction(student, title=f"Treino escolhido no WhatsApp: {day['label']}", body=normalized_text)
            _record_signal(
                student,
                signal_type="manual_note",
                title=f"Aluno escolheu {day['label']} no WhatsApp",
                body=normalized_text,
                payload={"workout_day_id": str(day["id"]), "label": str(day["label"])},
            )
            _sync_student_operations(student)
            db.session.commit()
            return BotReply(
                handled=True,
                reply_text=_selected_workout_insight(student, day),
                next_phase="treino_ativo",
                metadata_patch={
                    "awaitingWorkoutChoice": False,
                    "selectedWorkoutLabel": day["label"],
                    "selectedWorkoutDayId": str(day["id"]),
                    "selectedWorkoutPlanId": str(day.get("planId") or ""),
                },
                student_id=str(student.id),
                student_name=student.full_name,
            )

    if metadata.get("selectedWorkoutDayId") and re.search(r"\d+(?:[,.]\d+)?\s*(kg|kgs|quilo|quilos)", normalized_lower):
        registered, count = _register_workout_loads_from_whatsapp(student, normalized_text, metadata)
        if registered:
            _record_interaction(student, title="Cargas de treino registradas no WhatsApp", body=normalized_text)
            _sync_student_operations(student)
            db.session.commit()
            return BotReply(
                handled=True,
                reply_text=f"Registrei as cargas de {count} exercício(s) ✅ Pode mandar mais cargas ou responder como foi: leve, normal ou pesado.",
                next_phase="treino_ativo",
                student_id=str(student.id),
                student_name=student.full_name,
            )

    if normalized_lower in {"terminei", "conclui", "concluido", "concluído", "ja treinei", "já treinei"}:
        selected = metadata.get("selectedWorkoutLabel") or "seu treino"
        _record_interaction(student, title="Treino concluído sinalizado no WhatsApp", body=normalized_text)
        _record_signal(
            student,
            signal_type="workout",
            title=f"Aluno concluiu {selected} pelo WhatsApp",
            body=normalized_text,
            payload={"selected_workout": selected},
        )
        _sync_student_operations(student)
        db.session.commit()
        return BotReply(
            handled=True,
            reply_text="Boa 👊 Como foi o treino hoje? Pode responder: leve, normal ou pesado.",
            next_phase="finalizacao",
            metadata_patch={"awaitingWorkoutFeedback": True},
            student_id=str(student.id),
            student_name=student.full_name,
        )

    if metadata.get("awaitingWorkoutFeedback") and normalized_lower in {"leve", "normal", "pesado"}:
        _record_interaction(student, title="Feedback de treino no WhatsApp", body=normalized_text)
        _record_signal(
            student,
            signal_type="manual_note",
            title="Aluno registrou percepção do treino",
            body=normalized_text,
            payload={"perceived_effort": normalized_lower},
        )
        _sync_student_operations(student)
        db.session.commit()
        return BotReply(
            handled=True,
            reply_text="Perfeito ✅ Já registrei como foi seu treino. Se mandar refeições ao longo do dia, eu junto tudo para te acompanhar melhor.",
            next_phase="checkin",
            metadata_patch={"awaitingWorkoutFeedback": False},
            student_id=str(student.id),
            student_name=student.full_name,
        )

    if _is_image_message(message_type) or _looks_like_meal_text(normalized_text):
        return _handle_meal(student, normalized_text, message_type=message_type, metadata=metadata)

    return BotReply(
        handled=True,
        reply_text=_build_ai_message(
            state_phase=state_phase,
            inbound_text=normalized_text or "mensagem vazia",
            student=student,
            metadata=metadata,
        ),
        next_phase=state_phase,
        student_id=str(student.id),
        student_name=student.full_name,
    )
