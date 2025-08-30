# env.local.ps1 — Local developer configuration for docker compose
#
# NOTE:
# - This file is dot-sourced by run.ps1 to populate $env:* variables used by
#   docker compose. Edit values below to match your environment.

# --- Telegram ---
$env:TELEGRAM_BOT_TOKEN       = "<bot_token>"          # [string]
$env:TELEGRAM_WHITELIST_IDS   = "123456789"           # [string] comma-separated
$env:TELEGRAM_MODE            = "polling"              # [string] polling|webhook
$env:WEBHOOK_URL              = ""                      # [string] when webhook
$env:WEBHOOK_SECRET           = ""                      # [string] optional

# --- MinIO / S3 ---
$env:S3_ENDPOINT              = "https://minio.example.com"   # [string]
$env:S3_ACCESS_KEY_ID         = "minio"
$env:S3_SECRET_ACCESS_KEY     = "minio123"
$env:S3_BUCKET                = "archives"
$env:S3_REGION                = ""                     # [string|null]
$env:S3_FORCE_PATH_STYLE      = "true"                 # [bool-string]
$env:MINIO_TLS_SKIP_VERIFY    = "true"                 # [bool-string] only for self-signed dev

# --- Ingestion ---
$env:ALBUM_AGGREGATION_WINDOW_SECONDS = "10"          # [int-string]
$env:MAX_FILE_GB                      = "4"           # [int-string]
$env:BOT_API_MAX_FILE_SIZE_BYTES      = "52428800"    # [int-string] 50MB

# --- Dedup / DB ---
$env:SQLITE_PATH              = "/data/teltubby.db"
$env:DEDUP_ENABLE            = "true"

# --- Concurrency & I/O ---
$env:CONCURRENCY             = "8"
$env:IO_TIMEOUT_SECONDS      = "60"
$env:S3_MULTIPART_THRESHOLD_MB = "8"
$env:S3_MULTIPART_PART_SIZE_MB = "16"

# --- Quota & Alerts ---
$env:QUOTA_ALERT_THRESHOLD_PCT  = "80"
$env:QUOTA_ALERT_COOLDOWN_HOURS = "24"
# Optional explicit bucket quota in bytes to simulate capacity locally (e.g., 10485760 = 10MB)
$env:S3_BUCKET_QUOTA_BYTES      = ""  # leave empty to disable

# --- Logging & Health ---
$env:LOG_LEVEL                 = "INFO"
$env:LOG_ROTATE_MAX_BYTES      = "5242880"   # 5MB
$env:LOG_ROTATE_BACKUP_COUNT   = "10"
$env:HEALTH_PORT               = "8081"
$env:BIND_HEALTH_LOCALHOST_ONLY = "false"
