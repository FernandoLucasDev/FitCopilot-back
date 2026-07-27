from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

from app.physical.models import PhysicalAssessment
from app.students.models import StudentDailySignal
from app.workouts.services import list_student_sessions

# A carga fica embutida em `repsCompleted`/`notes` (ex.: "10 series · carga 40kg"),
# no mesmo formato que o portal do aluno grava. Extraimos o primeiro "<n>kg".
_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg\b", re.IGNORECASE)

# Tolerancia (kg) pra considerar "mesma carga" — evita marcar micro-variacao como progresso.
_TOLERANCE_KG = 0.5


def _extract_weight(*values) -> float | None:
    for value in values:
        if not value:
            continue
        match = _WEIGHT_RE.search(str(value))
        if match:
            return float(match.group(1).replace(",", "."))
    return None


def analyze_load_progression(student, *, max_exercises: int = 4, min_points: int = 2) -> list[dict]:
    """Feedback de IA custo-zero (heuristico): detecta progressao/plato/regressao de
    carga por exercicio a partir das sessoes ja registradas. Prioriza os casos
    acionaveis (plato/regressao) pro profissional agir. Nunca prescreve — so aponta.
    Defensivo: qualquer erro retorna [] pra nao quebrar o painel."""
    try:
        sessions = list_student_sessions(account_id=student.account_id, student_id=student.id)
    except Exception:
        return []

    # sessoes vem mais-recente-primeiro; montamos a serie cronologica (antiga -> nova).
    series: dict[str, list[float]] = {}
    for session in reversed(sessions):
        if session.get("status") == "skipped":
            continue
        for exercise in session.get("exercises", []):
            name = str(exercise.get("exerciseName") or "").strip()
            if not name:
                continue
            weight = _extract_weight(exercise.get("repsCompleted"), exercise.get("notes"))
            if weight is None:
                continue
            series.setdefault(name, []).append(weight)

    results: list[dict] = []
    for name, weights in series.items():
        if len(weights) < min_points:
            status = "new"
            delta = 0.0
        else:
            delta = weights[-1] - weights[0]
            recent = weights[-3:]
            if len(weights) >= 3 and (max(recent) - min(recent)) < _TOLERANCE_KG:
                status = "plateau"
            elif delta > _TOLERANCE_KG:
                status = "progressing"
            elif delta < -_TOLERANCE_KG:
                status = "regressing"
            else:
                status = "plateau"
        results.append(
            {
                "exerciseName": name,
                "series": [round(w, 1) for w in weights[-8:]],
                "sessions": len(weights),
                "latestKg": round(weights[-1], 1) if weights else None,
                "deltaKg": round(delta, 1),
                "status": status,
            }
        )

    # Ordena: plato e regressao primeiro (acionaveis), depois mais historico.
    priority = {"plateau": 0, "regressing": 1, "progressing": 2, "new": 3}
    results.sort(key=lambda item: (priority.get(item["status"], 9), -item["sessions"]))
    return results[:max_exercises]


# ---------------------------------------------------------------------------
# Sinais do coach — feedbacks de IA custo-zero (heuristicos) pro painel.
# Cada heuristica retorna um dict {kind, severity, title, detail, meta} ou None.
# Ordem de exibicao por acionabilidade (critical > warning > positive > info).
# Tudo defensivo: qualquer erro numa heuristica e ignorado, nunca quebra o painel.
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "positive": 2, "info": 3}

_WEEKDAY_PT = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def build_coach_signals(student) -> list[dict]:
    """Agrega os feedbacks heuristicos custo-zero do painel do profissional."""
    signals: list[dict] = []
    for builder in (
        _signal_routine_anomaly,
        _signal_streak,
        _signal_reassessment_due,
        _signal_goal_projection,
        _signal_training_nutrition_coherence,
        _signal_overtraining,
        _signal_best_time,
    ):
        try:
            result = builder(student)
        except Exception:
            result = None
        if result:
            signals.append(result)
    signals.sort(key=lambda item: _SEVERITY_ORDER.get(item.get("severity"), 9))
    return signals


