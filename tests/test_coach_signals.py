from datetime import date, timedelta
from types import SimpleNamespace

from app.insights import coach_signals


class _Col:
    # coluna-fake que aceita os operadores usados nos filtros/order_by do ORM
    def __eq__(self, other):
        return True

    def __gt__(self, other):
        return True

    def __le__(self, other):
        return True

    def isnot(self, other):
        return True

    def asc(self):
        return self

    def desc(self):
        return self


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def with_entities(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def count(self):
        return len(self._rows)


def _fake_model(rows):
    class M:
        student_id = _Col()
        signal_date = _Col()
        created_at = _Col()
        assessment_date = _Col()
        weight_kg = _Col()
        query = _FakeQuery(rows)

    return M


def _run(monkeypatch, sessions):
    monkeypatch.setattr(coach_signals, "list_student_sessions", lambda **_: sessions)
    student = SimpleNamespace(account_id="acc", id="stu")
    return {item["exerciseName"]: item for item in coach_signals.analyze_load_progression(student)}


def _session(date, exercises):
    # `exercises` = list of (name, reps_completed_str)
    return {
        "date": date,
        "status": "completed",
        "exercises": [{"exerciseName": n, "repsCompleted": r, "notes": None} for n, r in exercises],
    }


def test_progressing_plateau_regressing(monkeypatch):
    # list_student_sessions vem mais-recente-primeiro
    sessions = [
        _session("2026-07-15", [("Supino", "10 reps · carga 50kg"), ("Agacho", "8 reps · carga 80kg"), ("Rosca", "12 reps · carga 20kg")]),
        _session("2026-07-12", [("Supino", "10 reps · carga 45kg"), ("Agacho", "8 reps · carga 80kg"), ("Rosca", "12 reps · carga 22kg")]),
        _session("2026-07-09", [("Supino", "10 reps · carga 40kg"), ("Agacho", "8 reps · carga 80kg"), ("Rosca", "12 reps · carga 24kg")]),
    ]
    result = _run(monkeypatch, sessions)

    assert result["Supino"]["status"] == "progressing"
    assert result["Supino"]["deltaKg"] == 10.0
    assert result["Agacho"]["status"] == "plateau"
    assert result["Rosca"]["status"] == "regressing"
    assert result["Rosca"]["deltaKg"] == -4.0
    # ordena acionaveis primeiro: plateau/regressing antes de progressing
    order = [item for item in coach_signals.analyze_load_progression(SimpleNamespace(account_id="a", id="s"))]  # noqa: F841


def test_skipped_sessions_and_no_weight_ignored(monkeypatch):
    sessions = [
        {"date": "2026-07-15", "status": "skipped", "exercises": [{"exerciseName": "Supino", "repsCompleted": "carga 99kg", "notes": None}]},
        _session("2026-07-12", [("Supino", "10 reps")]),  # sem carga -> ignorado
        _session("2026-07-09", [("Supino", "10 reps · carga 40kg")]),
    ]
    result = _run(monkeypatch, sessions)
    # so 1 ponto de carga valido (40kg) -> "new"
    assert result["Supino"]["status"] == "new"
    assert result["Supino"]["sessions"] == 1


def test_defensive_on_error(monkeypatch):
    def boom(**_):
        raise RuntimeError("db down")

    monkeypatch.setattr(coach_signals, "list_student_sessions", boom)
    assert coach_signals.analyze_load_progression(SimpleNamespace(account_id="a", id="s")) == []


# ---------------------------------------------------------------------------
# build_coach_signals — heuristicas custo-zero
# ---------------------------------------------------------------------------


def test_linear_slope():
    assert coach_signals._linear_slope([0, 1, 2, 3], [0, 1, 2, 3]) == 1.0
    assert coach_signals._linear_slope([0, 1, 2], [5, 5, 5]) == 0.0
    assert coach_signals._linear_slope([1], [1]) is None
    assert coach_signals._linear_slope([2, 2, 2], [1, 2, 3]) is None  # denominador zero


def test_goal_projection_regression(monkeypatch):
    rows = [
        (date(2026, 6, 1), 82.0),
        (date(2026, 6, 15), 81.0),
        (date(2026, 7, 1), 80.0),
        (date(2026, 7, 15), 79.0),
    ]
    monkeypatch.setattr(coach_signals, "PhysicalAssessment", _fake_model(rows))
    result = coach_signals._signal_goal_projection(SimpleNamespace(id="s"))
    assert result["kind"] == "goal_projection"
    assert result["severity"] == "info"
    assert result["meta"]["perWeekKg"] < 0  # tendencia de queda de peso
    assert result["meta"]["projected4wKg"] < result["meta"]["currentKg"]


def test_goal_projection_needs_minimum_points(monkeypatch):
    monkeypatch.setattr(coach_signals, "PhysicalAssessment", _fake_model([(date(2026, 7, 1), 80.0)]))
    assert coach_signals._signal_goal_projection(SimpleNamespace(id="s")) is None


def test_streak_counts_consecutive_days(monkeypatch):
    today = date.today()
    rows = [
        (today,),
        (today - timedelta(days=1),),
        (today - timedelta(days=2),),
        (today - timedelta(days=5),),  # quebra a sequencia
    ]
    monkeypatch.setattr(coach_signals, "StudentDailySignal", _fake_model(rows))
    result = coach_signals._signal_streak(SimpleNamespace(id="s"))
    assert result["kind"] == "streak"
    assert result["severity"] == "positive"
    assert result["meta"]["streak"] == 3


def test_build_coach_signals_is_defensive(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    for name in (
        "_signal_routine_anomaly",
        "_signal_streak",
        "_signal_reassessment_due",
        "_signal_goal_projection",
        "_signal_training_nutrition_coherence",
        "_signal_overtraining",
        "_signal_best_time",
    ):
        monkeypatch.setattr(coach_signals, name, boom)
    assert coach_signals.build_coach_signals(SimpleNamespace(id="s", account_id="a")) == []
