from __future__ import annotations

import os
from dataclasses import dataclass, field


def _sqlite_fallback() -> str:
    return "sqlite:///fitcopilot.db"


@dataclass
class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-fitcopilot-32-bytes-minimum")
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-fitcopilot-32-bytes-minimum")
    SQLALCHEMY_DATABASE_URI: str = os.getenv("DATABASE_URL", _sqlite_fallback())
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    STORAGE_PROVIDER: str = os.getenv("STORAGE_PROVIDER", "local")
    STORAGE_LOCAL_ROOT: str = os.getenv("STORAGE_LOCAL_ROOT", "storage")
    STORAGE_PUBLIC_BASE_URL: str = os.getenv("STORAGE_PUBLIC_BASE_URL", "http://localhost:5000")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "fake")
    MAX_CONTENT_LENGTH: int = int(os.getenv("MAX_CONTENT_LENGTH", str(15 * 1024 * 1024)))
    CORS_ORIGINS: list[str] = None  # type: ignore[assignment]
    CORE_API_URL: str | None = os.getenv("CORE_API_URL")
    CORE_TIMEOUT_SECONDS: float = float(os.getenv("CORE_TIMEOUT_SECONDS", "15"))
    APP_ID: str | None = os.getenv("APP_ID", "3")
    APP_SLUG: str = os.getenv("APP_SLUG", "fit-copilot")
    LOCAL_AI_API_KEY: str | None = os.getenv("LOCAL_AI_API_KEY")
    LOCAL_AI_BASE_URL: str | None = os.getenv("LOCAL_AI_BASE_URL")
    LOCAL_AI_MODEL_FAST: str = os.getenv("LOCAL_AI_MODEL_FAST", "fitcopilot-fast")
    LOCAL_AI_MODEL_SMART: str = os.getenv("LOCAL_AI_MODEL_SMART", "fitcopilot-smart")
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
    API_PORT: int = int(os.getenv("API_PORT", "5050"))

    def __post_init__(self) -> None:
        origins = os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:4173,http://127.0.0.1:4173",
        )
        self.CORS_ORIGINS = [item.strip() for item in origins.split(",") if item.strip()]


@dataclass
class TestSettings(Settings):
    SQLALCHEMY_DATABASE_URI: str = "sqlite+pysqlite:///:memory:"
    TESTING: bool = True
    CORS_ORIGINS: list[str] = field(default_factory=lambda: ["http://localhost", "http://127.0.0.1"])
