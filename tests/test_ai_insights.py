from app.ai.fake_provider import FakeAIProvider


def test_weekly_recap_mentions_workouts():
    provider = FakeAIProvider()
    recap = provider.weekly_recap(
        context={"student_name": "Ana Beatriz", "workouts_completed": 3, "workouts_skipped": 1, "score": 64}
    )
    assert "3" in recap
    assert "Ana" in recap


def test_student_questions_returns_list():
    provider = FakeAIProvider()
    questions = provider.student_questions(context={"student_name": "Ana"})
    assert isinstance(questions, list)
    assert 3 <= len(questions) <= 5
    assert all(isinstance(q, str) and q.strip() for q in questions)


def test_sentiment_no_messages_is_sem_dados():
    provider = FakeAIProvider()
    result = provider.analyze_sentiment(messages=[], context={})
    assert result.label == "sem_dados"


def test_sentiment_detects_negative_and_positive():
    provider = FakeAIProvider()
    assert provider.analyze_sentiment(messages=["tô muito cansado, tá difícil"], context={}).label == "desmotivado"
    assert provider.analyze_sentiment(messages=["consegui, tô super animado, obrigado!"], context={}).label == "positivo"
    assert provider.analyze_sentiment(messages=["ok, entendi"], context={}).label == "neutro"
