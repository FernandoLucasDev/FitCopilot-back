from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, send_from_directory

from app.ai.fake_provider import FakeAIProvider
from app.common.api import register_error_handlers
from app.common.storage.local import LocalStorageProvider
from app.config import Settings, TestSettings
from app.extensions import celery_app, cors, db, jwt, migrate


load_dotenv()


def create_app(config_object: type[Settings] | None = None) -> Flask:
    app = Flask(__name__)
    settings = config_object() if config_object else Settings()
    app.config.from_mapping(settings.__dict__)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": settings.CORS_ORIGINS}})

    storage = LocalStorageProvider(settings.STORAGE_LOCAL_ROOT, settings.STORAGE_PUBLIC_BASE_URL)
    ai_provider = FakeAIProvider()

    app.extensions["storage_provider"] = storage
    app.extensions["ai_provider"] = ai_provider

    configure_celery(app)
    register_error_handlers(app)
    register_blueprints(app)

    @app.get("/api/v1/system/storage/<path:storage_key>")
    def serve_local_storage(storage_key: str):
        root = Path(settings.STORAGE_LOCAL_ROOT)
        file_path = root / storage_key
        return send_from_directory(file_path.parent, file_path.name, as_attachment=False)

    return app


def create_test_app() -> Flask:
    return create_app(TestSettings)


def register_blueprints(app: Flask) -> None:
    from app.auth.routes import auth_bp
    from app.files.routes import files_bp
    from app.billing.routes import billing_bp
    from app.ai.routes import ai_bp
    from app.insights.routes import insights_bp
    from app.messaging.routes import messaging_bp
    from app.overview.routes import overview_bp
    from app.reports.routes import reports_bp
    from app.students.routes import students_bp
    from app.students.portal_routes import student_portal_bp
    from app.system.routes import system_bp
    from app.workouts.routes import workouts_bp

    for blueprint in [
        auth_bp,
        overview_bp,
        students_bp,
        student_portal_bp,
        files_bp,
        billing_bp,
        workouts_bp,
        insights_bp,
        messaging_bp,
        reports_bp,
        ai_bp,
        system_bp,
    ]:
        app.register_blueprint(blueprint, url_prefix="/api/v1")


def configure_celery(app: Flask) -> None:
    celery_app.conf.update(
        broker_url=app.config["REDIS_URL"],
        result_backend=app.config["REDIS_URL"],
        task_ignore_result=False,
    )

    class FlaskTask(celery_app.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app.Task = FlaskTask
