from __future__ import annotations

from datetime import date, datetime, timezone
import re

from app.extensions import celery_app, db
from app.files.models import StudentFile
from app.jobs.models import BackgroundJob
from app.jobs.services import finish_background_job
from app.messaging.models import SuggestedMessage
from app.nutrition.services import evaluate_nutrition_automation
from app.reports.models import GeneratedReport
from app.students.models import StudentDailySummary, StudentInteraction, StudentProfile
from app.students.services import compute_student_score
from app.insights.models import AIInsight
from app.whatsapp.models import InboundMessageRecord, OutboundMessageDispatch
from app.whatsapp.services import (
    get_or_create_student_automations,
    perform_dispatch,
    process_inbound_message,
    send_daily_checkin,
    send_end_of_day_report,
    send_manual_whatsapp_message,
    send_workout_of_day,
    check_pending_workout_sessions,
)


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

    if report.report_type == "nutrition_summary":
        return _generate_nutrition_report(report=report, student=student, ai_provider=ai_provider)

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
    pdf_content = _build_student_report_pdf(
        title=f"Relatorio FitCopilot - {student.full_name}",
        subtitle=f"{report.report_type} | {report.period_start.isoformat() if report.period_start else 'inicio'} a {report.period_end.isoformat() if report.period_end else 'hoje'}",
        sections=[
            ("Resumo inteligente", report.summary_text or "Relatorio gerado sem resumo disponivel."),
            ("Treinos recentes", _format_workout_sessions_for_report(workout_sessions)),
            ("Arquivos usados", _format_files_for_report(recent_files)),
        ],
    )
    stored = storage.save(f"accounts/{report.account_id}/reports", f"{report.report_type}.pdf", pdf_content, "application/pdf")
    report.storage_key = stored.storage_key
    report.file_url = stored.file_url
    report.status = "completed"
    report.completed_at = utcnow()
    db.session.commit()
    return {"status": "completed", "report_id": report_id}


def _generate_nutrition_report(*, report: GeneratedReport, student, ai_provider) -> dict:
    from flask import current_app

    from app.nutrition.services import latest_food_score, weekly_food_summary

    summary = weekly_food_summary(student)
    food_score = latest_food_score(student)
    report.summary_text = ai_provider.summarize_student_progress(
        context={
            "student_name": student.full_name,
            "goal": student.main_objective_text,
            "weekly_food_summary": summary,
            "food_score": food_score,
            "period_start": report.period_start.isoformat() if report.period_start else None,
            "period_end": report.period_end.isoformat() if report.period_end else None,
        }
    )
    storage = current_app.extensions["storage_provider"]
    pdf_content = _build_student_report_pdf(
        title=f"Relatorio Nutricional FitCopilot - {student.full_name}",
        subtitle=f"nutrition_summary | {report.period_start.isoformat() if report.period_start else 'inicio'} a {report.period_end.isoformat() if report.period_end else 'hoje'}",
        sections=[
            ("Resumo inteligente", report.summary_text or "Relatorio gerado sem resumo disponivel."),
            ("Resumo alimentar do periodo", _format_weekly_food_summary_for_report(summary)),
            ("Score alimentar", f"{food_score['score']} ({food_score['level']}) — {food_score['reason']}"),
        ],
    )
    stored = storage.save(f"accounts/{report.account_id}/reports", f"{report.report_type}.pdf", pdf_content, "application/pdf")
    report.storage_key = stored.storage_key
    report.file_url = stored.file_url
    report.status = "completed"
    report.completed_at = utcnow()
    db.session.commit()
    return {"status": "completed", "report_id": str(report.id)}


