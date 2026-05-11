from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import celery_app, db
from app.files.models import StudentFile
from app.jobs.models import BackgroundJob
from app.jobs.services import finish_background_job
from app.messaging.models import SuggestedMessage
from app.reports.models import GeneratedReport
from app.students.models import StudentDailySummary, StudentInteraction, StudentProfile
from app.students.services import compute_student_score
from app.insights.models import AIInsight
from app.whatsapp.models import InboundMessageRecord, OutboundMessageDispatch
from app.whatsapp.services import perform_dispatch, process_inbound_message, send_daily_checkin, send_manual_whatsapp_message, send_workout_of_day


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _find_ledger(reference_type: str, reference_id: str) -> BackgroundJob | None:
    return BackgroundJob.query.filter_by(reference_type=reference_type, reference_id=reference_id).order_by(BackgroundJob.created_at.desc()).first()


@celery_app.task(name="send_whatsapp_message_job")
def send_whatsapp_message_job(dispatch_id: str):
    dispatch = OutboundMessageDispatch.query.filter_by(id=dispatch_id).first()
    if dispatch is None:
        return {"status": "missing"}
    ledger = _find_ledger("whatsapp_dispatch", dispatch_id)
    try:
        perform_dispatch(dispatch_id)
        if ledger:
            finish_background_job(ledger, status="completed", result={"dispatch_id": dispatch_id})
        db.session.commit()
        return {"status": "completed"}
    except Exception as exc:  # pragma: no cover
        if dispatch:
            dispatch.local_status = "failed"
        if ledger:
            finish_background_job(ledger, status="failed", error_message=str(exc))
        db.session.commit()
        raise


@celery_app.task(name="process_inbound_whatsapp_message_job")
def process_inbound_whatsapp_message_job(inbound_message_record_id: str):
    inbound = InboundMessageRecord.query.filter_by(id=inbound_message_record_id).first()
    if inbound is None:
        return {"status": "missing"}
    ledger = _find_ledger("inbound_message_record", inbound_message_record_id)
    try:
        result = process_inbound_message(inbound_message_record_id)
        if ledger:
            finish_background_job(ledger, status="completed", result=result)
        db.session.commit()
        return result
    except Exception as exc:  # pragma: no cover
        inbound.processing_status = "failed"
        if ledger:
            finish_background_job(ledger, status="failed", error_message=str(exc))
        db.session.commit()
        raise


@celery_app.task(name="extract_student_file_job")
def extract_student_file_job(student_file_id: str):
    item = StudentFile.query.filter_by(id=student_file_id).first()
    if item is None:
        return {"status": "missing"}
    ledger = _find_ledger("student_file", student_file_id)
    if item.extraction_status == "completed":
        if ledger:
            finish_background_job(ledger, status="completed", result={"student_file_id": student_file_id})
            db.session.commit()
        return {"status": "already_completed"}
    try:
        item.extraction_status = "processing"
        storage = celery_app.flask_app.extensions["storage_provider"] if hasattr(celery_app, "flask_app") else None
        if storage is None:
            from flask import current_app

            storage = current_app.extensions["storage_provider"]
            ai_provider = current_app.extensions["ai_provider"]
        else:
            from flask import current_app

            ai_provider = current_app.extensions["ai_provider"]
        content = storage.open_bytes(item.storage_key)
        result = ai_provider.summarize_file(
            filename=item.original_filename,
            content=content,
            context={"student_name": item.student.full_name},
        )
        item.extracted_text = result.extracted_text
        item.ai_summary = result.ai_summary
        item.extracted_structured_json = result.structured_data
        item.extraction_status = "completed"
        if ledger:
            finish_background_job(ledger, status="completed", result={"student_file_id": student_file_id})
        db.session.commit()
        return {"status": "completed"}
    except Exception as exc:  # pragma: no cover - defensive path
        item.extraction_status = "failed"
        if ledger:
            finish_background_job(ledger, status="failed", error_message=str(exc))
        db.session.commit()
        raise


@celery_app.task(name="generate_student_daily_summary_job")
def generate_student_daily_summary_job(student_id: str, summary_date: str | None = None):
    from datetime import date

    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        return {"status": "missing"}
    target_date = date.fromisoformat(summary_date) if summary_date else date.today()
    summary = StudentDailySummary.query.filter_by(student_id=student.id, summary_date=target_date).first()
    if summary is None:
        summary = StudentDailySummary(
            account_id=student.account_id,
            student_id=student.id,
            summary_date=target_date,
            generation_status="processing",
        )
        db.session.add(summary)
        db.session.flush()

    signals = [
        {"title": item.title, "signal_type": item.signal_type}
        for item in student.daily_signals
        if item.signal_date == target_date
    ]
    interactions = [
        {"title": item.title, "interaction_type": item.interaction_type}
        for item in student.interactions
        if item.interaction_at.date() == target_date
    ]
    recent_files = [
        {
            "title": item.title,
            "category": item.file_category,
            "summary": item.ai_summary,
            "structured_data": item.extracted_structured_json,
        }
        for item in student.files
        if item.extraction_status == "completed"
    ][:3]
    recent_sessions = [
        {
            "date": item.session_date.isoformat(),
            "status": item.status,
            "notes": item.notes,
        }
        for item in sorted(student.workout_sessions, key=lambda value: value.session_date, reverse=True)[:5]
    ]
    from flask import current_app

    ai_provider = current_app.extensions["ai_provider"]
    score = compute_student_score(student)
    result = ai_provider.summarize_student_day(
        context={
            "student_name": student.full_name,
            "signals": signals,
            "interactions": interactions,
            "score": score.score,
            "recent_files": recent_files,
            "recent_workout_sessions": recent_sessions,
        }
    )
    summary.food_summary_text = result.food_summary_text
    summary.activity_summary_text = result.activity_summary_text
    summary.overall_summary_text = result.overall_summary_text
    summary.ai_reading_text = result.ai_reading_text
    summary.suggested_adjustment_text = result.suggested_adjustment_text
    summary.suggested_message_text = result.suggested_message_text
    summary.risk_level = result.risk_level
    summary.needs_attention = result.risk_level in {"attention", "high"}
    summary.was_generated_by_ai = True
    summary.generation_status = "completed"
    summary.completed_at = utcnow()

    insight = AIInsight(
        account_id=student.account_id,
        student_id=student.id,
        summary_id=summary.id,
        insight_scope="daily",
        insight_type="daily_adjustment",
        title="Sugestão do dia",
        body=result.suggested_adjustment_text,
        priority="high" if summary.needs_attention else "medium",
        status="open",
        action_label="Aplicar sugestão",
    )
    db.session.add(insight)
    message = SuggestedMessage(
        account_id=student.account_id,
        student_id=student.id,
        summary_id=summary.id,
        insight_id=insight.id,
        message_category="engagement",
        subject_hint="Follow-up do dia",
        message_text=result.suggested_message_text,
    )
    db.session.add(message)
    db.session.commit()
    return {"status": "completed", "summary_id": str(summary.id)}


