from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.students.models import StudentDailySignal
from app.ai.bot_orchestrator import reply_for_whatsapp


def test_register_sets_vertical_from_professional_type(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "account_name": "Clinica Nutri",
            "account_email": "nutri@fitcopilot.dev",
            "full_name": "Ana Nutri",
            "email": "ana@fitcopilot.dev",
            "password": "abcd1234",
            "professional_type": "nutritionist",
        },
    )
    assert response.status_code == 201
    payload = response.get_json()["data"]
    assert payload["account"]["professionalVertical"] == "nutricionista"


def test_register_defaults_vertical_to_personal_trainer(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "account_name": "Estudio PT",
            "account_email": "pt@fitcopilot.dev",
            "full_name": "Carlos PT",
            "email": "carlos@fitcopilot.dev",
            "password": "abcd1234",
            "professional_type": "personal_trainer",
        },
    )
    assert response.status_code == 201
    payload = response.get_json()["data"]
    assert payload["account"]["professionalVertical"] == "personal_trainer"


def test_owner_can_update_account_vertical(client, seeded_data, auth_headers):
    response = client.patch(
        "/api/v1/account",
        json={"professional_vertical": "nutricionista"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["account"]["professionalVertical"] == "nutricionista"

    me_response = client.get("/api/v1/auth/me", headers=auth_headers)
    assert me_response.get_json()["data"]["account"]["professionalVertical"] == "nutricionista"


def test_update_account_vertical_rejects_invalid_value(client, seeded_data, auth_headers):
    response = client.patch(
        "/api/v1/account",
        json={"professional_vertical": "invalido"},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_patch_student_saves_daily_calorie_target(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    response = client.patch(
        f"/api/v1/students/{student.id}",
        json={"daily_calorie_target": 1800},
        headers=auth_headers,
    )
    assert response.status_code == 200
    panel = response.get_json()["data"]["student"]
    assert panel["data"]["dailyCalorieTarget"] == 1800
    assert panel["today"]["metrics"]["calorieTargetKcal"] == 1800


def test_panel_calorie_progress_reflects_meals_against_target(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    student.daily_calorie_target = 1000
    db.session.add(
        StudentDailySignal(
            account_id=student.account_id,
            student_id=student.id,
            signal_date=date.today(),
            signal_type="meal",
            source="whatsapp",
            title="Refeicao registrada pelo WhatsApp",
            body="arroz frango e salada",
            payload_json={"estimated_calories": 500},
            created_at=datetime.now(timezone.utc),
        )
    )
    db.session.commit()

    response = client.get(f"/api/v1/students/{student.id}/panel", headers=auth_headers)
    assert response.status_code == 200
    metrics = response.get_json()["data"]["today"]["metrics"]
    assert metrics["calorieTargetKcal"] == 1000
    assert metrics["caloriePct"] == 50


def test_whatsapp_meal_reply_includes_calorie_target_progress(client, seeded_data):
    student = seeded_data["student"]
    student.daily_calorie_target = 1000
    db.session.commit()

    with client.application.app_context():
        reply = reply_for_whatsapp(
            phone_number=student.phone,
            text="2 ovos mexidos",
            message_type="text",
            state_phase="checkin",
        )

    assert reply.handled is True
    assert "meta de 1000 kcal" in reply.reply_text
    assert "16%" in reply.reply_text


def test_nutrition_automation_rules_seeded_for_nutricionista_account(seeded_data):
    from app.whatsapp.services import get_or_create_student_automations

    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    rule_types = {rule.rule_type for rule in get_or_create_student_automations(student)}
    assert "nutrition_no_log_2d" in rule_types
    assert "nutrition_over_target_3d" in rule_types


def test_nutrition_automation_rules_backfilled_when_vertical_changes_after_rules_exist(seeded_data):
    from app.whatsapp.services import get_or_create_student_automations

    student = seeded_data["student"]
    # Conta comeca como personal_trainer (default) e ja cria as 4 rules base.
    initial_rule_types = {rule.rule_type for rule in get_or_create_student_automations(student)}
    assert "nutrition_no_log_2d" not in initial_rule_types
    assert len(initial_rule_types) == 4

    # Depois muda para nutricionista - as rules de nutricao devem ser criadas (backfill),
    # sem duplicar as 4 rules base ja existentes.
    student.account.professional_vertical = "nutricionista"
    db.session.commit()
    updated_rules = get_or_create_student_automations(student)
    updated_rule_types = {rule.rule_type for rule in updated_rules}
    assert "nutrition_no_log_2d" in updated_rule_types
    assert "nutrition_over_target_3d" in updated_rule_types
    assert len(updated_rules) == 6
    assert len({rule.id for rule in updated_rules}) == 6


def test_nutrition_automation_rules_not_seeded_for_personal_trainer_account(seeded_data):
    from app.whatsapp.services import get_or_create_student_automations

    student = seeded_data["student"]
    rule_types = {rule.rule_type for rule in get_or_create_student_automations(student)}
    assert "nutrition_no_log_2d" not in rule_types
    assert "nutrition_over_target_3d" not in rule_types


def test_evaluate_nutrition_automation_triggers_on_no_log_2d(seeded_data):
    from app.nutrition.services import evaluate_nutrition_automation

    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    decision = evaluate_nutrition_automation(student)
    db.session.commit()
    assert decision is not None
    assert decision.rule_type == "nutrition_no_log_2d"


def test_evaluate_nutrition_automation_respects_cooldown(seeded_data):
    from app.nutrition.services import evaluate_nutrition_automation

    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    first = evaluate_nutrition_automation(student)
    db.session.commit()
    second = evaluate_nutrition_automation(student)
    db.session.commit()
    assert first is not None
    assert second is not None
    assert first.id == second.id


def test_disabled_nutrition_rule_does_not_trigger(client, seeded_data, auth_headers):
    from app.nutrition.services import evaluate_nutrition_automation

    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    response = client.patch(
        f"/api/v1/students/{student.id}/whatsapp/automations",
        json={"nutrition_no_log_active": False},
        headers=auth_headers,
    )
    assert response.status_code == 200

    decision = evaluate_nutrition_automation(student)
    db.session.commit()
    assert decision is None


def test_weekly_summary_empty_state(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    response = client.get(f"/api/v1/students/{student.id}/nutrition/weekly-summary", headers=auth_headers)
    assert response.status_code == 200
    summary = response.get_json()["data"]["summary"]
    assert summary["daysWithLog"] == 0
    assert summary["avgCaloriesKcal"] is None


def test_weekly_summary_aggregates_meals(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    student.daily_calorie_target = 1000
    db.session.add(
        StudentDailySignal(
            account_id=student.account_id,
            student_id=student.id,
            signal_date=date.today(),
            signal_type="meal",
            source="whatsapp",
            title="Refeicao registrada pelo WhatsApp",
            body="arroz frango e salada",
            payload_json={"estimated_calories": 600, "protein_grams": 40, "carbs_grams": 60, "fats_grams": 15},
            created_at=datetime.now(timezone.utc),
        )
    )
    db.session.commit()

    response = client.get(f"/api/v1/students/{student.id}/nutrition/weekly-summary", headers=auth_headers)
    assert response.status_code == 200
    summary = response.get_json()["data"]["summary"]
    assert summary["daysWithLog"] == 1
    assert summary["avgCaloriesKcal"] == 600
    assert summary["targetAdherencePct"] == 100


def test_calculate_food_score_scenarios(seeded_data):
    from app.nutrition.services import calculate_food_score

    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"

    absence_score = calculate_food_score(student)
    assert absence_score.level in {"risk", "cooling"}

    student.daily_calorie_target = 1000
    for offset in range(7):
        db.session.add(
            StudentDailySignal(
                account_id=student.account_id,
                student_id=student.id,
                signal_date=date.today() - timedelta(days=offset),
                signal_type="meal",
                source="whatsapp",
                title="Refeicao registrada pelo WhatsApp",
                body=f"refeicao consistente dia {offset}",
                payload_json={"estimated_calories": 900, "protein_grams": 40, "carbs_grams": 80, "fats_grams": 20},
                created_at=datetime.now(timezone.utc) - timedelta(days=offset),
            )
        )
    db.session.commit()
    consistent_score = calculate_food_score(student)
    assert consistent_score.level == "ok"
    assert consistent_score.score > absence_score.score

    StudentDailySignal.query.filter_by(student_id=student.id, signal_type="meal").delete()
    db.session.commit()
    for offset in range(7):
        db.session.add(
            StudentDailySignal(
                account_id=student.account_id,
                student_id=student.id,
                signal_date=date.today() - timedelta(days=offset),
                signal_type="meal",
                source="whatsapp",
                title="Refeicao registrada pelo WhatsApp",
                body=f"refeicao excesso dia {offset}",
                payload_json={"estimated_calories": 1900, "protein_grams": 40, "carbs_grams": 200, "fats_grams": 90},
                created_at=datetime.now(timezone.utc) - timedelta(days=offset),
            )
        )
    db.session.commit()
    over_target_score = calculate_food_score(student)
    assert over_target_score.score < consistent_score.score
    assert over_target_score.level in {"attention", "cooling", "risk"}


def test_fake_provider_analyze_meal_returns_items_and_confidence():
    from app.ai.fake_provider import FakeAIProvider

    provider = FakeAIProvider()
    result = provider.analyze_meal(context={"meal_description": "arroz feijao bife e batata frita"})
    assert result.confidence is not None
    assert result.items and result.items[0]["calories"] == result.estimated_calories


def test_media_safety_saves_meal_photo_backup_for_safe_food(client, flask_app, seeded_data):
    import base64

    from app.ai.base import MediaSafetyResult
    from app.files.models import StudentFile

    class Provider:
        def moderate_media(self, *, content: bytes, mime_type: str, context: dict):
            return MediaSafetyResult(allowed=True, category="safe_food", severity="allow", user_message="", confidence=0.9)

    flask_app.extensions["ai_provider"] = Provider()
    student = seeded_data["student"]
    encoded = base64.b64encode(b"fake-image-bytes").decode("ascii")

    response = client.post(
        "/api/v1/internal/bot/whatsapp/media-safety",
        headers={"X-Bot-Secret": flask_app.config["BOT_INTERNAL_SECRET"], "Content-Type": "application/json"},
        json={
            "phoneNumber": student.phone,
            "messageType": "image",
            "media": {"base64": encoded, "mimeType": "image/jpeg"},
        },
    )
    assert response.status_code == 200

    photo = StudentFile.query.filter_by(student_id=student.id, file_category="meal_photo").first()
    assert photo is not None
    assert photo.mime_type == "image/jpeg"


def test_media_safety_does_not_save_photo_when_blocked(client, flask_app, seeded_data):
    import base64

    from app.ai.base import MediaSafetyResult
    from app.files.models import StudentFile

    class Provider:
        def moderate_media(self, *, content: bytes, mime_type: str, context: dict):
            return MediaSafetyResult(allowed=False, category="non_relevant", severity="block", user_message="x", confidence=0.9)

    flask_app.extensions["ai_provider"] = Provider()
    student = seeded_data["student"]
    encoded = base64.b64encode(b"fake-image-bytes").decode("ascii")

    client.post(
        "/api/v1/internal/bot/whatsapp/media-safety",
        headers={"X-Bot-Secret": flask_app.config["BOT_INTERNAL_SECRET"], "Content-Type": "application/json"},
        json={
            "phoneNumber": student.phone,
            "messageType": "image",
            "media": {"base64": encoded, "mimeType": "image/jpeg"},
        },
    )

    assert StudentFile.query.filter_by(student_id=student.id, file_category="meal_photo").count() == 0


def test_meal_reply_links_recent_photo_backup(client, flask_app, seeded_data):
    from app.files.services import save_meal_photo

    student = seeded_data["student"]
    with client.application.app_context():
        photo = save_meal_photo(student=student, content=b"fake-bytes", mime_type="image/jpeg")
        db.session.commit()

        reply = reply_for_whatsapp(
            phone_number=student.phone,
            text="comi omelete com queijo",
            message_type="text",
            state_phase="checkin",
        )
        assert reply.handled is True

        signal = (
            StudentDailySignal.query.filter_by(student_id=student.id, signal_type="meal")
            .order_by(StudentDailySignal.created_at.desc())
            .first()
        )
        assert signal.payload_json.get("photo_file_id") == str(photo.id)
        assert signal.payload_json.get("photo_url") == photo.file_url


def test_nutrition_report_generation_with_and_without_data(client, seeded_data, auth_headers, monkeypatch):
    from app.jobs.tasks import generate_student_report_job
    from app.reports import routes as report_routes
    from app.reports.models import GeneratedReport

    monkeypatch.setattr(report_routes.generate_student_report_job, "delay", lambda *args, **kwargs: None)
    student = seeded_data["student"]

    empty_report = client.post(
        f"/api/v1/students/{student.id}/reports",
        headers=auth_headers,
        json={"report_type": "nutrition_summary"},
    ).get_json()["data"]["report"]

    with client.application.app_context():
        generate_student_report_job(empty_report["id"])
        generated = db.session.get(GeneratedReport, empty_report["id"])
        assert generated.status == "completed"
        assert generated.file_url

    db.session.add(
        StudentDailySignal(
            account_id=student.account_id,
            student_id=student.id,
            signal_date=date.today(),
            signal_type="meal",
            source="whatsapp",
            title="Refeicao registrada pelo WhatsApp",
            body="salada com frango",
            payload_json={"estimated_calories": 450, "protein_grams": 35, "carbs_grams": 30, "fats_grams": 12},
            created_at=datetime.now(timezone.utc),
        )
    )
    db.session.commit()

    filled_report = client.post(
        f"/api/v1/students/{student.id}/reports",
        headers=auth_headers,
        json={"report_type": "nutrition_summary"},
    ).get_json()["data"]["report"]

    with client.application.app_context():
        generate_student_report_job(filled_report["id"])
        generated = db.session.get(GeneratedReport, filled_report["id"])
        assert generated.status == "completed"
        assert generated.file_url


def test_format_weekly_food_summary_empty_state_message():
    from app.jobs.tasks import _format_weekly_food_summary_for_report

    text = _format_weekly_food_summary_for_report(
        {"daysWithLog": 0, "periodStart": "2026-01-01", "periodEnd": "2026-01-07", "daysInPeriod": 7}
    )
    assert "Nenhuma refeição registrada" in text


def test_panel_includes_nutrition_only_for_nutricionista_vertical(client, seeded_data, auth_headers):
    student = seeded_data["student"]

    response = client.get(f"/api/v1/students/{student.id}/panel", headers=auth_headers)
    assert response.get_json()["data"]["nutrition"] is None

    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    response = client.get(f"/api/v1/students/{student.id}/panel", headers=auth_headers)
    payload = response.get_json()["data"]["nutrition"]
    assert payload is not None
    assert "weeklySummary" in payload
    assert "foodScore" in payload
    assert payload["plan"] is None
    assert payload["plans"] == []


def _create_nutrition_plan_payload(title="Plano de emagrecimento"):
    return {
        "title": title,
        "objective": "Emagrecimento",
        "meals": [
            {
                "label": "Café da manhã",
                "order_index": 1,
                "items": [
                    {"order_index": 1, "food_name": "Ovos mexidos", "quantity_text": "2 unidades", "calories": 160, "protein_grams": 13},
                    {"order_index": 2, "food_name": "Pão integral", "quantity_text": "1 fatia", "calories": 80, "carbs_grams": 15},
                ],
            },
            {
                "label": "Almoço",
                "order_index": 2,
                "items": [
                    {"order_index": 1, "food_name": "Arroz integral", "quantity_text": "100g", "calories": 130, "carbs_grams": 28},
                    {"order_index": 2, "food_name": "Frango grelhado", "quantity_text": "150g", "calories": 250, "protein_grams": 40},
                ],
            },
        ],
    }


def test_create_and_assign_nutrition_plan(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    created = client.post(
        f"/api/v1/students/{student.id}/nutrition-plans",
        json=_create_nutrition_plan_payload(),
        headers=auth_headers,
    )
    assert created.status_code == 201
    plan = created.get_json()["data"]["nutritionPlan"]
    assert plan["status"] == "draft"
    assert plan["totals"]["calories"] == 620
    assert len(plan["meals"]) == 2
    assert plan["meals"][0]["totals"]["calories"] == 240

    assign = client.post(
        f"/api/v1/students/{student.id}/assign-nutrition-plan",
        json={"plan_id": plan["id"]},
        headers=auth_headers,
    )
    assert assign.status_code == 201
    assert assign.get_json()["data"]["studentNutritionPlan"]["active"] is True

    active = client.get(f"/api/v1/students/{student.id}/nutrition-plan", headers=auth_headers)
    assert active.get_json()["data"]["nutritionPlan"]["id"] == plan["id"]
    assert active.get_json()["data"]["nutritionPlan"]["status"] == "active"


def test_assigning_second_plan_deactivates_first(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    first = client.post(
        f"/api/v1/students/{student.id}/nutrition-plans",
        json=_create_nutrition_plan_payload("Plano A"),
        headers=auth_headers,
    ).get_json()["data"]["nutritionPlan"]
    client.post(f"/api/v1/students/{student.id}/assign-nutrition-plan", json={"plan_id": first["id"]}, headers=auth_headers)

    second = client.post(
        f"/api/v1/students/{student.id}/nutrition-plans",
        json=_create_nutrition_plan_payload("Plano B"),
        headers=auth_headers,
    ).get_json()["data"]["nutritionPlan"]
    client.post(f"/api/v1/students/{student.id}/assign-nutrition-plan", json={"plan_id": second["id"]}, headers=auth_headers)

    items = client.get(f"/api/v1/students/{student.id}/nutrition-plans", headers=auth_headers).get_json()["data"]["items"]
    by_id = {item["id"]: item for item in items}
    assert by_id[first["id"]]["status"] == "draft"
    assert by_id[second["id"]]["status"] == "active"

    active = client.get(f"/api/v1/students/{student.id}/nutrition-plan", headers=auth_headers)
    assert active.get_json()["data"]["nutritionPlan"]["id"] == second["id"]


def test_archive_nutrition_plan(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    plan = client.post(
        f"/api/v1/students/{student.id}/nutrition-plans",
        json=_create_nutrition_plan_payload(),
        headers=auth_headers,
    ).get_json()["data"]["nutritionPlan"]
    client.post(f"/api/v1/students/{student.id}/assign-nutrition-plan", json={"plan_id": plan["id"]}, headers=auth_headers)

    archived = client.post(f"/api/v1/nutrition-plans/{plan['id']}/archive", headers=auth_headers)
    assert archived.get_json()["data"]["nutritionPlan"]["status"] == "archived"

    active = client.get(f"/api/v1/students/{student.id}/nutrition-plan", headers=auth_headers)
    assert active.get_json()["data"]["nutritionPlan"] is None


def test_panel_and_portal_expose_active_nutrition_plan(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    plan = client.post(
        f"/api/v1/students/{student.id}/nutrition-plans",
        json=_create_nutrition_plan_payload(),
        headers=auth_headers,
    ).get_json()["data"]["nutritionPlan"]
    client.post(f"/api/v1/students/{student.id}/assign-nutrition-plan", json={"plan_id": plan["id"]}, headers=auth_headers)

    panel = client.get(f"/api/v1/students/{student.id}/panel", headers=auth_headers).get_json()["data"]
    assert panel["nutrition"]["plan"]["id"] == plan["id"]
    assert len(panel["nutrition"]["plans"]) == 1

    from app.students.portal_services import build_student_portal_payload

    with client.application.app_context():
        from app.extensions import db as _db

        refreshed = _db.session.get(type(student), student.id)
        portal_payload = build_student_portal_payload(refreshed)
        assert portal_payload["nutritionPlan"]["id"] == plan["id"]


def test_send_nutrition_plan_requires_active_plan(client, seeded_data, auth_headers):
    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    response = client.post(f"/api/v1/students/{student.id}/whatsapp/send-nutrition-plan", headers=auth_headers)
    assert response.status_code == 409


def test_send_nutrition_plan_queues_dispatch(client, seeded_data, auth_headers, monkeypatch):
    from app.jobs import tasks

    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)

    student = seeded_data["student"]
    student.account.professional_vertical = "nutricionista"
    db.session.commit()

    plan = client.post(
        f"/api/v1/students/{student.id}/nutrition-plans",
        json=_create_nutrition_plan_payload(),
        headers=auth_headers,
    ).get_json()["data"]["nutritionPlan"]
    client.post(f"/api/v1/students/{student.id}/assign-nutrition-plan", json={"plan_id": plan["id"]}, headers=auth_headers)

    response = client.post(f"/api/v1/students/{student.id}/whatsapp/send-nutrition-plan", headers=auth_headers)
    assert response.status_code == 202
    assert response.get_json()["data"]["dispatch"]["status"] == "queued"
