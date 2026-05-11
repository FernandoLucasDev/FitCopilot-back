from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _sqlite_fallback() -> str:
    return "sqlite:///fitcopilot.db"


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _resolve_secret_path(file_path: str) -> Path:
    candidate = Path(file_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def _env_or_file(name: str, default: str | None = None) -> str | None:
    file_path = os.getenv(f"{name}_FILE")
    if file_path:
        resolved = _resolve_secret_path(file_path)
        if resolved.exists():
            return resolved.read_text(encoding="utf-8").strip()
    value = os.getenv(name)
    if value:
        return value
    return default


@dataclass
class Settings:
    SECRET_KEY: str = _env_or_file("SECRET_KEY", "dev-secret-fitcopilot-32-bytes-minimum") or "dev-secret-fitcopilot-32-bytes-minimum"
    JWT_SECRET_KEY: str = _env_or_file("JWT_SECRET_KEY", "dev-jwt-secret-fitcopilot-32-bytes-minimum") or "dev-jwt-secret-fitcopilot-32-bytes-minimum"
    SQLALCHEMY_DATABASE_URI: str = _env_or_file("DATABASE_URL", _sqlite_fallback()) or _sqlite_fallback()
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    REDIS_URL: str = _env_or_file("REDIS_URL", "redis://localhost:6379/0") or "redis://localhost:6379/0"
    STORAGE_PROVIDER: str = _env_or_file("STORAGE_PROVIDER", "local") or "local"
    STORAGE_LOCAL_ROOT: str = _env_or_file("STORAGE_LOCAL_ROOT", "storage") or "storage"
    STORAGE_PUBLIC_BASE_URL: str = _env_or_file("STORAGE_PUBLIC_BASE_URL", "http://localhost:5000") or "http://localhost:5000"
    B2_ENDPOINT: str = _env_or_file("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com") or "https://s3.us-east-005.backblazeb2.com"
    B2_REGION: str = _env_or_file("B2_REGION", "us-east-005") or "us-east-005"
    B2_BUCKET: str = _env_or_file("B2_BUCKET", "fc-dev") or "fc-dev"
    B2_KEY_ID: str | None = _env_or_file("B2_KEY_ID")
    B2_APP_KEY: str | None = _env_or_file("B2_APP_KEY")
    B2_KEY_PREFIX: str = _env_or_file("B2_KEY_PREFIX", "dev/fitcopilot/") or "dev/fitcopilot/"
    AI_PROVIDER: str = _env_or_file("AI_PROVIDER", "fake") or "fake"
    GEMINI_API_KEY: str | None = _env_or_file("GEMINI_API_KEY")
    GEMINI_MODEL_FAST: str = _env_or_file("GEMINI_MODEL_FAST", "gemini-2.0-flash") or "gemini-2.0-flash"
    GEMINI_MODEL_SMART: str = _env_or_file("GEMINI_MODEL_SMART", "gemini-2.5-flash") or "gemini-2.5-flash"
    MAX_CONTENT_LENGTH: int = int(_env_or_file("MAX_CONTENT_LENGTH", str(15 * 1024 * 1024)) or str(15 * 1024 * 1024))
    CORS_ORIGINS: list[str] = None  # type: ignore[assignment]
    CORE_API_URL: str | None = _env_or_file("CORE_API_URL")
    CORE_PROXY_MODE: str = _env_or_file("CORE_PROXY_MODE", "local") or "local"
    CORE_TIMEOUT_SECONDS: float = float(_env_or_file("CORE_TIMEOUT_SECONDS", "15") or "15")
    LOCAL_CORE_STATE_FILE: str = _env_or_file("LOCAL_CORE_STATE_FILE", "instance/local_core_state.json") or "instance/local_core_state.json"
    APP_ID: str | None = _env_or_file("APP_ID", "3")
    APP_SLUG: str = _env_or_file("APP_SLUG", "fit-copilot") or "fit-copilot"
    LOCAL_AI_API_KEY: str | None = _env_or_file("LOCAL_AI_API_KEY")
    LOCAL_AI_BASE_URL: str | None = _env_or_file("LOCAL_AI_BASE_URL")
    LOCAL_AI_MODEL_FAST: str = _env_or_file("LOCAL_AI_MODEL_FAST", "fitcopilot-fast") or "fitcopilot-fast"
    LOCAL_AI_MODEL_SMART: str = _env_or_file("LOCAL_AI_MODEL_SMART", "fitcopilot-smart") or "fitcopilot-smart"
    WHATSAPP_REQUESTED_BY_SERVICE: str = _env_or_file("WHATSAPP_REQUESTED_BY_SERVICE", "fitcopilot-backend") or "fitcopilot-backend"
    WHATSAPP_DEFAULT_COUNTRY_CODE: str = _env_or_file("WHATSAPP_DEFAULT_COUNTRY_CODE", "55") or "55"
    WHATSAPP_CHECKIN_HOUR: int = int(_env_or_file("WHATSAPP_CHECKIN_HOUR", "8") or "8")
    WHATSAPP_QUIET_HOURS_START: int = int(_env_or_file("WHATSAPP_QUIET_HOURS_START", "21") or "21")
    WHATSAPP_QUIET_HOURS_END: int = int(_env_or_file("WHATSAPP_QUIET_HOURS_END", "7") or "7")
    BOT_INTERNAL_SECRET: str = _env_or_file("BOT_INTERNAL_SECRET", "fitcopilot-bot-dev-secret") or "fitcopilot-bot-dev-secret"
    OTP_RATE_LIMIT_PER_HOUR: int = int(_env_or_file("OTP_RATE_LIMIT_PER_HOUR", "5") or "5")
    PASSWORD_RESET_RATE_LIMIT_PER_HOUR: int = int(_env_or_file("PASSWORD_RESET_RATE_LIMIT_PER_HOUR", "5") or "5")
    BOT_RATE_LIMIT_PER_MINUTE: int = int(_env_or_file("BOT_RATE_LIMIT_PER_MINUTE", "60") or "60")
    API_HOST: str = _env_or_file("API_HOST", "127.0.0.1") or "127.0.0.1"
    API_PORT: int = int(_env_or_file("API_PORT", "5050") or "5050")

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
    CORE_PROXY_MODE: str = "disabled"
    STORAGE_PROVIDER: str = "local"
    AI_PROVIDER: str = "fake"
    SECRET_KEY: str = "test-secret-fitcopilot-32-bytes-minimum"
    JWT_SECRET_KEY: str = "test-jwt-secret-fitcopilot-32-bytes-minimum"
    CORS_ORIGINS: list[str] = field(default_factory=lambda: ["http://localhost", "http://127.0.0.1"])
