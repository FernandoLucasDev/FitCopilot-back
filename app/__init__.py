from __future__ import annotations

import mimetypes
from io import BytesIO
from pathlib import Path

from celery.schedules import crontab
from dotenv import load_dotenv
from flask import Flask, send_file

from app.common.api import register_error_handlers
from app.config import Settings, TestSettings
from app.extensions import celery_app, cors, db, jwt, migrate


load_dotenv()


_sentry_initialized = False


def init_sentry(settings: Settings) -> None:
    global _sentry_initialized
    if _sentry_initialized or not settings.SENTRY_DSN:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=settings.SENTRY_SEND_DEFAULT_PII,
    )
    _sentry_initialized = True


def create_app(config_object: type[Settings] | None = None) -> Flask:
    settings = config_object() if config_object else Settings()
    init_sentry(settings)

    app = Flask(__name__)
    app.config.from_mapping(settings.__dict__)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": settings.CORS_ORIGINS}})

    if settings.STORAGE_PROVIDER == "b2":
        from app.common.storage.b2 import B2StorageProvider

        storage = B2StorageProvider(
            endpoint_url=settings.B2_ENDPOINT,
            region_name=settings.B2_REGION,
            bucket_name=settings.B2_BUCKET,
            key_id=settings.B2_KEY_ID or "",
            app_key=settings.B2_APP_KEY or "",
            public_base_url=settings.STORAGE_PUBLIC_BASE_URL,
            key_prefix=settings.B2_KEY_PREFIX,
        )
    else:
        from app.common.storage.local import LocalStorageProvider

        storage = LocalStorageProvider(settings.STORAGE_LOCAL_ROOT, settings.STORAGE_PUBLIC_BASE_URL)

    if settings.AI_PROVIDER == "gemini":
        from app.ai.gemini_provider import GeminiAIProvider

        ai_provider = GeminiAIProvider(
            api_key=settings.GEMINI_API_KEY,
            fast_model=settings.GEMINI_MODEL_FAST,
            smart_model=settings.GEMINI_MODEL_SMART,
        )
    else:
        from app.ai.fake_provider import FakeAIProvider

        ai_provider = FakeAIProvider()

    if settings.WEARABLE_PROVIDER == "strava":
        from app.wearables.providers.strava_provider import StravaWearableProvider

        wearable_provider = StravaWearableProvider(
            client_id=settings.STRAVA_CLIENT_ID,
            client_secret=settings.STRAVA_CLIENT_SECRET,
            redirect_uri=settings.STRAVA_REDIRECT_URI,
        )
    else:
        from app.wearables.providers.fake_provider import FakeWearableProvider

        wearable_provider = FakeWearableProvider(callback_url=settings.STRAVA_REDIRECT_URI or "")

    app.extensions["storage_provider"] = storage
    app.extensions["ai_provider"] = ai_provider
    app.extensions["wearable_provider"] = wearable_provider

    configure_celery(app)
    register_error_handlers(app)
    register_blueprints(app)

    @app.get("/api/v1/system/storage/<path:storage_key>")
    def serve_local_storage(storage_key: str):
        content = storage.open_bytes(storage_key)
        mimetype = mimetypes.guess_type(storage_key)[0] or "application/octet-stream"
        filename = Path(storage_key).name
        return send_file(BytesIO(content), mimetype=mimetype, download_name=filename, as_attachment=False)

    return app


def create_test_app() -> Flask:
    return create_app(TestSettings)


def register_blueprints(app: Flask) -> None:
    from app.accounts.routes import accounts_bp
    from app.auth.routes import auth_bp
    from app.files.routes import files_bp
    from app.billing.routes import billing_bp
    from app.ai.routes import ai_bp
    from app.integrations.academy.routes import academy_bp
    from app.insights.routes import insights_bp
    from app.messaging.routes import messaging_bp
    from app.nutrition.routes import nutrition_bp
    from app.overview.routes import overview_bp
    from app.orgs.routes import orgs_bp
    from app.physical.routes import physical_bp
    from app.reports.routes import reports_bp
    from app.students.routes import students_bp
    from app.students.portal_routes import student_portal_bp
    from app.system.routes import system_bp
    from app.whatsapp.routes import whatsapp_bp
    from app.workouts.routes import workouts_bp
    from app.referral.routes import referral_bp
    from app.wearables.routes import wearables_bp

    for blueprint in [
        accounts_bp,
        auth_bp,
        overview_bp,
        orgs_bp,
        students_bp,
        student_portal_bp,
        files_bp,
        billing_bp,
        workouts_bp,
        insights_bp,
        messaging_bp,
        nutrition_bp,
        physical_bp,
        reports_bp,
        whatsapp_bp,
        ai_bp,
        academy_bp,
        wearables_bp,
        system_bp,
        referral_bp,
    ]:
        app.register_blueprint(blueprint, url_prefix="/api/v1")


def configure_celery(app: Flask) -> None:
    celery_app.conf.update(
        broker_url=app.config["REDIS_URL"],
        result_backend=app.config["REDIS_URL"],
        task_default_queue=app.config.get("CELERY_TASK_DEFAULT_QUEUE", "fitcopilot"),
        task_ignore_result=False,
        timezone=app.config.get("APP_TIMEZONE", "America/Sao_Paulo"),
        beat_schedule={
            "fitcopilot-send-end-of-day-reports": {
                "task": "send_end_of_day_reports_job",
                "schedule": crontab(hour=app.config.get("WHATSAPP_DAILY_REPORT_HOUR", 20), minute=0),
            },
            "fitcopilot-check-pending-workout-sessions": {
                "task": "check_pending_workout_sessions_job",
                "schedule": 600.0,
            },
            "fitcopilot-evaluate-nutrition-automations": {
                "task": "evaluate_nutrition_automations_job",
                "schedule": crontab(hour=app.config.get("WHATSAPP_DAILY_REPORT_HOUR", 20), minute=15),
            },
            "fitcopilot-sync-wearable-data": {
                "task": "sync_wearable_data_job",
                "schedule": crontab(hour=5, minute=0),
            },
        },
    )

    class FlaskTask(celery_app.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app.Task = FlaskTask
