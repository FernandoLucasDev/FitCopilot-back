from app import create_app
from app.config import Settings

app = create_app()
settings = Settings()


if __name__ == "__main__":
    app.run(host=settings.API_HOST, port=settings.API_PORT, debug=False, use_reloader=False)
