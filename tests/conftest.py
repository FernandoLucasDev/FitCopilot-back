from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_test_app
from app.accounts.models import Account, ProfessionalProfile
from app.auth.models import User
from app.extensions import db
from app.common.security.rate_limit import reset_rate_limits
from app.files.models import StudentFile
from app.insights.models import AIInsight
from app.messaging.models import SuggestedMessage
from app.reports.models import GeneratedReport
from app.students.models import StudentDailySummary, StudentHealthContext, StudentInteraction, StudentProfile
from app.workouts.models import WorkoutDayExercise, WorkoutPlan, WorkoutPlanDay


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def flask_app():
    application = create_test_app()
    application.config.update(
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=30),
        WTF_CSRF_ENABLED=False,
        TESTING=True,
    )

    with application.app_context():
        import app.models  # noqa: F401

        reset_rate_limits()
        db.create_all()
        yield application
        reset_rate_limits()
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def seeded_data(flask_app):
    account = Account(
        name="Fit Copilot",
        slug="fit-copilot",
        email="owner@fitcopilot.dev",
        phone="+5511999990000",
        external_org_id="fit-copilot-org",
        settings_json={"workspace_mode": "assistant"},
    )
    db.session.add(account)
    db.session.flush()

    owner = User(
        account_id=account.id,
        role="owner",
        full_name="Lucas Fit",
        email="owner@fitcopilot.dev",
        phone="+5511999990000",
        password_hash=generate_password_hash("abcd1234"),
        is_active=True,
        core_access_token="local-core-token",
    )
    db.session.add(owner)
    db.session.flush()

    professional = ProfessionalProfile(
        user_id=owner.id,
        account_id=account.id,
        professional_type="mixed",
        onboarding_completed=True,
    )
    db.session.add(professional)
    db.session.flush()

    student = StudentProfile(
        account_id=account.id,
        primary_professional_id=professional.id,
        full_name="Joao Almeida",
        email="joao@fitcopilot.dev",
        phone="+5511999991000",
        status="attention",
        adherence_score=58,
        adherence_trend="down",
        main_objective_text="Hipertrofia",
        notes="Observacoes operacionais.",
        last_contact_at=utcnow() - timedelta(days=1),
        last_activity_at=utcnow() - timedelta(hours=12),
        last_signal_summary="Acompanhamento recente disponivel",
    )
    db.session.add(student)
    db.session.flush()

    db.session.add(StudentHealthContext(student_id=student.id, sleep_notes="Sono irregular"))

    summary = StudentDailySummary(
        account_id=account.id,
        student_id=student.id,
        summary_date=date.today(),
        food_summary_text="Baixa ingestao proteica.",
        activity_summary_text="Treinou com fadiga moderada.",
        overall_summary_text="Dia com necessidade de ajuste.",
        ai_reading_text="Sono abaixo do ideal e baixa energia.",
        suggested_adjustment_text="Reduzir volume em 20%.",
        suggested_message_text="Vamos ajustar o treino hoje para recuperar melhor.",
        risk_level="attention",
        needs_attention=True,
        was_generated_by_ai=True,
        generation_status="completed",
        completed_at=utcnow(),
    )
    db.session.add(summary)
    db.session.flush()

    insight = AIInsight(
        account_id=account.id,
        student_id=student.id,
        summary_id=summary.id,
        insight_scope="daily",
        insight_type="daily_adjustment",
        title="Ajustar treino de hoje",
        body="Reduzir volume e reforcar descanso.",
        priority="high",
        status="open",
        action_label="Aplicar",
    )
    db.session.add(insight)
    db.session.flush()

    db.session.add(
        SuggestedMessage(
            account_id=account.id,
            student_id=student.id,
            summary_id=summary.id,
            insight_id=insight.id,
            message_category="workout_adjustment",
            subject_hint="Ajuste do treino",
            message_text="Joao, vamos aliviar o treino hoje para manter qualidade.",
        )
    )

    db.session.add(
        StudentInteraction(
            account_id=account.id,
            student_id=student.id,
            interaction_type="manual_note",
            channel="manual",
            title="Nota operacional",
            body="Aluno relatou fadiga.",
            created_by_user_id=owner.id,
            interaction_at=utcnow(),
            created_at=utcnow(),
        )
    )

    plan = WorkoutPlan(
        account_id=account.id,
        student_id=student.id,
        created_by_user_id=owner.id,
        title="Push A",
        objective="Hipertrofia",
        status="active",
        version_number=1,
    )
    db.session.add(plan)
    db.session.flush()

    day = WorkoutPlanDay(workout_plan_id=plan.id, label="Treino A", order_index=1)
    db.session.add(day)
    db.session.flush()
    db.session.add(
        WorkoutDayExercise(
            workout_plan_day_id=day.id,
            order_index=1,
            exercise_name="Supino reto",
            reps_text="4 x 8",
            sets_count=4,
        )
    )

    db.session.add(
        StudentFile(
            account_id=account.id,
            student_id=student.id,
            uploaded_by_user_id=owner.id,
            file_category="physical_evaluation",
            title="Avaliacao de Joao",
            original_filename="avaliacao.txt",
            storage_key="mock/avaliacao.txt",
            file_url="http://localhost/mock-file.txt",
            mime_type="text/plain",
            file_size_bytes=128,
            extraction_status="completed",
            ai_summary="Resumo local.",
            uploaded_at=utcnow(),
        )
    )

    db.session.add(
        GeneratedReport(
            account_id=account.id,
            student_id=student.id,
            requested_by_user_id=owner.id,
            report_type="progress_summary",
            status="completed",
            summary_text="Resumo de progresso.",
            file_url="http://localhost/mock-report.txt",
            completed_at=utcnow(),
        )
    )

    db.session.commit()

    return {
      "account": account,
      "owner": owner,
      "professional": professional,
      "student": student,
      "summary": summary,
      "insight": insight,
      "plan": plan,
    }


@pytest.fixture()
def auth_headers(client, seeded_data):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@fitcopilot.dev", "password": "abcd1234"},
    )
    token = response.get_json()["data"]["token"]
    return {"Authorization": f"Bearer {token}"}