def _linear_slope(xs: list[float], ys: list[float]) -> float | None:
    """Coeficiente angular por minimos quadrados; None se indeterminado."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom


def _signal_routine_anomaly(student) -> dict | None:
    """Queda abrupta no volume de registros vs a semana anterior."""
    today = date.today()
    week_ago = today - timedelta(days=7)
    two_weeks = today - timedelta(days=14)
    recent = StudentDailySignal.query.filter(
        StudentDailySignal.student_id == student.id,
        StudentDailySignal.signal_date > week_ago,
    ).count()
    prior = StudentDailySignal.query.filter(
        StudentDailySignal.student_id == student.id,
        StudentDailySignal.signal_date > two_weeks,
        StudentDailySignal.signal_date <= week_ago,
    ).count()
    if prior >= 3 and recent <= prior * 0.4:
        return {
            "kind": "routine_anomaly",
            "severity": "warning",
            "title": "Queda na rotina de registros",
            "detail": f"{recent} sinais nos ultimos 7 dias vs {prior} na semana anterior. Vale um check-in.",
            "meta": {"recent": recent, "prior": prior},
        }
    return None


def _signal_streak(student) -> dict | None:
    """Reforco positivo: dias consecutivos com registro terminando hoje/ontem."""
    today = date.today()
    rows = (
        StudentDailySignal.query.with_entities(StudentDailySignal.signal_date)
        .filter(
            StudentDailySignal.student_id == student.id,
            StudentDailySignal.signal_date > today - timedelta(days=21),
        )
        .all()
    )
    days = {row[0] for row in rows if row[0]}
    if not days:
        return None
    yesterday = today - timedelta(days=1)
    anchor = today if today in days else (yesterday if yesterday in days else None)
    if anchor is None:
        return None
    streak = 0
    cursor = anchor
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    if streak >= 3:
        return {
            "kind": "streak",
            "severity": "positive",
            "title": f"{streak} dias seguidos ativo",
            "detail": "Sequencia boa de engajamento. Vale um reforco positivo pra manter o ritmo.",
            "meta": {"streak": streak},
        }
    return None


def _signal_reassessment_due(student, *, due_days: int = 60) -> dict | None:
    """Reavaliacao fisica vencida (ou nunca feita)."""
    today = date.today()
    last = (
        PhysicalAssessment.query.with_entities(PhysicalAssessment.assessment_date)
        .filter(PhysicalAssessment.student_id == student.id)
        .order_by(PhysicalAssessment.assessment_date.desc())
        .first()
    )
    if last is None:
        created = getattr(student, "created_at", None)
        if created and (today - created.date()).days >= 21:
            return {
                "kind": "reassessment_due",
                "severity": "info",
                "title": "Sem avaliacao fisica registrada",
                "detail": "Uma avaliacao inicial ajuda a medir progresso e ancorar o acompanhamento.",
                "meta": {"daysSince": None},
            }
        return None
    days_since = (today - last[0]).days
    if days_since >= due_days:
        return {
            "kind": "reassessment_due",
            "severity": "warning" if days_since >= due_days * 1.5 else "info",
            "title": "Reavaliacao vencida",
            "detail": f"Ultima avaliacao ha {days_since} dias. Vale agendar uma nova medicao.",
            "meta": {"daysSince": days_since},
        }
    return None


def _signal_goal_projection(student, *, min_points: int = 3) -> dict | None:
    """Regressao linear na serie de peso das avaliacoes -> projecao em 4 semanas."""
    rows = (
        PhysicalAssessment.query.with_entities(
            PhysicalAssessment.assessment_date, PhysicalAssessment.weight_kg
        )
        .filter(
            PhysicalAssessment.student_id == student.id,
            PhysicalAssessment.weight_kg.isnot(None),
        )
        .order_by(PhysicalAssessment.assessment_date.asc())
        .all()
    )
    points = [(row[0], float(row[1])) for row in rows if row[1] is not None]
    if len(points) < min_points:
        return None
    base = points[0][0]
    xs = [float((day - base).days) for day, _ in points]
    ys = [weight for _, weight in points]
    slope = _linear_slope(xs, ys)  # kg/dia
    if slope is None:
        return None
    per_week = slope * 7
    current = ys[-1]
    if abs(per_week) < 0.05:
        return {
            "kind": "goal_projection",
            "severity": "info",
            "title": "Peso estavel",
            "detail": "A serie de avaliacoes esta praticamente estavel nas ultimas medicoes.",
            "meta": {"perWeekKg": round(per_week, 2), "currentKg": round(current, 1)},
        }
    projected = current + per_week * 4
    direction = "queda" if per_week < 0 else "ganho"
    return {
        "kind": "goal_projection",
        "severity": "info",
        "title": f"Projecao: {direction} de peso",
        "detail": f"No ritmo atual ({per_week:+.2f} kg/sem), projecao de ~{projected:.1f} kg em 4 semanas.",
        "meta": {
            "perWeekKg": round(per_week, 2),
            "projected4wKg": round(projected, 1),
            "currentKg": round(current, 1),
        },
    }


def _signal_training_nutrition_coherence(student) -> dict | None:
    """Coerencia entre treino e nutricao (leitura cega ou consumo acima da meta)."""
    from app.workouts.services import summarize_workout_consistency

    consistency = summarize_workout_consistency(student) or {}
    completed = consistency.get("completedCount", 0)

    food: dict | None = None
    try:
        from app.nutrition.services import weekly_food_summary

        result = weekly_food_summary(student)
        food = result if isinstance(result, dict) else None
    except Exception:
        food = None

    days_with_log = (food or {}).get("daysWithLog", 0)
    if completed >= 3 and days_with_log <= 1:
        return {
            "kind": "training_nutrition_coherence",
            "severity": "info",
            "title": "Treino em dia, alimentacao sem registro",
            "detail": "O aluno esta treinando bem mas quase nao registra refeicoes — a leitura nutricional fica cega.",
            "meta": {"completed": completed, "daysWithLog": days_with_log},
        }

    target = getattr(student, "daily_calorie_target", None)
    avg = (food or {}).get("avgCaloriesKcal")
    if target and avg and avg > target * 1.2 and days_with_log >= 3:
        return {
            "kind": "training_nutrition_coherence",
            "severity": "warning",
            "title": "Consumo acima da meta",
            "detail": f"Media de ~{int(avg)} kcal/dia vs meta de {target} kcal. Vale revisar o plano ou a meta.",
            "meta": {"avgKcal": int(avg), "targetKcal": target},
        }
    return None


def _signal_overtraining(student) -> dict | None:
    """Fadiga/overtraining: volume subindo com recuperacao baixa (exige wearable)."""
    from app.wearables.services import serialize_wearable_summary

    summary = serialize_wearable_summary(student)
    if not summary or not summary.get("connected"):
        return None
    series = summary.get("series") or []

    def _metric(metric: str) -> list[float]:
        points = sorted(
            (p for p in series if p.get("metricType") == metric and isinstance(p.get("value"), (int, float))),
            key=lambda p: p.get("date") or "",
        )
        return [float(p["value"]) for p in points]

    def _trend_up(values: list[float]) -> bool:
        if len(values) < 4:
            return False
        half = len(values) // 2
        first = sum(values[:half]) / half
        second = sum(values[half:]) / (len(values) - half)
        return first > 0 and second > first * 1.15

    active = _metric("active_minutes")
    sleep = _metric("sleep_hours")
    resting = _metric("resting_hr")

    volume_up = _trend_up(active)
    low_sleep = bool(sleep) and (sum(sleep) / len(sleep)) < 6
    resting_up = _trend_up(resting)

    if volume_up and (low_sleep or resting_up):
        reason = "sono baixo" if low_sleep else "frequencia de repouso subindo"
        return {
            "kind": "overtraining",
            "severity": "warning",
            "title": "Sinais de fadiga / overtraining",
            "detail": f"Volume de atividade subindo com {reason}. Considere um deload ou dia de recuperacao.",
            "meta": {"volumeUp": True, "lowSleep": low_sleep, "restingUp": resting_up},
        }
    return None


def _signal_best_time(student, *, min_signals: int = 6) -> dict | None:
    """Melhor janela de contato a partir dos timestamps dos registros."""
    rows = (
        StudentDailySignal.query.with_entities(StudentDailySignal.created_at)
        .filter(
            StudentDailySignal.student_id == student.id,
            StudentDailySignal.created_at.isnot(None),
        )
        .order_by(StudentDailySignal.created_at.desc())
        .limit(60)
        .all()
    )
    stamps = [row[0] for row in rows if row[0]]
    if len(stamps) < min_signals:
        return None
    top_weekday = Counter(stamp.weekday() for stamp in stamps).most_common(1)[0][0]
    top_hour = Counter(stamp.hour for stamp in stamps).most_common(1)[0][0]
    return {
        "kind": "best_time",
        "severity": "info",
        "title": "Melhor janela de contato",
        "detail": f"Aluno costuma se registrar as {_WEEKDAY_PT[top_weekday]}s, por volta das {top_hour:02d}h.",
        "meta": {"weekday": top_weekday, "hour": top_hour},
    }
