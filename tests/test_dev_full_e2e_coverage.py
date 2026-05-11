from __future__ import annotations

from io import BytesIO
from pathlib import Path

from werkzeug.security import generate_password_hash

from app.accounts.models import Account, ProfessionalProfile
from app.auth.models import User
from app.extensions import db
from app.files.models import StudentFile
from app.jobs.tasks import extract_student_file_job
from app.students.models import StudentDailySignal, StudentProfile
from app.whatsapp.models import OutboundMessageDispatch


def _ok(response, status_code: int = 200):
    assert response.status_code == status_code, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    return payload["data"]


def _login(client, email: str = "owner.e2e@fitcopilot.dev") -> dict[str, str]:
    payload = _ok(client.post("/api/v1/auth/login", json={"email": email, "password": "abcd1234"}))
    return {"Authorization": f"Bearer {payload['token']}"}


def _create_academy_graph(flask_app) -> dict:
    with flask_app.app_context():
        account = Account(
            name="Academia E2E",
            slug="academia-e2e",
            email="academia.e2e@fitcopilot.dev",
            phone="+5537996620448",
            current_plan_code="ACADEMIA",
            max_students=9999,
            monthly_ai_credits=9999,
            external_org_id="academy-e2e-org",
            settings_json={"workspace_mode": "academy"},
        )
        db.session.add(account)
        db.session.flush()

        professionals: list[ProfessionalProfile] = []
        users: list[User] = []
        for index in range(5):
            role = "owner" if index == 0 else "professional"
            user = User(
                account_id=account.id,
                role=role,
                full_name=f"Personal E2E {index + 1}",
                email=("owner.e2e@fitcopilot.dev" if index == 0 else f"personal{index + 1}.e2e@fitcopilot.dev"),
                phone=f"+55379966210{index:02d}",
                password_hash=generate_password_hash("abcd1234"),
                is_active=True,
                core_access_token="local-core-token",
            )
            db.session.add(user)
            db.session.flush()
            professional = ProfessionalProfile(
                account_id=account.id,
                user_id=user.id,
                professional_type="mixed",
                onboarding_completed=True,
            )
            db.session.add(professional)
            db.session.flush()
            users.append(user)
            professionals.append(professional)

        students: list[StudentProfile] = []
        for index in range(30):
            student = StudentProfile(
                account_id=account.id,
                primary_professional_id=professionals[index % len(professionals)].id,
                full_name=f"Aluno E2E {index + 1:02d}",
                email=f"aluno{index + 1:02d}.e2e@fitcopilot.dev",
                phone="+5537996620448" if index == 0 else f"+5537996622{index + 1:03d}",
                status="ok" if index % 3 else "attention",
                adherence_score=72 - (index % 5) * 6,
                adherence_trend="down" if index % 4 == 0 else "stable",
                main_objective_text="Hipertrofia e consistência",
                notes="Criado pelo teste E2E de DEV.",
            )
            db.session.add(student)
            db.session.flush()
            students.append(student)

        db.session.commit()
        return {
            "account_id": str(account.id),
            "owner_id": str(users[0].id),
            "professional_ids": [str(item.id) for item in professionals],
            "student_ids": [str(item.id) for item in students],
            "first_student_phone": students[0].phone,
        }


