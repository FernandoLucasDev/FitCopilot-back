#!/bin/sh
set -e

python -c "from app import create_app; from app.extensions import db; app=create_app(); app.app_context().push(); db.create_all()"
flask db stamp head >/dev/null 2>&1 || true

exec gunicorn "app:create_app()" \
  --bind "0.0.0.0:${API_PORT:-5050}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-120}"
