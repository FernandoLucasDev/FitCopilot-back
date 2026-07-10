from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.events.models import StudentHealthScore
from app.operations.models import AutomationDecision
from app.operations.services import emit_event
from app.students.models import StudentDailySignal, StudentProfile
from app.whatsapp.services import get_or_create_student_automations

NUTRITION_RULE_TYPES = ("nutrition_no_log_2d", "nutrition_over_target_3d")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _meal_signals_for_date(student: StudentProfile, day: date) -> list[StudentDailySignal]:
    return StudentDailySignal.query.filter_by(student_id=student.id, signal_date=day, signal_type="meal").all()


def _daily_calories_for_date(student: StudentProfile, day: date) -> int:
    total = 0
    for signal in _meal_signals_for_date(student, day):
        calories = (signal.payload_json or {}).get("estimated_calories")
        if isinstance(calories, int):
            total += calories
    return total


def weekly_food_summary(student: StudentProfile) -> dict:
    today = date.today()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    days_with_log = 0
    total_calories = 0
    total_protein = 0
    total_carbs = 0
    total_fats = 0
    days_within_target = 0
    days_evaluated_for_target = 0

    for day in days:
        signals = _meal_signals_for_date(student, day)
        if not signals:
            continue
        days_with_log += 1
        day_calories = 0
        for signal in signals:
            payload = signal.payload_json or {}
            calories = payload.get("estimated_calories")
            if isinstance(calories, int):
                total_calories += calories
                day_calories += calories
            for key, total_name in (("protein_grams", "protein"), ("carbs_grams", "carbs"), ("fats_grams", "fats")):
                value = payload.get(key)
                if isinstance(value, int):
                    if total_name == "protein":
                        total_protein += value
                    elif total_name == "carbs":
                        total_carbs += value
                    else:
                        total_fats += value
        if student.daily_calorie_target:
            days_evaluated_for_target += 1
            if day_calories <= student.daily_calorie_target:
                days_within_target += 1

    adherence_pct = round((days_within_target / days_evaluated_for_target) * 100) if days_evaluated_for_target else None
    return {
        "periodStart": days[0].isoformat(),
        "periodEnd": days[-1].isoformat(),
        "daysWithLog": days_with_log,
        "daysInPeriod": len(days),
        "avgCaloriesKcal": round(total_calories / days_with_log) if days_with_log else None,
        "avgProteinGrams": round(total_protein / days_with_log) if days_with_log else None,
        "avgCarbsGrams": round(total_carbs / days_with_log) if days_with_log else None,
        "avgFatsGrams": round(total_fats / days_with_log) if days_with_log else None,
        "calorieTargetKcal": student.daily_calorie_target,
        "targetAdherencePct": adherence_pct,
    }


@dataclass
class FoodScore:
    score: int
    level: str
    trend: str
    reason: str
    components: dict


def calculate_food_score(student: StudentProfile) -> FoodScore:
    today = date.today()
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    score = 70
    reasons: list[str] = []

    days_with_log = sum(1 for day in days if _meal_signals_for_date(student, day))
    components = {"days_with_log_7d": days_with_log}
    score += min(days_with_log * 4, 20)
    if days_with_log <= 1:
        score -= 25
        reasons.append("quase nenhum registro alimentar na semana")
    elif days_with_log <= 3:
        score -= 10
        reasons.append("registros alimentares irregulares")

    last_two_days = days[-2:]
    absence_streak = all(not _meal_signals_for_date(student, day) for day in last_two_days)
    components["absent_last_2_days"] = absence_streak
    if absence_streak:
        score -= 15
        reasons.append("sem registro nos últimos 2 dias")

    if student.daily_calorie_target:
        evaluated = [_daily_calories_for_date(student, day) for day in days if _meal_signals_for_date(student, day)]
        over_target_days = sum(1 for value in evaluated if value > student.daily_calorie_target)
        components["days_over_target_7d"] = over_target_days
        if evaluated:
            over_ratio = over_target_days / len(evaluated)
            if over_ratio >= 0.6:
                score -= 20
                reasons.append("meta calórica ultrapassada com frequência")
            elif over_ratio >= 0.3:
                score -= 8
                reasons.append("meta calórica ultrapassada algumas vezes")
            else:
                reasons.append("boa aderência à meta calórica")

    macro_variation = 0
    macro_days = [_meal_signals_for_date(student, day) for day in days if _meal_signals_for_date(student, day)]
    proteins = [
        sum((signal.payload_json or {}).get("protein_grams") or 0 for signal in day_signals) for day_signals in macro_days
    ]
    if len(proteins) >= 3:
        macro_variation = max(proteins) - min(proteins)
        components["protein_variation_7d"] = macro_variation
        if macro_variation > 120:
            score -= 5
            reasons.append("variação alta de proteína entre os dias")

    score = max(0, min(100, score))
    if score < 40:
        level = "risk"
    elif score < 58:
        level = "cooling"
    elif score < 72:
        level = "attention"
    else:
        level = "ok"

    previous = (
        StudentHealthScore.query.filter_by(student_id=student.id, score_type="food")
        .order_by(StudentHealthScore.computed_at.desc())
        .first()
    )
    if previous and score < previous.raw_score - 5:
        trend = "down"
    elif previous and score > previous.raw_score + 5:
        trend = "up"
    else:
        trend = "stable"

    if not reasons:
        reasons.append("rotina alimentar recente sem alerta importante")
    return FoodScore(score=score, level=level, trend=trend, reason="; ".join(reasons), components=components)