@celery_app.task(name="generate_student_report_job")
def generate_student_report_job(report_id: str):
    report = GeneratedReport.query.filter_by(id=report_id).first()
    if report is None:
        return {"status": "missing"}
    from flask import current_app

    report.status = "processing"
    student = report.student
    ai_provider = current_app.extensions["ai_provider"]
    recent_files = [
        {
            "title": item.title,
            "category": item.file_category,
            "summary": item.ai_summary,
            "structured_data": item.extracted_structured_json,
        }
        for item in student.files
        if item.extraction_status == "completed"
    ][:5]
    workout_sessions = [
        {
            "date": item.session_date.isoformat(),
            "status": item.status,
            "notes": item.notes,
            "exercises": [
                {
                    "exercise_name": log.exercise_name,
                    "sets_completed": log.sets_completed,
                    "reps_completed": log.reps_completed,
                }
                for log in item.exercise_logs
            ],
        }
        for item in sorted(student.workout_sessions, key=lambda value: value.session_date, reverse=True)[:10]
    ]
    report.summary_text = ai_provider.summarize_student_progress(
        context={
            "student_name": student.full_name,
            "goal": student.main_objective_text,
            "status": student.status,
            "adherence_score": student.adherence_score,
            "recent_files": recent_files,
            "workout_sessions": workout_sessions,
            "period_start": report.period_start.isoformat() if report.period_start else None,
            "period_end": report.period_end.isoformat() if report.period_end else None,
        }
    )
    storage = current_app.extensions["storage_provider"]
    content = report.summary_text.encode("utf-8")
    stored = storage.save(f"accounts/{report.account_id}/reports", f"{report.report_type}.txt", content, "text/plain")
    report.storage_key = stored.storage_key
    report.file_url = stored.file_url
    report.status = "completed"
    report.completed_at = utcnow()
    db.session.commit()
    return {"status": "completed", "report_id": report_id}


@celery_app.task(name="recompute_student_score_job")
def recompute_student_score_job(student_id: str):
    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        return {"status": "missing"}
    score = compute_student_score(student)
    student.adherence_score = score.score
    student.adherence_trend = score.trend
    if student.status != "archived":
        student.status = score.status
    student.last_signal_summary = score.insight
    db.session.commit()
    return {"status": "completed", "score": score.score}


@celery_app.task(name="generate_message_suggestion_job")
def generate_message_suggestion_job(student_id: str):
    from flask import current_app

    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        return {"status": "missing"}
    ai_provider = current_app.extensions["ai_provider"]
    text = ai_provider.suggest_message(context={"student_name": student.full_name, "reason": "acompanhar sua aderência"})
    message = SuggestedMessage(
        account_id=student.account_id,
        student_id=student.id,
        message_category="engagement",
        subject_hint="Mensagem sugerida",
        message_text=text,
    )
    db.session.add(message)
    db.session.commit()
    return {"status": "completed", "message_id": str(message.id)}


@celery_app.task(name="send_daily_checkin_job")
def send_daily_checkin_job(student_id: str):
    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        return {"status": "missing"}
    dispatch = send_daily_checkin(student=student, actor_user_id=student.primary_professional.user_id if student.primary_professional else None)
    return {"status": "completed", "dispatch_id": str(dispatch.id)}


@celery_app.task(name="send_workout_of_day_job")
def send_workout_of_day_job(student_id: str, workout_plan_id: str | None = None):
    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        return {"status": "missing"}
    dispatch = send_workout_of_day(student=student, actor_user_id=student.primary_professional.user_id if student.primary_professional else None)
    return {"status": "completed", "dispatch_id": str(dispatch.id), "workout_plan_id": workout_plan_id}


@celery_app.task(name="send_reengagement_message_job")
def send_reengagement_message_job(student_id: str, reason_code: str):
    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        return {"status": "missing"}
    text = (
        f"Oi {student.full_name.split()[0]}, percebi uma queda no seu ritmo ({reason_code}). "
        "Se fizer sentido, me responde aqui que eu ajusto seu acompanhamento."
    )
    dispatch = send_manual_whatsapp_message(
        student=student,
        actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
        message_text=text,
    )
    return {"status": "completed", "dispatch_id": str(dispatch.id)}