def test_full_dev_e2e_academy_billing_referral_workouts_whatsapp_and_uploads(client, flask_app, monkeypatch):
    from app.files import routes as file_routes
    from app.integrations import core_messaging_client as client_module
    from app.jobs import tasks

    monkeypatch.setattr(file_routes.extract_student_file_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.send_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks.process_inbound_whatsapp_message_job, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        client_module.core_messaging_client,
        "send_interactive_message",
        lambda **kwargs: {"public_id": "core-wa-e2e", "channel_account_id": "wa-dev"},
    )
    monkeypatch.setattr(
        client_module.core_messaging_client,
        "send_text_message",
        lambda **kwargs: {"public_id": "core-wa-e2e-text", "channel_account_id": "wa-dev"},
    )

    graph = _create_academy_graph(flask_app)
    headers = _login(client)
    student_ids = graph["student_ids"]
    first_student_id = student_ids[0]

    plans = _ok(client.get("/api/v1/billing/plans", headers=headers))["items"]
    assert {item["code"] for item in plans} >= {"FREE", "PRO", "ELITE", "ACADEMIA"}
    assert any(feature["name"] == "Automações" for item in plans for feature in item["features"])

    checkout = _ok(
        client.post(
            "/api/v1/billing/checkout-session",
            headers=headers,
            json={"plan_code": "ACADEMIA", "success_url": "http://127.0.0.1:3000/billing?ok=1"},
        )
    )
    assert checkout["plan_code"] == "ACADEMIA"
    assert checkout["url"].endswith("ok=1")

    portal = _ok(client.post("/api/v1/billing/portal-session", headers=headers, json={"return_url": "http://127.0.0.1:3000/billing"}))
    assert portal["portal_url"].endswith("/billing")

    referral_stats = _ok(client.get("/api/v1/referral/stats", headers=headers))
    assert referral_stats["monthly_credit_brl"] == "25.00"
    referral_credit = _ok(client.get("/api/v1/referral/credit", headers=headers))
    assert referral_credit["capped_credit_brl"] == "25.00"
    referral_register = _ok(client.post("/api/v1/referral/register", headers=headers, json={"referral_code": "DEMO123"}))
    assert referral_register["status"] == "pending"

    students = _ok(client.get("/api/v1/students", headers=headers))["items"]
    assert len(students) == 30
    assert len(graph["professional_ids"]) == 5

    pdf_path = Path(__file__).resolve().parents[2] / "Dados e Plano Alimentar - Fernando.pdf"
    pdf_bytes = pdf_path.read_bytes()
    uploaded_ids: list[str] = []
    plan_ids: list[str] = []
    for index, student_id in enumerate(student_ids):
        upload = _ok(
            client.post(
                f"/api/v1/students/{student_id}/files",
                headers=headers,
                data={
                    "title": f"Plano alimentar E2E {index + 1}",
                    "file_category": "nutrition_evaluation",
                    "file": (BytesIO(pdf_bytes), f"plano-alimentar-e2e-{index + 1}.pdf"),
                },
                content_type="multipart/form-data",
            ),
            201,
        )["file"]
        uploaded_ids.append(upload["id"])

        created_plan = _ok(
            client.post(
                f"/api/v1/students/{student_id}/workout-plans",
                headers=headers,
                json={
                    "title": f"Ficha Base E2E {index + 1}",
                    "objective": "Força, hipertrofia e aderência",
                    "days": [
                        {
                            "label": "Treino A",
                            "order_index": 1,
                            "exercises": [
                                {"exercise_name": "Agachamento", "order_index": 1, "sets_count": 4, "reps_text": "4 x 8"},
                                {"exercise_name": "Supino reto", "order_index": 2, "sets_count": 4, "reps_text": "4 x 8"},
                            ],
                        },
                        {
                            "label": "Treino B",
                            "order_index": 2,
                            "exercises": [
                                {"exercise_name": "Remada baixa", "order_index": 1, "sets_count": 3, "reps_text": "3 x 10"},
                                {"exercise_name": "Desenvolvimento", "order_index": 2, "sets_count": 3, "reps_text": "3 x 10"},
                            ],
                        },
                    ],
                },
            ),
            201,
        )["workoutPlan"]
        plan_ids.append(created_plan["id"])
        activated = _ok(client.post(f"/api/v1/workout-plans/{created_plan['id']}/activate", headers=headers))
        assert activated["workoutPlan"]["status"] == "active"

    with flask_app.app_context():
        extract_student_file_job(uploaded_ids[0])
        first_file = db.session.get(StudentFile, uploaded_ids[0])
        assert first_file is not None
        assert first_file.extraction_status == "completed"
        assert first_file.ai_summary

    onboard = _ok(client.post(f"/api/v1/students/{first_student_id}/whatsapp/onboard", headers=headers), 202)
    checkin = _ok(client.post(f"/api/v1/students/{first_student_id}/whatsapp/send-checkin", headers=headers), 202)
    workout = _ok(client.post(f"/api/v1/students/{first_student_id}/whatsapp/send-workout", headers=headers), 202)
    assert onboard["dispatch"]["status"] == checkin["dispatch"]["status"] == workout["dispatch"]["status"] == "queued"

    with flask_app.app_context():
        dispatches = OutboundMessageDispatch.query.filter_by(student_id=first_student_id).all()
        bodies = [
            (item.payload_json.get("interactive") or {}).get("body", "")
            or (item.payload_json.get("text") or {}).get("body", "")
            for item in dispatches
        ]
        assert any("começou" in body and "👋" in body for body in bodies)
        assert any("Você vai treinar hoje? 💪" in body for body in bodies)
        assert any("São 4 exercícios" in body for body in bodies)

    bot_secret = flask_app.config["BOT_INTERNAL_SECRET"]
    image_reply = _ok(
        client.post(
            "/api/v1/internal/bot/whatsapp/respond",
            headers={"X-Bot-Secret": bot_secret},
            json={"phoneNumber": graph["first_student_phone"], "messageType": "image", "text": "", "phase": "checkin"},
        )
    )
    assert "refeição" in image_reply["replyText"]
    assert "👀" in image_reply["replyText"]

    meal_reply = _ok(
        client.post(
            "/api/v1/internal/bot/whatsapp/respond",
            headers={"X-Bot-Secret": bot_secret},
            json={
                "phoneNumber": graph["first_student_phone"],
                "messageType": "text",
                "text": "Arroz, feijão, bife grelhado, alface, tomate e batata frita",
                "phase": "checkin",
                "metadata": image_reply["metadataPatch"],
            },
        )
    )
    assert "entre 700 e 950 kcal" in meal_reply["replyText"]
    assert "Total estimado do dia até agora: entre 700 e 950 kcal." in meal_reply["replyText"]

    duplicate_reply = _ok(
        client.post(
            "/api/v1/internal/bot/whatsapp/respond",
            headers={"X-Bot-Secret": bot_secret},
            json={
                "phoneNumber": graph["first_student_phone"],
                "messageType": "text",
                "text": "Arroz, feijão, bife grelhado, alface, tomate e batata frita",
                "phase": "checkin",
            },
        )
    )
    assert "já estava registrada" in duplicate_reply["replyText"]

    portal_otp = _ok(client.post("/api/v1/student-auth/request-otp", json={"email": "aluno01.e2e@fitcopilot.dev"}), 202)
    student_token = _ok(client.post("/api/v1/student-auth/verify-otp", json={"email": "aluno01.e2e@fitcopilot.dev", "code": portal_otp["debugCode"]}))["token"]
    session = _ok(
        client.post(
            "/api/v1/student-portal/workout-sessions",
            headers={"Authorization": f"Bearer {student_token}"},
            json={
                "plan_id": plan_ids[0],
                "status": "completed",
                "notes": "Treino concluído no fluxo E2E.",
                "exercises": [{"exercise_name": "Agachamento", "sets_completed": 4, "reps_completed": "4 x 8", "notes": "Carga boa"}],
            },
        ),
        201,
    )
    assert session["session"]["status"] == "completed"

    with flask_app.app_context():
        assert StudentProfile.query.filter_by(account_id=graph["account_id"]).count() == 30
        assert ProfessionalProfile.query.filter_by(account_id=graph["account_id"]).count() == 5
        assert StudentFile.query.filter_by(account_id=graph["account_id"]).count() == 30
        assert StudentDailySignal.query.filter_by(student_id=first_student_id, signal_type="meal").count() == 1