def _format_weekly_food_summary_for_report(summary: dict) -> str:
    if not summary.get("daysWithLog"):
        return f"Nenhuma refeição registrada entre {summary.get('periodStart')} e {summary.get('periodEnd')}."
    lines = [
        f"Dias com registro: {summary['daysWithLog']}/{summary['daysInPeriod']}",
        f"Média de calorias por dia: {summary.get('avgCaloriesKcal') or '—'} kcal",
    ]
    macros = [
        f"proteína {summary['avgProteinGrams']}g" if summary.get("avgProteinGrams") else None,
        f"carboidratos {summary['avgCarbsGrams']}g" if summary.get("avgCarbsGrams") else None,
        f"gordura {summary['avgFatsGrams']}g" if summary.get("avgFatsGrams") else None,
    ]
    macros = [item for item in macros if item]
    if macros:
        lines.append("Macros médios: " + ", ".join(macros))
    if summary.get("targetAdherencePct") is not None:
        lines.append(f"Aderência à meta calórica: {summary['targetAdherencePct']}%")
    return "\n".join(lines)


def _format_workout_sessions_for_report(workout_sessions: list[dict]) -> str:
    if not workout_sessions:
        return "Nenhum treino registrado no periodo."
    lines = []
    for item in workout_sessions[:8]:
        exercises = item.get("exercises") or []
        lines.append(f"{item.get('date')} - {item.get('status')} - {len(exercises)} exercicios")
        for exercise in exercises[:5]:
            detail = " ".join(
                part
                for part in [
                    exercise.get("exercise_name"),
                    f"{exercise.get('sets_completed')} series" if exercise.get("sets_completed") else None,
                    exercise.get("reps_completed"),
                ]
                if part
            )
            if detail:
                lines.append(f"  - {detail}")
    return "\n".join(lines)


def _format_files_for_report(files: list[dict]) -> str:
    if not files:
        return "Nenhum arquivo processado usado como contexto."
    lines = []
    for item in files:
        lines.append(f"{item.get('title')} ({item.get('category')})")
        if item.get("summary"):
            lines.append(f"  {item.get('summary')}")
    return "\n".join(lines)


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_text(text: str, max_chars: int = 92) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return [""]
    words = clean.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _build_student_report_pdf(*, title: str, subtitle: str, sections: list[tuple[str, str]]) -> bytes:
    lines: list[tuple[str, int]] = [(title, 18), (subtitle, 10), ("", 10)]
    for heading, body in sections:
        lines.append((heading, 14))
        for paragraph in str(body).splitlines():
            for wrapped in _wrap_pdf_text(paragraph):
                lines.append((wrapped, 10))
        lines.append(("", 10))
    if len(lines) > 46:
        lines = lines[:45] + [("Conteudo completo armazenado no resumo do relatorio no FitCopilot.", 10)]

    page_height = 792
    margin_x = 54
    y = 740
    content_parts = ["BT", f"{margin_x} {y} Td"]
    previous_size = 10
    for line, size in lines:
        leading = 24 if size >= 14 else 15
        if size != previous_size:
            content_parts.append(f"/F1 {size} Tf")
            previous_size = size
        if line:
            content_parts.append(f"({_pdf_escape(line)}) Tj")
        content_parts.append(f"0 -{leading} Td")
        y -= leading
    content_parts.append("ET")
    content = "\n".join(content_parts).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def generate_network_monthly_report(network_account_id: str) -> bytes:
    from app.accounts.enterprise_services import get_network_dashboard
    from app.accounts.models import Account

    network = Account.query.filter_by(id=network_account_id, deleted_at=None).first()
    dashboard = get_network_dashboard(network_account_id)

    overview_body = (
        f"Unidades: {dashboard['unitsCount']}\n"
        f"Alunos na rede: {dashboard['studentsTotal']}\n"
        f"Retencao media: {dashboard['averageRetentionRate']}%"
    )
    sections = [("Visao geral da rede", overview_body)]

    if not dashboard["units"]:
        sections.append(("Unidades", "Nenhuma unidade cadastrada nesta rede no periodo."))
    else:
        for unit in dashboard["units"]:
            unit_body = (
                f"Alunos: {unit['studentsCount']}\n"
                f"Em atencao: {unit['attentionCount']}\n"
                f"Retencao: {unit['retentionRate']}%"
                + ("\nChurn acima da media da rede." if unit["churnAlert"] else "")
            )
            sections.append((unit["unitName"], unit_body))

    contract = (network.enterprise_contract_json or {}) if network else {}
    if contract:
        contract_body = "\n".join(f"{key}: {value}" for key, value in contract.items())
        sections.append(("Contrato", contract_body))

    return _build_student_report_pdf(
        title=f"Relatorio mensal — {dashboard['networkName'] or 'Rede'}",
        subtitle=utcnow().strftime("%d/%m/%Y"),
        sections=sections,
    )


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


