from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.getenv(name, default).split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    project_id: str
    service_name: str
    environment: str
    database_url: str
    is_dev_mode: bool
    secret_key: str
    admin_id: str
    admin_password: str
    kakao_rest_api_key: str
    kakao_client_secret: str
    kakao_token_url: str
    kakao_userinfo_url: str
    otp_secret_key: str
    otp_timeout_second: int
    otp_delta: int
    otp_qr_hash_algorithm: str
    otp_manual_hash_algorithm: str
    otp_manual_code_length: int
    otp_manual_code_registry_backend: str
    otp_manual_code_collection: str
    otp_manual_code_max_attempts: int
    firestore_project_id: str
    firestore_database: str
    cors_allowed_origins: list[str]
    display_timezone_label: str
    app_timezone: str
    message_bus_backend: str
    visit_event_topic: str
    reservation_queue_backend: str
    reservation_gateway_secret: str
    reservation_worker_secret: str
    reservation_worker_url: str
    cloud_tasks_project_id: str
    cloud_tasks_location: str
    cloud_tasks_queue: str
    cloud_tasks_service_account_email: str
    cloud_tasks_audience: str


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
    environment = os.getenv("APP_ENV", "local")
    return Settings(
        project_id=os.getenv("GCP_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", "reservation-io")),
        service_name=os.getenv("SERVICE_NAME", "reservation-api"),
        environment=environment,
        database_url=os.getenv("DATABASE_URL", "sqlite:///./.local/reservation_api.db"),
        is_dev_mode=_bool_env("IS_DEV_MODE", False),
        secret_key=secret_key,
        admin_id=os.getenv("ADMIN_ID", "admin"),
        admin_password=os.getenv("ADMIN_PASSWORD", "admin"),
        kakao_rest_api_key=os.getenv("KAKAO_REST_API_KEY", ""),
        kakao_client_secret=os.getenv("KAKAO_CLIENT_SECRET", ""),
        kakao_token_url=os.getenv("KAKAO_TOKEN_URL", "https://kauth.kakao.com/oauth/token"),
        kakao_userinfo_url=os.getenv("KAKAO_USERINFO_URL", "https://kapi.kakao.com/v2/user/me"),
        otp_secret_key=os.getenv("OTP_SECRET_KEY", secret_key),
        otp_timeout_second=max(5, _int_env("OTP_TIMEOUT_SECOND", 30)),
        otp_delta=max(0, _int_env("OTP_DELTA", 1)),
        otp_qr_hash_algorithm=os.getenv("OTP_QR_HASH_ALGORITHM", "md5"),
        otp_manual_hash_algorithm=os.getenv("OTP_MANUAL_HASH_ALGORITHM", "sha1"),
        otp_manual_code_length=max(6, _int_env("OTP_MANUAL_CODE_LENGTH", 12)),
        otp_manual_code_registry_backend=os.getenv(
            "OTP_MANUAL_CODE_REGISTRY_BACKEND",
            "memory" if environment == "local" else "firestore",
        ),
        otp_manual_code_collection=os.getenv("OTP_MANUAL_CODE_COLLECTION", "manual_otp_codes"),
        otp_manual_code_max_attempts=max(1, _int_env("OTP_MANUAL_CODE_MAX_ATTEMPTS", 16)),
        firestore_project_id=os.getenv("FIRESTORE_PROJECT_ID", os.getenv("GCP_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))),
        firestore_database=os.getenv("FIRESTORE_DATABASE", "(default)"),
        cors_allowed_origins=_csv_env("CORS_ALLOWED_ORIGINS", "http://localhost:3000"),
        display_timezone_label=os.getenv("DISPLAY_TIMEZONE_LABEL", "Seoul, KR"),
        app_timezone=os.getenv("APP_TIMEZONE", "Asia/Seoul"),
        message_bus_backend=os.getenv("MESSAGE_BUS_BACKEND", "none"),
        visit_event_topic=os.getenv("VISIT_EVENT_TOPIC", ""),
        reservation_queue_backend=os.getenv(
            "RESERVATION_QUEUE_BACKEND",
            "inline" if environment == "local" else "cloud_tasks",
        ).strip().lower(),
        reservation_gateway_secret=os.getenv("RESERVATION_GATEWAY_SECRET", ""),
        reservation_worker_secret=os.getenv("RESERVATION_WORKER_SECRET", ""),
        reservation_worker_url=os.getenv("RESERVATION_WORKER_URL", ""),
        cloud_tasks_project_id=os.getenv("CLOUD_TASKS_PROJECT_ID", os.getenv("GCP_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", ""))),
        cloud_tasks_location=os.getenv("CLOUD_TASKS_LOCATION", os.getenv("REGION", "asia-northeast3")),
        cloud_tasks_queue=os.getenv("CLOUD_TASKS_QUEUE", "reservation-requests"),
        cloud_tasks_service_account_email=os.getenv("CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL", ""),
        cloud_tasks_audience=os.getenv("CLOUD_TASKS_AUDIENCE", ""),
    )
