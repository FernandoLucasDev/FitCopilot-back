from app import create_app
from app.extensions import celery_app


flask_app = create_app()
celery_app.flask_app = flask_app

import app.jobs.tasks  # noqa: E402,F401

