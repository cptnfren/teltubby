"""Configuration model and loader for teltubby.

Defines the `AppConfig` dataclass-like container that reads environment
variables and provides typed access across the application.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


def _get_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


@dataclass
class AppConfig:
    """Application configuration resolved from environment variables.

    Fields mirror the requirements in docs/teltubby_requirements.md §14.
    """

    # Telegram
    telegram_bot_token: str
    telegram_whitelist_ids: List[int]
    telegram_mode: str  # polling | webhook
    webhook_url: Optional[str]
    webhook_secret: Optional[str]

    # S3 / MinIO
    s3_endpoint: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_bucket: str
    s3_region: Optional[str]
    s3_force_path_style: bool
    minio_tls_skip_verify: bool

    # Ingestion
    album_aggregation_window_seconds: int
    max_file_gb: int
    bot_api_max_file_size_bytes: int

    # Dedup / DB
    sqlite_path: str
    dedup_enable: bool

    # Concurrency & I/O
    concurrency: int
    io_timeout_seconds: int
    s3_multipart_threshold_mb: int
    s3_multipart_part_size_mb: int

    # Quota & Alerts
    quota_alert_threshold_pct: int
    quota_alert_cooldown_hours: int
    bucket_quota_bytes: int | None

    # Logging & Health
    log_level: str
    log_rotate_max_bytes: int
    log_rotate_backup_count: int
    health_port: int
    bind_health_localhost_only: bool

    # RabbitMQ / Queue
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_username: str
    rabbitmq_password: str
    rabbitmq_vhost: str
    job_queue_name: str
    job_dead_letter_queue: str
    job_exchange: str
    job_dlx_exchange: str

    # MTProto / Worker
    mtproto_api_id: int | None
    mtproto_api_hash: str | None
    mtproto_phone_number: str | None
    mtproto_session_path: str | None
    worker_concurrency: int
    worker_max_retries: int
    worker_retry_delay_seconds: int

    @staticmethod
    def from_env() -> "AppConfig":
        whitelist_raw = os.getenv("TELEGRAM_WHITELIST_IDS", "").strip()
        whitelist_ids = [
            int(x) for x in whitelist_raw.split(",") if x.strip().isdigit()
        ]
        return AppConfig(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_whitelist_ids=whitelist_ids,
            telegram_mode=os.getenv("TELEGRAM_MODE", "polling"),
            webhook_url=os.getenv("WEBHOOK_URL"),
            webhook_secret=os.getenv("WEBHOOK_SECRET"),
            s3_endpoint=os.getenv("S3_ENDPOINT", ""),
            s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID", ""),
            s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY", ""),
            s3_bucket=os.getenv("S3_BUCKET", ""),
            s3_region=os.getenv("S3_REGION"),
            s3_force_path_style=_get_bool("S3_FORCE_PATH_STYLE", True),
            minio_tls_skip_verify=_get_bool("MINIO_TLS_SKIP_VERIFY", False),
            album_aggregation_window_seconds=_get_int(
                "ALBUM_AGGREGATION_WINDOW_SECONDS", 10
            ),
            max_file_gb=_get_int("MAX_FILE_GB", 4),
            bot_api_max_file_size_bytes=_get_int(
                "BOT_API_MAX_FILE_SIZE_BYTES", 50 * 1024 * 1024
            ),
            sqlite_path=os.getenv("SQLITE_PATH", "/data/teltubby.db"),
            dedup_enable=_get_bool("DEDUP_ENABLE", True),
            concurrency=max(1, min(_get_int("CONCURRENCY", 8), 32)),
            io_timeout_seconds=_get_int("IO_TIMEOUT_SECONDS", 60),
            s3_multipart_threshold_mb=_get_int("S3_MULTIPART_THRESHOLD_MB", 8),
            s3_multipart_part_size_mb=_get_int("S3_MULTIPART_PART_SIZE_MB", 16),
            quota_alert_threshold_pct=_get_int("QUOTA_ALERT_THRESHOLD_PCT", 80),
            quota_alert_cooldown_hours=_get_int("QUOTA_ALERT_COOLDOWN_HOURS", 24),
            bucket_quota_bytes=int(os.getenv("S3_BUCKET_QUOTA_BYTES", "0") or 0) or None,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_rotate_max_bytes=_get_int("LOG_ROTATE_MAX_BYTES", 5 * 1024 * 1024),
            log_rotate_backup_count=_get_int("LOG_ROTATE_BACKUP_COUNT", 10),
            health_port=_get_int("HEALTH_PORT", 8081),
            bind_health_localhost_only=_get_bool("BIND_HEALTH_LOCALHOST_ONLY", True),
            # RabbitMQ / Queue
            rabbitmq_host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
            rabbitmq_port=_get_int("RABBITMQ_PORT", 5672),
            rabbitmq_username=os.getenv("RABBITMQ_USERNAME", "guest"),
            rabbitmq_password=os.getenv("RABBITMQ_PASSWORD", "guest"),
            rabbitmq_vhost=os.getenv("RABBITMQ_VHOST", "/"),
            job_queue_name=os.getenv(
                "JOB_QUEUE_NAME", "teltubby.large_files"
            ),
            job_dead_letter_queue=os.getenv(
                "JOB_DEAD_LETTER_QUEUE", "teltubby.failed_jobs"
            ),
            job_exchange=os.getenv(
                "JOB_EXCHANGE", "teltubby.exchange"
            ),
            job_dlx_exchange=os.getenv(
                "JOB_DLX_EXCHANGE", "teltubby.dlx"
            ),
            # MTProto / Worker
            mtproto_api_id=int(os.getenv("MTPROTO_API_ID", "0") or 0) or None,
            mtproto_api_hash=os.getenv("MTPROTO_API_HASH"),
            mtproto_phone_number=os.getenv("MTPROTO_PHONE_NUMBER"),
            mtproto_session_path=os.getenv("MTPROTO_SESSION_PATH", "/data/mtproto.session"),
            worker_concurrency=_get_int("WORKER_CONCURRENCY", 1),
            worker_max_retries=_get_int("WORKER_MAX_RETRIES", 3),
            worker_retry_delay_seconds=_get_int("WORKER_RETRY_DELAY_SECONDS", 60),
        )