def recompute_and_persist_food_score(student: StudentProfile, *, emit: bool = True) -> StudentHealthScore:
    result = calculate_food_score(student)
    previous = (
        StudentHealthScore.query.filter_by(student_id=student.id, score_type="food")
        .order_by(StudentHealthScore.computed_at.desc())
        .first()
    )
    previous_score = previous.raw_score if previous else None
    previous_level = previous.level if previous else None
    snapshot = StudentHealthScore.query.filter_by(student_id=student.id, score_date=date.today(), score_type="food").first()
    if snapshot is None:
        snapshot = StudentHealthScore(
            account_id=student.account_id,
            student_id=student.id,
            score_date=date.today(),
            score_type="food",
            raw_score=result.score,
            level=result.level,
            trend=result.trend,
            components_json={"reason": result.reason, "factors": result.components},
            computed_at=utcnow(),
        )
        db.session.add(snapshot)
    else:
        snapshot.raw_score = result.score
        snapshot.level = result.level
        snapshot.trend = result.trend
        snapshot.components_json = {"reason": result.reason, "factors": result.components}
        snapshot.computed_at = utcnow()

    if emit and (previous_score is None or previous_level != result.level or abs((previous_score or 0) - result.score) >= 8):
        emit_event(
            account_id=student.account_id,
            student_id=student.id,
            event_type="food_score_changed",
            source="food_score_engine",
            title=f"Score alimentar: {result.score} ({result.level})",
            body=result.reason,
            severity="warning" if result.level in {"attention", "cooling"} else "critical" if result.level == "risk" else "info",
            payload={"score": result.score, "level": result.level, "previous_score": previous_score},
        )
    db.session.flush()
    return snapshot


def latest_food_score(student: StudentProfile) -> dict:
    snapshot = (
        StudentHealthScore.query.filter_by(student_id=student.id, score_type="food")
        .order_by(StudentHealthScore.computed_at.desc())
        .first()
    )
    if snapshot is None:
        snapshot = recompute_and_persist_food_score(student)
    components = snapshot.components_json or {}
    return {
        "score": snapshot.raw_score,
        "level": snapshot.level,
        "trend": snapshot.trend,
        "reason": components.get("reason") or "rotina alimentar recente sem alerta importante",
        "factors": components.get("factors") or {},
        "createdAt": snapshot.computed_at.isoformat(),
    }


def evaluate_nutrition_automation(student: StudentProfile) -> AutomationDecision | None:
    if getattr(student.account, "professional_vertical", None) != "nutricionista":
        return None

    rules_by_type = {rule.rule_type: rule for rule in get_or_create_student_automations(student)}
    now = utcnow()

    recent_decision = AutomationDecision.query.filter(
        AutomationDecision.student_id == student.id,
        AutomationDecision.rule_type.in_(NUTRITION_RULE_TYPES),
        AutomationDecision.created_at >= now - timedelta(hours=48),
    ).first()
    if recent_decision is not None:
        return recent_decision

    today = date.today()
    no_log_rule = rules_by_type.get("nutrition_no_log_2d")
    if no_log_rule and no_log_rule.is_active:
        last_two_days = [today - timedelta(days=offset) for offset in range(0, 2)]
        if all(not _meal_signals_for_date(student, day) for day in last_two_days):
            return _create_nutrition_decision(
                student,
                rule_type="nutrition_no_log_2d",
                priority="medium",
                reason="paciente sem registrar refeição há 2 dias",
                action="Enviar mensagem de incentivo para retomar o registro alimentar",
                cooldown_hours=48,
            )

    over_target_rule = rules_by_type.get("nutrition_over_target_3d")
    if over_target_rule and over_target_rule.is_active and student.daily_calorie_target:
        last_three_days = [today - timedelta(days=offset) for offset in range(0, 3)]
        over_target_days = [
            day for day in last_three_days if _meal_signals_for_date(student, day) and _daily_calories_for_date(student, day) > student.daily_calorie_target
        ]
        if len(over_target_days) == 3:
            return _create_nutrition_decision(
                student,
                rule_type="nutrition_over_target_3d",
                priority="high",
                reason="meta calórica ultrapassada 3 dias seguidos",
                action="Avisar o profissional para revisar orientações com o paciente",
                cooldown_hours=48,
            )

    return None


def _create_nutrition_decision(
    student: StudentProfile, *, rule_type: str, priority: str, reason: str, action: str, cooldown_hours: int
) -> AutomationDecision:
    now = utcnow()
    decision = AutomationDecision(
        account_id=student.account_id,
        student_id=student.id,
        rule_type=rule_type,
        status="suggested",
        priority=priority,
        reason=reason,
        suggested_action=action,
        suppressed_until=now + timedelta(hours=cooldown_hours),
        payload_json={"rule_type": rule_type},
    )
    db.session.add(decision)
    emit_event(
        account_id=student.account_id,
        student_id=student.id,
        event_type="automation_suggested",
        source="nutrition_automation_engine",
        title=action,
        body=reason,
        severity="warning" if priority == "medium" else "critical",
        payload={"rule_type": rule_type, "cooldown_hours": cooldown_hours},
    )
    db.session.flush()
    return decision
