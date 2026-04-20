from __future__ import annotations

import sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.accounts.models import Account, ProfessionalProfile
from app.auth.models import User
from app.extensions import db
from app.files.models import StudentFile
from app.insights.models import AIInsight
from app.messaging.models import SuggestedMessage
from app.reports.models import GeneratedReport
from app.students.models import (
    StudentDailySignal,
    StudentDailySummary,
    StudentHealthContext,
    StudentInteraction,
    StudentProfile,
)
from app.workouts.models import WorkoutDayExercise, WorkoutPlan, WorkoutPlanDay


def utcnow():
    return datetime.now(timezone.utc)


def main():
    app = create_app()
    with app.app_context():
        import app.models  # noqa

        db.create_all()
        if User.query.filter_by(email="owner@fitcopilot.dev").first():
            print("seed-already-present")
            return

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

        user = User(
            account_id=account.id,
            role="owner",
            full_name="Lucas Fit",
            email="owner@fitcopilot.dev",
            phone="+5511999990000",
            password_hash=generate_password_hash("abcd1234"),
            is_active=True,
            core_access_token="local-core-token",
        )
        db.session.add(user)
        db.session.flush()

        professional = ProfessionalProfile(
            user_id=user.id,
            account_id=account.id,
            professional_type="mixed",
            onboarding_completed=True,
        )
        db.session.add(professional)
        db.session.flush()

        student_specs = [
            ("João Almeida", "joao@fitcopilot.dev", "attention", 58, "down", "Hipertrofia"),
            ("Maria Lopes", "maria@fitcopilot.dev", "no_signal", 34, "down", "Emagrecimento"),
            ("Pedro Rangel", "pedro@fitcopilot.dev", "attention", 62, "down", "Performance corrida"),
            ("Ana Beatriz", "ana@fitcopilot.dev", "active", 91, "up", "Saúde geral"),
            ("Lucas Ferreira", "lucas@fitcopilot.dev", "active", 82, "stable", "Hipertrofia"),
            ("Carla Mendes", "carla@fitcopilot.dev", "new", 50, "stable", "Pós-parto"),
        ]

        seeded_students: list[StudentProfile] = []
        for index, spec in enumerate(student_specs):
            full_name, email, status, score, trend, objective = spec
            student = StudentProfile(
                account_id=account.id,
                primary_professional_id=professional.id,
                full_name=full_name,
                email=email,
                phone=f"+55119999910{index}",
                status=status,
                adherence_score=score,
                adherence_trend=trend,
                main_objective_text=objective,
                notes=f"Observações operacionais de {full_name}.",
                last_contact_at=utcnow() - timedelta(hours=index * 4 + 1),
                last_activity_at=utcnow() - timedelta(hours=index * 6 + 2),
                last_signal_summary="Acompanhamento recente disponível",
            )
            db.session.add(student)
            db.session.flush()
            db.session.add(StudentHealthContext(student_id=student.id, sleep_notes="Sono irregular" if index < 2 else "Sem alertas"))
            seeded_students.append(student)

        db.session.flush()

        joao = seeded_students[0]
        summary = StudentDailySummary(
            account_id=account.id,
            student_id=joao.id,
            summary_date=date.today(),
            food_summary_text="Ingestão abaixo do ideal antes do treino.",
            activity_summary_text="Relatou cansaço em 3 sessões recentes.",
            overall_summary_text="João merece ajuste pontual hoje.",
            ai_reading_text="Baixa energia pré-treino e queda de sono.",
            suggested_adjustment_text="Reduzir volume de membros inferiores em 30%.",
            suggested_message_text="Oi João! Vamos aliviar o treino hoje para manter qualidade e recuperar bem.",
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
            student_id=joao.id,
            summary_id=summary.id,
            insight_scope="daily",
            insight_type="daily_adjustment",
            title="Reduzir volume do treino de hoje",
            body="Diminuir 30% do volume em membros inferiores e priorizar mobilidade.",
            priority="high",
            status="open",
            action_label="Aplicar sugestão",
        )
        db.session.add(insight)
        db.session.flush()

        db.session.add(
            SuggestedMessage(
                account_id=account.id,
                student_id=joao.id,
                summary_id=summary.id,
                insight_id=insight.id,
                message_category="workout_adjustment",
                subject_hint="Ajuste do treino de hoje",
                message_text="Oi João! Vi sua energia mais baixa hoje. Ajustei o treino para manter qualidade sem te esgotar.",
            )
        )

        for student in seeded_students:
            for offset in range(3):
                signal_type = "meal" if offset == 0 else "workout" if offset == 1 else "message"
                db.session.add(
                    StudentDailySignal(
                        account_id=account.id,
                        student_id=student.id,
                        signal_date=date.today() - timedelta(days=offset),
                        signal_type=signal_type,
                        source="manual",
                        title=f"{signal_type.title()} registrada",
                        body=f"Sinal {signal_type} de {student.full_name}",
                        payload_json={},
                        created_by_user_id=user.id,
                        created_at=utcnow() - timedelta(hours=offset * 8),
                    )
                )
            db.session.add(
                StudentInteraction(
                    account_id=account.id,
                    student_id=student.id,
                    interaction_type="manual_note",
                    channel="manual",
                    title="Nota operacional",
                    body=f"Contexto recente de {student.full_name}",
                    created_by_user_id=user.id,
                    interaction_at=utcnow() - timedelta(hours=2),
                    created_at=utcnow() - timedelta(hours=2),
                )
            )
            db.session.add(
                GeneratedReport(
                    account_id=account.id,
                    student_id=student.id,
                    requested_by_user_id=user.id,
                    report_type="progress_summary",
                    status="completed",
                    summary_text=f"Resumo de progresso de {student.full_name}.",
                    file_url="http://localhost:5000/mock-report.txt",
                    completed_at=utcnow(),
                )
            )
            db.session.add(
                StudentFile(
                    account_id=account.id,
                    student_id=student.id,
                    uploaded_by_user_id=user.id,
                    file_category="physical_evaluation",
                    title=f"Avaliação de {student.full_name}",
                    original_filename="avaliacao.txt",
                    storage_key="mock/avaliacao.txt",
                    file_url="http://localhost:5000/mock-file.txt",
                    mime_type="text/plain",
                    file_size_bytes=128,
                    extraction_status="completed",
                    ai_summary="Resumo local para ambiente de desenvolvimento.",
                    uploaded_at=utcnow(),
                )
            )

        workout = WorkoutPlan(
            account_id=account.id,
            student_id=joao.id,
            created_by_user_id=user.id,
            title="Push A — Peito e Ombro",
            objective="Hipertrofia",
            status="active",
            version_number=1,
        )
        db.session.add(workout)
        db.session.flush()
        day = WorkoutPlanDay(workout_plan_id=workout.id, label="Treino A", order_index=1)
        db.session.add(day)
        db.session.flush()
        for i, (name, reps) in enumerate(
            [("Supino reto", "4 x 8"), ("Desenvolvimento halteres", "3 x 10"), ("Elevação lateral", "4 x 15")]
        ):
            db.session.add(
                WorkoutDayExercise(
                    workout_plan_day_id=day.id,
                    order_index=i + 1,
                    exercise_name=name,
                    reps_text=reps,
                    sets_count=3,
                )
            )

        db.session.commit()
        print("seed-complete")


if __name__ == "__main__":
    main()
