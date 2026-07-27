from types import SimpleNamespace

from app.overview.services import _triage_student


def _result(*, status="ok", score=80):
    return SimpleNamespace(status=status, score=score, insight="...")


def test_risk_ranks_above_attention():
    risk_score, risk_reasons = _triage_student(
        _result(), {"status": "risk", "trend": "down", "score": 40}
    )
    att_score, _ = _triage_student(
        _result(), {"status": "attention", "trend": "flat", "score": 70}
    )
    assert risk_score > att_score
    assert "Risco alto" in risk_reasons
    assert "Tendência de queda" in risk_reasons
    assert "Score 40" in risk_reasons


def test_no_signal_contributes_reason():
    score, reasons = _triage_student(
        _result(status="no_signal"), {"status": "ok", "trend": "flat", "score": 90}
    )
    assert score == 25
    assert reasons == ["Sem sinal recente"]


def test_healthy_student_has_zero_priority():
    score, reasons = _triage_student(
        _result(), {"status": "ok", "trend": "up", "score": 88}
    )
    assert score == 0
    assert reasons == []