@celery_app.task(name="generate_physical_assessment_insights_job")
def generate_physical_assessment_insights_job(assessment_id: str):
    from app.physical.services import generate_physical_assessment_insights, serialize_assessment

    assessment = generate_physical_assessment_insights(assessment_id)
    db.session.commit()
    return {"status": "completed", "assessment": serialize_assessment(assessment)}


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


@celery_app.task(name="check_pending_workout_sessions_job")
def check_pending_workout_sessions_job():
    return check_pending_workout_sessions()


@celery_app.task(name="send_end_of_day_report_job")
def send_end_of_day_report_job(student_id: str, summary_date: str | None = None):
    student = StudentProfile.query.filter_by(id=student_id).first()
    if student is None:
        return {"status": "missing"}
    target_date = date.fromisoformat(summary_date) if summary_date else date.today()
    dispatch = send_end_of_day_report(
        student=student,
        actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
        summary_date=target_date,
    )
    return {"status": "completed", "dispatch_id": str(dispatch.id), "summary_date": target_date.isoformat()}


@celery_app.task(name="send_end_of_day_reports_job")
def send_end_of_day_reports_job(summary_date: str | None = None):
    target_date = date.fromisoformat(summary_date) if summary_date else date.today()
    students = StudentProfile.query.filter_by(archived_at=None).all()
    sent = []
    skipped = []
    for student in students:
        rules = {rule.rule_type: rule for rule in get_or_create_student_automations(student)}
        daily_report = rules.get("daily_report")
        if daily_report and not daily_report.is_active:
            skipped.append({"student_id": str(student.id), "reason": "automation_disabled"})
            continue
        if not student.phone:
            skipped.append({"student_id": str(student.id), "reason": "missing_phone"})
            continue
        dispatch = send_end_of_day_report(
            student=student,
            actor_user_id=student.primary_professional.user_id if student.primary_professional else None,
            summary_date=target_date,
        )
        sent.append({"student_id": str(student.id), "dispatch_id": str(dispatch.id)})
    return {"status": "completed", "summary_date": target_date.isoformat(), "sent": sent, "skipped": skipped}


@celery_app.task(name="evaluate_nutrition_automations_job")
def evaluate_nutrition_automations_job():
    from app.accounts.models import Account

    nutri_account_ids = [row.id for row in Account.query.filter_by(professional_vertical="nutricionista").all()]
    if not nutri_account_ids:
        return {"status": "completed", "evaluated": 0, "triggered": 0}

    students = StudentProfile.query.filter(
        StudentProfile.account_id.in_(nutri_account_ids), StudentProfile.archived_at.is_(None)
    ).all()
    triggered = 0
    for student in students:
        decision = evaluate_nutrition_automation(student)
        if decision is not None:
            triggered += 1
    db.session.commit()
    return {"status": "completed", "evaluated": len(students), "triggered": triggered}


@celery_app.task(name="sync_wearable_data_job")
def sync_wearable_data_job():
    from app.operations.services import recompute_and_persist_score
    from app.wearables.alerts import evaluate_wearable_alerts
    from app.wearables.models import WearableConnection
    from app.wearables.services import sync_student_wearable_data

    connections = WearableConnection.query.filter_by(revoked_at=None).all()
    synced = 0
    alerts_triggered = 0
    for connection in connections:
        result = sync_student_wearable_data(connection)
        if result["status"] == "ok":
            synced += 1
        student = StudentProfile.query.filter_by(id=connection.student_id).first()
        if student is not None:
            recompute_and_persist_score(student)
            db.session.commit()
            if evaluate_wearable_alerts(student) is not None:
                alerts_triggered += 1
    return {"status": "completed", "connections": len(connections), "synced": synced, "alertsTriggered": alerts_triggered}


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
